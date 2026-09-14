"""Cadastro, login e sessão.

Este service não conhece ``Request``, ``Response`` nem cookie: recebe dados
simples e devolve o token da sessão. Quem grava o cookie é a rota.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import (
    CredenciaisInvalidas,
    EmailJaCadastrado,
    ErroDominio,
    NaoAutorizado,
)
from app.core.logging import get_logger
from app.core.seguranca import (
    gerar_hash_senha,
    gerar_token_opaco,
    hash_token,
    validar_forca_senha,
    verificar_senha,
)
from app.core.sessao_web import DURACAO_SESSAO
from app.core.tempo import agora_utc
from app.models import (
    OrigemSessaoLogin,
    Papel,
    PerfilPaciente,
    SessaoLogin,
    StatusPaciente,
    Usuario,
)

log = get_logger(__name__)

IDADE_MAIORIDADE = 18
#: Abaixo disto a plataforma não atende.
IDADE_MINIMA_ATENDIMENTO = 12


class SenhaFraca(ErroDominio):
    codigo = "senha_fraca"
    mensagem_padrao = "Escolha uma senha mais forte."


class IdadeNaoAtendida(ErroDominio):
    codigo = "idade_nao_atendida"
    mensagem_padrao = (
        "No momento a plataforma atende a partir de 12 anos. "
        "Para crianças menores, procure um profissional de psicologia infantil."
    )


@dataclass(frozen=True, slots=True)
class DadosCadastro:
    nome_completo: str
    email: str
    senha: str
    telefone: str | None = None
    data_nascimento: date | None = None
    timezone: str = "America/Sao_Paulo"


@dataclass(frozen=True, slots=True)
class ContextoRequisicao:
    """IP e user-agent, para a trilha de auditoria exigida pela LGPD."""

    ip: str | None = None
    user_agent: str | None = None


class AuthService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    # --- Cadastro ----------------------------------------------------------

    async def cadastrar(
        self, dados: DadosCadastro, papel: Papel, contexto: ContextoRequisicao
    ) -> Usuario:
        if papel is Papel.ADMIN:
            # Admin nunca é auto-atribuível: só por seed ou por outro admin.
            raise NaoAutorizado("Não é possível criar uma conta de administrador.")

        if (erro := validar_forca_senha(dados.senha)) is not None:
            raise SenhaFraca(erro, campo="senha")

        # Menor de 18 pode se cadastrar, mas a conta nasce PENDENTE e não
        # agenda nada até o responsável autorizar (LGPD art. 14). Abaixo de 12
        # a plataforma não atende: psicoterapia infantil exige setting e
        # formação que este produto não contempla.
        eh_menor_de_idade = False
        if papel is Papel.PACIENTE and dados.data_nascimento is not None:
            idade = _idade_em_anos(dados.data_nascimento)
            if idade < IDADE_MINIMA_ATENDIMENTO:
                raise IdadeNaoAtendida(campo="data_nascimento")
            eh_menor_de_idade = idade < IDADE_MAIORIDADE

        usuario = Usuario(
            email=dados.email.strip().lower(),
            senha_hash=gerar_hash_senha(dados.senha),
            papel=papel,
            nome_completo=dados.nome_completo.strip(),
            telefone_e164=dados.telefone,
            timezone=dados.timezone,
        )
        self.sessao.add(usuario)
        try:
            await self.sessao.flush()
        except IntegrityError as exc:
            await self.sessao.rollback()
            raise EmailJaCadastrado(campo="email") from exc

        if papel is Papel.PACIENTE:
            self.sessao.add(
                PerfilPaciente(
                    usuario_id=usuario.id,
                    data_nascimento=dados.data_nascimento,
                    status=(
                        StatusPaciente.PENDENTE_RESPONSAVEL
                        if eh_menor_de_idade
                        else StatusPaciente.ATIVO
                    ),
                )
            )
            await self.sessao.flush()

        log.info("auth.cadastro", usuario_id=str(usuario.id), papel=papel.value)
        return usuario

    # --- Login -------------------------------------------------------------

    async def autenticar(
        self, email: str, senha: str, contexto: ContextoRequisicao
    ) -> tuple[Usuario, str]:
        """Devolve ``(usuario, token_da_sessao)``.

        A mensagem de erro é a mesma para e-mail inexistente e senha errada:
        distinguir permitiria enumerar quem tem conta na plataforma -- que numa
        plataforma de psicologia já é, por si só, informação sensível.
        """
        usuario = await self.sessao.scalar(
            select(Usuario).where(Usuario.email == email.strip().lower())
        )

        if usuario is None:
            # Gasta o mesmo tempo de um hash real, para não vazar por timing.
            gerar_hash_senha(senha)
            raise CredenciaisInvalidas()

        ok, novo_hash = verificar_senha(senha, usuario.senha_hash)
        if not ok:
            log.info("auth.senha_invalida", usuario_id=str(usuario.id))
            raise CredenciaisInvalidas()

        if not usuario.ativo:
            raise CredenciaisInvalidas("Esta conta está desativada.")

        # Rehash transparente quando os parâmetros de custo mudam.
        if novo_hash:
            usuario.senha_hash = novo_hash

        usuario.ultimo_login_em = agora_utc()
        token = await self.criar_sessao(usuario, contexto)
        log.info("auth.login", usuario_id=str(usuario.id), papel=usuario.papel.value)
        return usuario, token

    # --- Sessão ------------------------------------------------------------

    async def criar_sessao(
        self,
        usuario: Usuario,
        contexto: ContextoRequisicao,
        origem: OrigemSessaoLogin = OrigemSessaoLogin.WEB,
    ) -> str:
        token = gerar_token_opaco()
        agora = agora_utc()
        self.sessao.add(
            SessaoLogin(
                usuario_id=usuario.id,
                token_hash=hash_token(token),
                origem=origem,
                criada_em=agora,
                expira_em=agora + DURACAO_SESSAO,
                ultimo_acesso_em=agora,
                ip=contexto.ip,
                user_agent=(contexto.user_agent or "")[:500] or None,
            )
        )
        await self.sessao.flush()
        return token

    async def resolver_sessao(self, token: str) -> Usuario | None:
        """Valida o token e devolve o usuário, renovando o acesso deslizante."""
        agora = agora_utc()
        registro = await self.sessao.scalar(
            select(SessaoLogin).where(
                SessaoLogin.token_hash == hash_token(token),
                SessaoLogin.revogada_em.is_(None),
                SessaoLogin.expira_em > agora,
            )
        )
        if registro is None:
            return None

        # Renovação deslizante: quem usa o sistema não é deslogado no meio.
        registro.ultimo_acesso_em = agora
        registro.expira_em = agora + DURACAO_SESSAO
        return registro.usuario

    async def encerrar_sessao(self, token: str) -> None:
        await self.sessao.execute(
            update(SessaoLogin)
            .where(SessaoLogin.token_hash == hash_token(token))
            .values(revogada_em=agora_utc())
        )

    async def revogar_todas(self, usuario_id: uuid.UUID) -> int:
        """Encerra todas as sessões do usuário.

        É o que faz um pedido de "sair de todos os dispositivos", uma suspensão
        de conta ou uma resposta a incidente funcionarem de imediato -- o que um
        JWT stateless não permite (ver ADR 0002).
        """
        resultado = await self.sessao.execute(
            update(SessaoLogin)
            .where(SessaoLogin.usuario_id == usuario_id, SessaoLogin.revogada_em.is_(None))
            .values(revogada_em=agora_utc())
        )
        return int(getattr(resultado, "rowcount", 0) or 0)


def _idade_em_anos(nascimento: date) -> int:
    hoje = agora_utc().date()
    return (
        hoje.year - nascimento.year - ((hoje.month, hoje.day) < (nascimento.month, nascimento.day))
    )
