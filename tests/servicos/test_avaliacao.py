"""Avaliação obrigatória — R11 e seus limites."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import AvaliacaoPendente, NaoEncontrado
from app.models import EventoPontuacao, StatusAgendamento, TipoPontuacao
from app.services.avaliacao_service import AvaliacaoService, NotaInvalida
from tests import fabricas as f

pytestmark = [pytest.mark.db, pytest.mark.servicos]


async def sessao_realizada(sessao: AsyncSession):  # type: ignore[no-untyped-def]
    prof = await f.criar_profissional(sessao)
    pac = await f.criar_paciente(sessao)
    esp = await f.criar_especialidade(sessao)
    agendamento = await f.criar_agendamento(
        sessao, prof, pac, esp, status=StatusAgendamento.REALIZADO
    )
    return prof, pac, agendamento


class TestResponder:
    async def test_grava_as_tres_notas(self, sessao: AsyncSession) -> None:
        _, pac, agendamento = await sessao_realizada(sessao)
        avaliacao = await AvaliacaoService(sessao).responder(
            paciente_id=pac.usuario_id,
            agendamento_id=agendamento.id,
            nota_plataforma=5,
            nota_profissional=4,
            nota_proprio_cuidado=3,
            comentario_profissional="  Muito acolhedora.  ",
        )
        assert (avaliacao.nota_plataforma, avaliacao.nota_profissional) == (5, 4)
        assert avaliacao.nota_proprio_cuidado == 3
        # Comentário chega com espaços da UI; guardamos limpo.
        assert avaliacao.comentario_profissional == "Muito acolhedora."
        assert avaliacao.comentario_plataforma is None

    async def test_pontua_o_paciente(self, sessao: AsyncSession) -> None:
        """Requisito: 'isso também gerará pontuação para o paciente'."""
        _, pac, agendamento = await sessao_realizada(sessao)
        await AvaliacaoService(sessao).responder(
            paciente_id=pac.usuario_id,
            agendamento_id=agendamento.id,
            nota_plataforma=5,
            nota_profissional=5,
            nota_proprio_cuidado=5,
        )
        pontos = await sessao.scalar(
            select(EventoPontuacao).where(
                EventoPontuacao.usuario_id == pac.usuario_id,
                EventoPontuacao.tipo == TipoPontuacao.AVALIACAO_RESPONDIDA,
            )
        )
        assert pontos is not None
        assert pontos.pontos > 0

    async def test_responder_duas_vezes_nao_duplica(self, sessao: AsyncSession) -> None:
        _, pac, agendamento = await sessao_realizada(sessao)
        servico = AvaliacaoService(sessao)
        dados = dict(
            paciente_id=pac.usuario_id,
            agendamento_id=agendamento.id,
            nota_plataforma=5,
            nota_profissional=5,
            nota_proprio_cuidado=5,
        )
        primeira = await servico.responder(**dados)  # type: ignore[arg-type]
        segunda = await servico.responder(**dados)  # type: ignore[arg-type]
        assert primeira.id == segunda.id

    @pytest.mark.parametrize("nota", [0, 6, -1])
    async def test_nota_fora_da_escala(self, sessao: AsyncSession, nota: int) -> None:
        _, pac, agendamento = await sessao_realizada(sessao)
        with pytest.raises(NotaInvalida):
            await AvaliacaoService(sessao).responder(
                paciente_id=pac.usuario_id,
                agendamento_id=agendamento.id,
                nota_plataforma=nota,
                nota_profissional=3,
                nota_proprio_cuidado=3,
            )

    async def test_nao_avalia_sessao_de_outro(self, sessao: AsyncSession) -> None:
        """404, não 403: nem confirmamos que a sessão existe."""
        _, _, agendamento = await sessao_realizada(sessao)
        outro = await f.criar_paciente(sessao)
        with pytest.raises(NaoEncontrado):
            await AvaliacaoService(sessao).responder(
                paciente_id=outro.usuario_id,
                agendamento_id=agendamento.id,
                nota_plataforma=5,
                nota_profissional=5,
                nota_proprio_cuidado=5,
            )

    async def test_nao_avalia_sessao_que_nao_aconteceu(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        esp = await f.criar_especialidade(sessao)
        agendamento = await f.criar_agendamento(
            sessao, prof, pac, esp, status=StatusAgendamento.CONFIRMADO
        )
        with pytest.raises(NaoEncontrado):
            await AvaliacaoService(sessao).responder(
                paciente_id=pac.usuario_id,
                agendamento_id=agendamento.id,
                nota_plataforma=5,
                nota_profissional=5,
                nota_proprio_cuidado=5,
            )


class TestBloqueio:
    """R11 — o bloqueio existe, mas tem limite bem definido."""

    async def test_sessao_realizada_sem_avaliacao_bloqueia_novo_agendamento(
        self, sessao: AsyncSession
    ) -> None:
        _, pac, _ = await sessao_realizada(sessao)
        with pytest.raises(AvaliacaoPendente):
            await AvaliacaoService(sessao).exigir_avaliacoes_em_dia(pac.usuario_id)

    async def test_depois_de_avaliar_libera(self, sessao: AsyncSession) -> None:
        _, pac, agendamento = await sessao_realizada(sessao)
        servico = AvaliacaoService(sessao)
        await servico.responder(
            paciente_id=pac.usuario_id,
            agendamento_id=agendamento.id,
            nota_plataforma=5,
            nota_profissional=5,
            nota_proprio_cuidado=5,
        )
        await servico.exigir_avaliacoes_em_dia(pac.usuario_id)  # não levanta

    async def test_paciente_sem_historico_nao_e_bloqueado(self, sessao: AsyncSession) -> None:
        pac = await f.criar_paciente(sessao)
        await AvaliacaoService(sessao).exigir_avaliacoes_em_dia(pac.usuario_id)

    async def test_sessao_cancelada_nao_bloqueia(self, sessao: AsyncSession) -> None:
        """Só sessão REALIZADA gera obrigação — não faz sentido avaliar o que
        não aconteceu."""
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        esp = await f.criar_especialidade(sessao)
        await f.criar_agendamento(
            sessao, prof, pac, esp, status=StatusAgendamento.CANCELADO_PACIENTE
        )
        await AvaliacaoService(sessao).exigir_avaliacoes_em_dia(pac.usuario_id)

    async def test_a_pendencia_e_a_mais_antiga(self, sessao: AsyncSession) -> None:
        from datetime import timedelta

        from app.core.tempo import agora_utc

        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        esp = await f.criar_especialidade(sessao)
        antiga = await f.criar_agendamento(
            sessao,
            prof,
            pac,
            esp,
            inicio=agora_utc() - timedelta(days=7),
            status=StatusAgendamento.REALIZADO,
        )
        await f.criar_agendamento(
            sessao,
            prof,
            pac,
            esp,
            inicio=agora_utc() - timedelta(days=1),
            status=StatusAgendamento.REALIZADO,
        )
        pendente = await AvaliacaoService(sessao).pendente_do_paciente(pac.usuario_id)
        assert pendente is not None
        assert pendente.id == antiga.id


class TestMedia:
    async def test_media_do_profissional(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        esp = await f.criar_especialidade(sessao)
        servico = AvaliacaoService(sessao)

        for nota in (5, 3):
            pac = await f.criar_paciente(sessao)
            agendamento = await f.criar_agendamento(
                sessao, prof, pac, esp, status=StatusAgendamento.REALIZADO
            )
            await servico.responder(
                paciente_id=pac.usuario_id,
                agendamento_id=agendamento.id,
                nota_plataforma=4,
                nota_profissional=nota,
                nota_proprio_cuidado=4,
            )

        assert await servico.media_do_profissional(prof.usuario_id) == pytest.approx(4.0)
