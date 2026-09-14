"""Avaliação de fim de sessão — R11.

As três perguntas do requisito original: sobre a plataforma, sobre o
profissional, e sobre o próprio cuidado do paciente.

Sobre "obrigatória": ela **bloqueia marcar uma nova sessão**, e nada além
disso. Bloquear logout, suporte ou o acesso do titular aos próprios dados seria
prender a pessoa dentro do produto — e, no caso dos dados, violaria o art. 18 da
LGPD.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import AvaliacaoPendente, ErroDominio, NaoEncontrado
from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models import (
    Agendamento,
    Avaliacao,
    EventoPontuacao,
    StatusAgendamento,
    TipoPontuacao,
)

log = get_logger(__name__)

PONTOS_AVALIACAO = 5


class NotaInvalida(ErroDominio):
    codigo = "nota_invalida"
    mensagem_padrao = "Dê uma nota de 1 a 5 para cada pergunta."


class AvaliacaoService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    async def pendente_do_paciente(self, paciente_id: uuid.UUID) -> Agendamento | None:
        """A sessão realizada mais antiga que ainda não foi avaliada."""
        resultado: Agendamento | None = await self.sessao.scalar(
            select(Agendamento)
            .outerjoin(Avaliacao, Avaliacao.agendamento_id == Agendamento.id)
            .where(
                Agendamento.paciente_id == paciente_id,
                Agendamento.status == StatusAgendamento.REALIZADO,
                Avaliacao.id.is_(None),
            )
            .order_by(Agendamento.inicio_utc)
            .limit(1)
        )
        return resultado

    async def exigir_avaliacoes_em_dia(self, paciente_id: uuid.UUID) -> None:
        """Chamado antes de marcar nova sessão."""
        pendente = await self.pendente_do_paciente(paciente_id)
        if pendente is not None:
            raise AvaliacaoPendente(
                "Avalie sua última sessão antes de marcar uma nova. Leva menos de um minuto.",
                detalhes={"agendamento_id": str(pendente.id)},
            )

    async def responder(
        self,
        *,
        paciente_id: uuid.UUID,
        agendamento_id: uuid.UUID,
        nota_plataforma: int,
        nota_profissional: int,
        nota_proprio_cuidado: int,
        comentario_plataforma: str | None = None,
        comentario_profissional: str | None = None,
        comentario_proprio_cuidado: str | None = None,
    ) -> Avaliacao:
        agendamento = await self.sessao.get(Agendamento, agendamento_id)
        if agendamento is None or agendamento.paciente_id != paciente_id:
            raise NaoEncontrado("Sessão não encontrada.")
        if agendamento.status is not StatusAgendamento.REALIZADO:
            raise NaoEncontrado("Só é possível avaliar uma sessão já realizada.")

        notas = (nota_plataforma, nota_profissional, nota_proprio_cuidado)
        if any(not 1 <= n <= 5 for n in notas):
            raise NotaInvalida()

        ja_existe = await self.sessao.scalar(
            select(Avaliacao).where(Avaliacao.agendamento_id == agendamento_id)
        )
        if ja_existe is not None:
            return ja_existe

        avaliacao = Avaliacao(
            agendamento_id=agendamento_id,
            nota_plataforma=nota_plataforma,
            nota_profissional=nota_profissional,
            nota_proprio_cuidado=nota_proprio_cuidado,
            comentario_plataforma=_limpar(comentario_plataforma),
            comentario_profissional=_limpar(comentario_profissional),
            comentario_proprio_cuidado=_limpar(comentario_proprio_cuidado),
            respondida_em=agora_utc(),
        )
        self.sessao.add(avaliacao)
        await self.sessao.flush()

        # "Isso também gerará pontuação para o paciente" (requisito original).
        # UNIQUE(usuario_id, tipo, referencia_id) torna isto idempotente.
        self.sessao.add(
            EventoPontuacao(
                usuario_id=paciente_id,
                tipo=TipoPontuacao.AVALIACAO_RESPONDIDA,
                pontos=PONTOS_AVALIACAO,
                referencia_tipo="Agendamento",
                referencia_id=agendamento_id,
                descricao="Avaliou a sessão",
                criado_em=agora_utc(),
            )
        )
        await self.sessao.flush()

        log.info("avaliacao.respondida", agendamento_id=str(agendamento_id))
        return avaliacao

    async def media_do_profissional(self, profissional_id: uuid.UUID) -> float | None:
        from sqlalchemy import func

        media = await self.sessao.scalar(
            select(func.avg(Avaliacao.nota_profissional))
            .join(Agendamento, Avaliacao.agendamento_id == Agendamento.id)
            .where(Agendamento.profissional_id == profissional_id)
        )
        return float(media) if media is not None else None


def _limpar(texto: str | None) -> str | None:
    if texto is None:
        return None
    limpo = texto.strip()
    return limpo or None
