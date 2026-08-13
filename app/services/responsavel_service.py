"""Consentimento do responsável legal por paciente menor de 18 (LGPD art. 14).

Dois caminhos, conforme a decisão de produto:

**A — o menor se cadastra.** O cadastro nasce ``PENDENTE_RESPONSAVEL``: ele não
agenda, não escolhe profissional, não paga. Um convite vai ao responsável por
WhatsApp e e-mail; ele envia cópia do documento e confirma.

**B — o responsável se cadastra** e declara que quer cadastrar um menor. O
consentimento é dado no ato, por quem tem legitimidade, e o cadastro do menor
já nasce ativo.

Uma ressalva que vale registrar: no caminho A a plataforma guarda dados de um
menor **antes** de ter o consentimento. Para reduzir isso ao mínimo defensável,
enquanto pendente coletamos só o essencial, nada é processado, e o worker
**apaga** o cadastro que não for confirmado dentro do prazo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.cripto import cifrar, decifrar
from app.core.erros import ErroDominio, NaoEncontrado
from app.core.logging import get_logger
from app.core.seguranca import gerar_hash_senha, gerar_token_opaco, hash_token
from app.core.tempo import agora_utc
from app.models import (
    CanalNotificacao,
    Papel,
    PerfilPaciente,
    StatusPaciente,
    TipoDocumentoResponsavel,
    TipoTermo,
    Usuario,
    VerificacaoResponsavel,
)
from app.providers.base import StorageProvider
from app.services.notificacao_service import NotificacaoService
from app.services.termos_service import TermosService

log = get_logger(__name__)

#: Prazo para o responsável confirmar. Vencido, o cadastro do menor é apagado —
#: manter dado de menor sem consentimento não se justifica.
PRAZO_CONFIRMACAO_DIAS = 7

TAMANHO_MAXIMO_DOCUMENTO = 8 * 1024 * 1024
TIPOS_ACEITOS = {"image/jpeg", "image/png", "image/webp", "application/pdf"}

TEMPLATE_CONVITE = "convite_responsavel"

IDADE_MAIORIDADE = 18
#: Abaixo disso a plataforma não atende: psicoterapia infantil tem exigências
#: próprias (setting, técnica, formação) que a Fase 1 não contempla.
IDADE_MINIMA_ATENDIMENTO = 12


class ResponsavelInvalido(ErroDominio):
    codigo = "responsavel_invalido"
    mensagem_padrao = "Informe os dados do responsável legal."


class DocumentoInvalido(ErroDominio):
    codigo = "documento_invalido"
    mensagem_padrao = "Envie uma foto ou PDF do documento, de até 8 MB."


class ConviteInvalido(ErroDominio):
    codigo = "convite_invalido"
    status_http = 410
    mensagem_padrao = "Este link de confirmação é inválido ou já expirou."


class IdadeNaoAtendida(ErroDominio):
    codigo = "idade_nao_atendida"
    mensagem_padrao = (
        "No momento a plataforma atende a partir de 12 anos. "
        "Para crianças menores, procure um profissional de psicologia infantil."
    )


class CadastroPendente(ErroDominio):
    codigo = "cadastro_pendente_responsavel"
    mensagem_padrao = (
        "Seu cadastro está aguardando a confirmação do responsável legal. "
        "Assim que ele confirmar, você poderá marcar sua primeira sessão."
    )


@dataclass(frozen=True, slots=True)
class DadosResponsavel:
    nome: str
    email: str | None = None
    telefone_e164: str | None = None
    parentesco: str | None = None


@dataclass(frozen=True, slots=True)
class DadosMenor:
    nome_completo: str
    data_nascimento: date
    email: str
    senha: str
    telefone_e164: str | None = None


def idade_em(nascimento: date) -> int:
    hoje = agora_utc().date()
    return (
        hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))
    )


def eh_menor(nascimento: date) -> bool:
    return idade_em(nascimento) < IDADE_MAIORIDADE


class ResponsavelService:
    def __init__(
        self,
        sessao: AsyncSession,
        settings: Settings,
        armazenamento: StorageProvider | None = None,
    ) -> None:
        self.sessao = sessao
        self.settings = settings
        self.armazenamento = armazenamento

    # --- Caminho A: o menor se cadastra ------------------------------------

    async def abrir_pendencia(
        self, paciente: PerfilPaciente, dados: DadosResponsavel
    ) -> tuple[VerificacaoResponsavel, str]:
        """Marca o cadastro como pendente e gera o convite.

        Devolve ``(verificacao, token_em_claro)`` — o token vai no link e **não**
        é persistido; o banco guarda só o hash.
        """
        if not dados.nome.strip():
            raise ResponsavelInvalido(campo="responsavel_nome")
        if not (dados.email or dados.telefone_e164):
            raise ResponsavelInvalido(
                "Informe ao menos um contato do responsável: e-mail ou WhatsApp.",
                campo="responsavel_contato",
            )

        paciente.status = StatusPaciente.PENDENTE_RESPONSAVEL
        paciente.responsavel_legal_nome = dados.nome.strip()

        # Invalida convites anteriores: só um link vale por vez.
        # Consulta explícita em vez de `paciente.verificacoes`: o relacionamento
        # pode não estar carregado, e o lazy load dispararia I/O fora do
        # contexto async (MissingGreenlet).
        anteriores = (
            await self.sessao.execute(
                select(VerificacaoResponsavel).where(
                    VerificacaoResponsavel.paciente_id == paciente.usuario_id,
                    VerificacaoResponsavel.confirmado_em.is_(None),
                    VerificacaoResponsavel.recusado_em.is_(None),
                )
            )
        ).scalars()
        for antiga in anteriores:
            antiga.recusado_em = agora_utc()
            antiga.motivo_recusa = "substituída por novo convite"

        token = gerar_token_opaco()
        verificacao = VerificacaoResponsavel(
            paciente_id=paciente.usuario_id,
            responsavel_nome=dados.nome.strip(),
            responsavel_email=(dados.email or "").strip().lower() or None,
            responsavel_telefone_e164=dados.telefone_e164,
            parentesco=dados.parentesco,
            token_hash=hash_token(token),
            expira_em=agora_utc() + timedelta(days=PRAZO_CONFIRMACAO_DIAS),
        )
        self.sessao.add(verificacao)
        await self.sessao.flush()

        await self._enviar_convite(paciente, verificacao, token)
        log.info(
            "responsavel.pendencia_aberta",
            paciente_id=str(paciente.usuario_id),
            expira_em=verificacao.expira_em.isoformat(),
        )
        return verificacao, token

    async def _usuario_do(self, paciente: PerfilPaciente) -> Usuario:
        usuario = await self.sessao.get(Usuario, paciente.usuario_id)
        if usuario is None:
            raise NaoEncontrado("Conta não encontrada.")
        return usuario

    async def _enviar_convite(
        self, paciente: PerfilPaciente, verificacao: VerificacaoResponsavel, token: str
    ) -> None:
        """Enfileira o convite nos canais informados.

        O contexto carrega apenas o primeiro nome do menor — quem recebe pode
        não ser o responsável, e o corpo da mensagem passa por servidores que
        não controlamos.
        """
        notificacoes = NotificacaoService(self.sessao)
        usuario = await self._usuario_do(paciente)
        contexto = {
            "responsavel_nome": verificacao.responsavel_nome,
            "menor_primeiro_nome": usuario.primeiro_nome,
            "link": f"{self.settings.app_base_url}/responsavel/confirmar/{token}",
            "prazo_dias": PRAZO_CONFIRMACAO_DIAS,
        }

        destinos = [
            (CanalNotificacao.EMAIL, verificacao.responsavel_email),
            (CanalNotificacao.WHATSAPP, verificacao.responsavel_telefone_e164),
        ]
        for canal, destino in destinos:
            if not destino:
                continue
            # A notificação fica LIGADA ao menor (é ele que existe como
            # usuário), mas o DESTINO é o contato do responsável. Sem esse
            # destino explícito, o convite iria para o e-mail do próprio
            # adolescente, que então poderia se autoautorizar.
            await notificacoes.enfileirar(
                usuario=usuario,
                canal=canal,
                template=TEMPLATE_CONVITE,
                contexto=contexto,
                chave_idempotencia=f"convite-resp:{verificacao.id}:{canal.value}",
                destino=destino,
            )
        verificacao.enviado_em = agora_utc()
        verificacao.tentativas_envio += 1

    async def buscar_por_token(self, token: str) -> VerificacaoResponsavel:
        verificacao = await self.sessao.scalar(
            select(VerificacaoResponsavel).where(
                VerificacaoResponsavel.token_hash == hash_token(token)
            )
        )
        if verificacao is None or not verificacao.pendente:
            raise ConviteInvalido()
        if verificacao.expira_em < agora_utc():
            raise ConviteInvalido(
                "O prazo para confirmar este cadastro venceu. "
                "Peça ao adolescente para iniciar o cadastro novamente."
            )
        return verificacao

    async def confirmar(
        self,
        token: str,
        *,
        documento: bytes,
        documento_tipo: TipoDocumentoResponsavel,
        content_type: str,
        documento_numero: str,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> PerfilPaciente:
        """O responsável envia o documento e confirma. O cadastro é liberado."""
        verificacao = await self.buscar_por_token(token)

        if not documento or len(documento) > TAMANHO_MAXIMO_DOCUMENTO:
            raise DocumentoInvalido()
        if content_type not in TIPOS_ACEITOS:
            raise DocumentoInvalido()

        if self.armazenamento is None:
            raise ErroDominio("Armazenamento não configurado.")

        agora = agora_utc()
        verificacao.documento_tipo = documento_tipo
        # Cifra ANTES de subir: o arquivo chega ao storage já ilegível, então
        # nem quem opera o MinIO consegue abrir o documento. O AAD amarra o
        # blob a esta verificação — movê-lo para outra linha faz a decifragem
        # falhar em vez de mostrar o documento de outra pessoa.
        chave_objeto = f"documentos/responsavel/{verificacao.id}.enc"
        await self.armazenamento.salvar(
            chave_objeto,
            cifrar(
                documento,
                chave_base64=self.settings.chave_cripto_transcricao,
                contexto=f"verificacao_responsavel:{verificacao.id}",
            ),
            "application/octet-stream",
        )
        verificacao.documento_chave = chave_objeto
        verificacao.documento_content_type = content_type
        verificacao.documento_final = "".join(c for c in documento_numero if c.isalnum())[-4:]
        verificacao.confirmado_em = agora
        verificacao.confirmado_ip = ip
        verificacao.confirmado_user_agent = (user_agent or "")[:500] or None

        paciente = await self.sessao.get(PerfilPaciente, verificacao.paciente_id)
        if paciente is None:
            raise NaoEncontrado("Cadastro não encontrado.")
        paciente.status = StatusPaciente.ATIVO
        paciente.responsavel_confirmado_em = agora
        paciente.responsavel_legal_nome = verificacao.responsavel_nome

        # Registra o consentimento com o hash do texto que o responsável leu.
        await TermosService(self.sessao).registrar_aceite(
            await self._usuario_do(paciente),
            (TipoTermo.CONSENT_RESPONSAVEL,),
            ip=ip,
            user_agent=user_agent,
        )
        await self.sessao.flush()

        log.info(
            "responsavel.confirmado",
            paciente_id=str(paciente.usuario_id),
            verificacao_id=str(verificacao.id),
        )
        return paciente

    # --- Caminho B: o responsável cadastra o menor --------------------------

    async def cadastrar_menor(
        self,
        responsavel: Usuario,
        dados: DadosMenor,
        *,
        parentesco: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> PerfilPaciente:
        """O responsável cria a conta do menor, consentindo no ato.

        Aqui não há pendência: quem tem legitimidade para consentir já está
        autenticado e declarou o vínculo.
        """
        idade = idade_em(dados.data_nascimento)
        if idade >= IDADE_MAIORIDADE:
            raise ResponsavelInvalido(
                "Esta pessoa é maior de idade e deve criar a própria conta.",
                campo="data_nascimento",
            )
        if idade < IDADE_MINIMA_ATENDIMENTO:
            raise IdadeNaoAtendida(campo="data_nascimento")

        usuario = Usuario(
            email=dados.email.strip().lower(),
            senha_hash=gerar_hash_senha(dados.senha),
            papel=Papel.PACIENTE,
            nome_completo=dados.nome_completo.strip(),
            telefone_e164=dados.telefone_e164,
            timezone=responsavel.timezone,
        )
        self.sessao.add(usuario)
        try:
            await self.sessao.flush()
        except IntegrityError as exc:
            await self.sessao.rollback()
            from app.core.erros import EmailJaCadastrado

            raise EmailJaCadastrado(campo="email") from exc

        agora = agora_utc()
        paciente = PerfilPaciente(
            usuario_id=usuario.id,
            data_nascimento=dados.data_nascimento,
            status=StatusPaciente.ATIVO,
            responsavel_usuario_id=responsavel.id,
            responsavel_legal_nome=responsavel.nome_completo,
            responsavel_confirmado_em=agora,
        )
        self.sessao.add(paciente)
        await self.sessao.flush()

        verificacao = VerificacaoResponsavel(
            paciente_id=paciente.usuario_id,
            responsavel_nome=responsavel.nome_completo,
            responsavel_email=responsavel.email,
            responsavel_telefone_e164=responsavel.telefone_e164,
            responsavel_usuario_id=responsavel.id,
            parentesco=parentesco,
            # Sem convite: o consentimento foi dado presencialmente na sessão
            # autenticada do responsável. O token existe só por consistência
            # do modelo e nasce já usado.
            token_hash=hash_token(gerar_token_opaco()),
            expira_em=agora,
            confirmado_em=agora,
            confirmado_ip=ip,
            confirmado_user_agent=(user_agent or "")[:500] or None,
        )
        self.sessao.add(verificacao)

        await TermosService(self.sessao).registrar_aceite(
            responsavel,
            (TipoTermo.CONSENT_RESPONSAVEL,),
            ip=ip,
            user_agent=user_agent,
        )
        await self.sessao.flush()

        log.info(
            "responsavel.menor_cadastrado",
            responsavel_id=str(responsavel.id),
            paciente_id=str(paciente.usuario_id),
        )
        return paciente

    async def ler_documento(self, verificacao: VerificacaoResponsavel) -> bytes:
        """Decifra o documento para exibição.

        **Não** existe link direto para este arquivo: quem chama é a rota
        autorizada, que já checou se o solicitante é o dono ou um admin com
        permissão no módulo `documentos`, e registrou o acesso.
        """
        if self.armazenamento is None:
            raise ErroDominio("Armazenamento não configurado.")
        if not verificacao.documento_chave:
            raise NaoEncontrado("O documento já foi removido após a verificação.")

        blob = await self.armazenamento.ler(verificacao.documento_chave)
        return decifrar(
            blob,
            chave_base64=self.settings.chave_cripto_transcricao,
            contexto=f"verificacao_responsavel:{verificacao.id}",
        )

    # --- Higiene de dados ---------------------------------------------------

    async def remover_documentos_verificados(self, apos_dias: int = 30) -> int:
        """Apaga a imagem do documento depois de verificada.

        Uma cópia de RG é dado sensível por si só. Depois de cumprir sua função
        — provar que o consentimento veio de quem tinha legitimidade — mantê-la
        só aumenta o estrago de um eventual vazamento. Fica o registro de que
        houve verificação, com tipo e últimos dígitos.
        """
        corte = agora_utc() - timedelta(days=apos_dias)
        pendentes = list(
            (
                await self.sessao.execute(
                    select(VerificacaoResponsavel).where(
                        VerificacaoResponsavel.confirmado_em.is_not(None),
                        VerificacaoResponsavel.confirmado_em < corte,
                        VerificacaoResponsavel.documento_chave.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for verificacao in pendentes:
            if self.armazenamento is not None and verificacao.documento_chave:
                await self.armazenamento.remover(verificacao.documento_chave)
            verificacao.documento_chave = None
            verificacao.documento_removido_em = agora_utc()

        await self.sessao.flush()
        if pendentes:
            log.info("responsavel.documentos_removidos", quantidade=len(pendentes))
        return len(pendentes)

    async def expirar_pendencias(self) -> list[uuid.UUID]:
        """Apaga cadastros de menores não confirmados no prazo.

        Não é limpeza cosmética: sem o consentimento do art. 14 não há base
        legal para manter o dado, então o certo é remover, não arquivar.
        """
        vencidas = list(
            (
                await self.sessao.execute(
                    select(VerificacaoResponsavel).where(
                        VerificacaoResponsavel.confirmado_em.is_(None),
                        VerificacaoResponsavel.recusado_em.is_(None),
                        VerificacaoResponsavel.expira_em < agora_utc(),
                    )
                )
            )
            .scalars()
            .all()
        )

        removidos: list[uuid.UUID] = []
        for verificacao in vencidas:
            paciente = await self.sessao.get(PerfilPaciente, verificacao.paciente_id)
            if paciente is None or paciente.status is not StatusPaciente.PENDENTE_RESPONSAVEL:
                verificacao.recusado_em = agora_utc()
                verificacao.motivo_recusa = "prazo vencido"
                continue

            usuario = await self.sessao.get(Usuario, paciente.usuario_id)
            if usuario is None:
                continue
            removidos.append(paciente.usuario_id)
            # CASCADE em usuarios leva perfil, verificações e sessões de login.
            await self.sessao.delete(usuario)

        await self.sessao.flush()
        if removidos:
            log.info("responsavel.pendencias_expiradas", quantidade=len(removidos))
        return removidos
