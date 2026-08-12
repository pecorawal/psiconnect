"""Sessão de atendimento: sala, disclaimer, lobby e admissão.

Todo marco vira uma linha em ``EventoSessao`` (append-only). É dela que saem
pontualidade (R10), tolerância de 15 minutos (R7) e no-show — derivar isso de
campos mutáveis seria frágil e não auditável.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import (
    ConsentimentoNecessario,
    NaoAutorizado,
    NaoEncontrado,
    SessaoNaoDisponivel,
)
from app.core.logging import get_logger
from app.core.seguranca import gerar_chave_acesso
from app.core.tempo import agora_utc
from app.models import (
    Agendamento,
    EventoPontuacao,
    EventoSessao,
    Papel,
    Sessao,
    StatusAgendamento,
    StatusSessao,
    TipoEventoSessao,
    TipoPontuacao,
    TipoTermo,
    Usuario,
)
from app.providers.base import Participante, VideoProvider
from app.services.parametros_service import ParametrosService
from app.services.termos_service import TermosService

log = get_logger(__name__)

PONTOS_PONTUALIDADE = 10
PONTOS_SESSAO_REALIZADA = 5


@dataclass(frozen=True, slots=True)
class EstadoLobby:
    sessao: Sessao
    profissional_presente: bool
    paciente_admitido: bool
    pode_entrar: bool
    mensagem: str


class SessaoService:
    def __init__(
        self,
        sessao_db: AsyncSession,
        parametros: ParametrosService,
        video: VideoProvider,
    ) -> None:
        self.db = sessao_db
        self.parametros = parametros
        self.video = video

    # --- Preparação ---------------------------------------------------------

    async def preparar_sala(self, agendamento: Agendamento) -> Sessao:
        """Cria a sala de vídeo. Chamado pelo worker em T-20min (R8).

        Idempotente: se a sala já existe, devolve a sessão como está.
        """
        existente = await self.db.scalar(
            select(Sessao).where(Sessao.agendamento_id == agendamento.id)
        )
        if existente is not None and existente.sala_url:
            return existente

        sessao = existente or Sessao(agendamento_id=agendamento.id, provedor_video=self.video.nome)
        nome_sala = f"psi-{agendamento.id.hex[:16]}"
        # A sala sobrevive ao fim previsto: sessão que estende não cai no meio.
        expira = agendamento.fim_utc + timedelta(minutes=30)

        sala = await self.video.criar_sala(nome_sala, expira, privada=True)
        sessao.sala_nome = sala.nome
        sessao.sala_url = sala.url
        sessao.sala_expira_em = sala.expira_em
        sessao.status = StatusSessao.SALA_PRONTA

        # As "chaves de acesso aleatórias" do requisito original: exibidas uma
        # vez, guardadas só como hash.
        _, hash_paciente = gerar_chave_acesso()
        _, hash_profissional = gerar_chave_acesso()
        sessao.chave_acesso_paciente_hash = hash_paciente
        sessao.chave_acesso_profissional_hash = hash_profissional

        if existente is None:
            self.db.add(sessao)
        await self.db.flush()

        await self._registrar_evento(sessao, TipoEventoSessao.SALA_CRIADA, None)
        log.info("sessao.sala_pronta", sessao_id=str(sessao.id))
        return sessao

    async def buscar_por_agendamento(self, agendamento_id: uuid.UUID, usuario: Usuario) -> Sessao:
        sessao = await self.db.scalar(select(Sessao).where(Sessao.agendamento_id == agendamento_id))
        if sessao is None:
            raise SessaoNaoDisponivel("A sala ainda não foi preparada.")
        await self._autorizar(sessao, usuario)
        return sessao

    async def _autorizar(self, sessao: Sessao, usuario: Usuario) -> None:
        """Só paciente e profissional daquela sessão entram.

        O admin **não** entra: acesso administrativo a uma consulta psicológica
        em andamento não tem justificativa.
        """
        agendamento = sessao.agendamento
        if usuario.id not in (agendamento.paciente_id, agendamento.profissional_id):
            raise NaoEncontrado("Sessão não encontrada.")

    # --- Disclaimer e lobby -------------------------------------------------

    async def registrar_consentimento(
        self,
        sessao: Sessao,
        usuario: Usuario,
        *,
        aceitou: bool,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Sem o checkbox marcado, não há entrada.

        O aceite é gravado com o hash do texto exato exibido, ligado a esta
        sessão específica — não é um consentimento global.
        """
        if not aceitou:
            raise ConsentimentoNecessario()

        await TermosService(self.db).registrar_aceite(
            usuario,
            (TipoTermo.CONSENT_TRANSCRICAO,),
            ip=ip,
            user_agent=user_agent,
            sessao_id=sessao.id,
        )
        sessao.transcricao_consentida = True
        await self.db.flush()
        await self._registrar_evento(sessao, TipoEventoSessao.DISCLAIMER_ACEITO, usuario.id)

    async def entrar_no_lobby(self, sessao: Sessao, usuario: Usuario) -> EstadoLobby:
        agora = agora_utc()
        agendamento = sessao.agendamento
        eh_profissional = usuario.id == agendamento.profissional_id

        if sessao.status is StatusSessao.AGENDADA:
            raise SessaoNaoDisponivel("A sala abre 20 minutos antes do horário.")

        if eh_profissional:
            if sessao.profissional_entrou_em is None:
                sessao.profissional_entrou_em = agora
                await self._registrar_evento(sessao, TipoEventoSessao.ENTROU_SALA, usuario.id)
                await self._pontuar_pontualidade(sessao, agendamento, agora)
            if sessao.status in (StatusSessao.SALA_PRONTA, StatusSessao.LOBBY):
                sessao.status = StatusSessao.EM_ANDAMENTO
                sessao.iniciada_em = sessao.iniciada_em or agora
        else:
            if not sessao.transcricao_consentida:
                raise ConsentimentoNecessario()
            if sessao.paciente_entrou_em is None:
                sessao.paciente_entrou_em = agora
                await self._registrar_evento(sessao, TipoEventoSessao.ENTROU_LOBBY, usuario.id)
            if sessao.status is StatusSessao.SALA_PRONTA:
                sessao.status = StatusSessao.LOBBY

        await self.db.flush()
        return await self.estado_lobby(sessao, usuario)

    async def estado_lobby(self, sessao: Sessao, usuario: Usuario) -> EstadoLobby:
        """Consultado em polling pelo lobby do paciente."""
        eh_profissional = usuario.id == sessao.agendamento.profissional_id
        profissional_presente = sessao.profissional_entrou_em is not None
        admitido = sessao.paciente_admitido_em is not None

        if eh_profissional:
            mensagem = (
                "Paciente aguardando na sala de espera."
                if sessao.paciente_entrou_em and not admitido
                else "Aguardando o paciente."
            )
            return EstadoLobby(sessao, profissional_presente, admitido, True, mensagem)

        if admitido:
            mensagem = "Você foi admitido. Entrando na sala…"
        elif profissional_presente:
            mensagem = "O profissional está na sala e vai admitir você em instantes."
        else:
            mensagem = "Aguardando o profissional entrar na sala."
        return EstadoLobby(sessao, profissional_presente, admitido, admitido, mensagem)

    async def admitir_paciente(self, sessao: Sessao, profissional: Usuario) -> Sessao:
        if profissional.id != sessao.agendamento.profissional_id:
            raise NaoAutorizado("Apenas o profissional admite o paciente.")

        sessao.paciente_admitido_em = agora_utc()
        if sessao.status in (StatusSessao.SALA_PRONTA, StatusSessao.LOBBY):
            sessao.status = StatusSessao.EM_ANDAMENTO
            sessao.iniciada_em = sessao.iniciada_em or sessao.paciente_admitido_em
        await self.db.flush()

        await self.video.admitir(sessao.sala_nome or "", str(sessao.agendamento.paciente_id))
        await self._registrar_evento(
            sessao, TipoEventoSessao.ADMITIDO, sessao.agendamento.paciente_id
        )
        log.info("sessao.paciente_admitido", sessao_id=str(sessao.id))
        return sessao

    async def token_de_entrada(self, sessao: Sessao, usuario: Usuario) -> str:
        """Token de curta duração, emitido na hora. Nunca persistido."""
        eh_dono = usuario.id == sessao.agendamento.profissional_id
        return await self.video.emitir_token(
            sessao.sala_nome or "",
            Participante(id=str(usuario.id), nome=usuario.primeiro_nome, eh_dono=eh_dono),
            expira_em=agora_utc() + timedelta(hours=2),
        )

    # --- Encerramento -------------------------------------------------------

    async def encerrar(self, sessao: Sessao, usuario: Usuario) -> Sessao:
        if usuario.papel is Papel.PACIENTE and usuario.id != sessao.agendamento.paciente_id:
            raise NaoAutorizado()

        agora = agora_utc()
        sessao.status = StatusSessao.ENCERRADA
        sessao.encerrada_em = agora
        if sessao.iniciada_em:
            sessao.duracao_real_min = int((agora - sessao.iniciada_em).total_seconds() // 60)

        agendamento = sessao.agendamento
        agendamento.status = StatusAgendamento.REALIZADO
        await self.db.flush()

        await self._registrar_evento(sessao, TipoEventoSessao.ENCERRADA, usuario.id)
        await self._pontuar(
            agendamento.profissional_id,
            TipoPontuacao.SESSAO_REALIZADA,
            PONTOS_SESSAO_REALIZADA,
            sessao.id,
            "Sessão realizada",
        )
        if sessao.sala_nome:
            await self.video.encerrar_sala(sessao.sala_nome)

        log.info("sessao.encerrada", sessao_id=str(sessao.id))
        return sessao

    # --- Internos -----------------------------------------------------------

    async def _registrar_evento(
        self,
        sessao: Sessao,
        tipo: TipoEventoSessao,
        ator_id: uuid.UUID | None,
        metadados: dict[str, object] | None = None,
    ) -> EventoSessao:
        evento = EventoSessao(
            sessao_id=sessao.id,
            tipo=tipo,
            ator_id=ator_id,
            ocorrido_em=agora_utc(),
            metadados=metadados,
        )
        self.db.add(evento)
        await self.db.flush()
        return evento

    async def _pontuar_pontualidade(
        self, sessao: Sessao, agendamento: Agendamento, entrada: datetime
    ) -> None:
        """R10 — o profissional pontual ganha pontos.

        Pontual = entrou até N minutos após o início marcado. Entrar antes
        também conta.
        """
        janela = await self.parametros.inteiro("sessao.janela_pontualidade_min", 5)
        if entrada <= agendamento.inicio_utc + timedelta(minutes=janela):
            await self._pontuar(
                agendamento.profissional_id,
                TipoPontuacao.PONTUALIDADE_PROFISSIONAL,
                PONTOS_PONTUALIDADE,
                sessao.id,
                "Entrou pontualmente na sala",
            )

    async def _pontuar(
        self,
        usuario_id: uuid.UUID,
        tipo: TipoPontuacao,
        pontos: int,
        referencia_id: uuid.UUID,
        descricao: str,
    ) -> None:
        """Idempotente por construção: `UNIQUE (usuario_id, tipo, referencia_id)`
        no banco impede pontuar o mesmo fato duas vezes."""
        ja_existe = await self.db.scalar(
            select(EventoPontuacao.id).where(
                EventoPontuacao.usuario_id == usuario_id,
                EventoPontuacao.tipo == tipo,
                EventoPontuacao.referencia_id == referencia_id,
            )
        )
        if ja_existe is not None:
            return

        self.db.add(
            EventoPontuacao(
                usuario_id=usuario_id,
                tipo=tipo,
                pontos=pontos,
                referencia_tipo="Sessao",
                referencia_id=referencia_id,
                descricao=descricao,
                criado_em=agora_utc(),
            )
        )
        await self.db.flush()
