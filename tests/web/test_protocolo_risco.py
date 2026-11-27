"""Protocolo de risco (S1) pela interface: formulário de sintomas e painel."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import Notificacao
from app.services.matching_service import MatchingService
from app.services.protocolo_risco_service import TEMPLATE_ALERTA_RISCO
from tests import fabricas as f
from tests.web.test_profissional import cliente, logar

pytestmark = [pytest.mark.web, pytest.mark.db]


class TestFormularioDeSintomas:
    async def test_marcar_sintoma_de_risco_avisa_o_profissional(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        await f.criar_agendamento(sessao, prof, pac, await f.criar_especialidade(sessao))
        risco = await f.criar_sintoma(sessao, bandeira_risco=True)

        async with cliente(app) as c:
            headers = await logar(c, app, sessao, settings, pac.usuario_id)
            r = await c.post(
                "/paciente/sintomas",
                data={"sintoma_id": [str(risco.id)], f"intensidade_{risco.id}": "5"},
                headers=headers,
            )
        assert r.status_code == 303

        alerta = await sessao.scalar(
            select(Notificacao).where(
                Notificacao.usuario_id == prof.usuario_id,
                Notificacao.template == TEMPLATE_ALERTA_RISCO,
            )
        )
        assert alerta is not None


class TestPainel:
    async def test_profissional_ve_o_aviso_do_paciente(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao, nome="Marina Costa")
        await f.criar_agendamento(sessao, prof, pac, await f.criar_especialidade(sessao))
        risco = await f.criar_sintoma(sessao, nome="Machuco a mim mesmo", bandeira_risco=True)
        await MatchingService(sessao).registrar_sintomas(pac, {risco.id: 5})

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, prof.usuario_id)
            pagina = (await c.get("/painel")).text

        assert "sinal de risco" in pagina
        assert "Marina Costa" in pagina
        assert "Machuco a mim mesmo" in pagina
        assert "188" in pagina

    async def test_profissional_sem_vinculo_nao_ve_o_dado(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Dado de saúde só para quem atende: o profissional alheio não vê nada."""
        alheio = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao, nome="Marina Costa")
        risco = await f.criar_sintoma(sessao, nome="Machuco a mim mesmo", bandeira_risco=True)
        await MatchingService(sessao).registrar_sintomas(pac, {risco.id: 5})

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, alheio.usuario_id)
            pagina = (await c.get("/painel")).text

        assert "Marina Costa" not in pagina
        assert "Machuco a mim mesmo" not in pagina
