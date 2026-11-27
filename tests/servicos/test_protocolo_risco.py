"""Protocolo de risco (S1): o profissional vinculado é avisado do sinal de crise."""

from __future__ import annotations

import itertools
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.tempo import agora_utc
from app.models import (
    CanalNotificacao,
    Notificacao,
    PerfilPaciente,
    PerfilProfissional,
    StatusAgendamento,
)
from app.services.matching_service import MatchingService
from app.services.protocolo_risco_service import (
    TEMPLATE_ALERTA_RISCO,
    ProtocoloRiscoService,
)
from tests import fabricas as f

pytestmark = [pytest.mark.db, pytest.mark.servicos]

#: Um dia por agendamento: o mesmo profissional não pode ter horários sobrepostos.
_dias = itertools.count(1)


async def alertas_para(sessao: AsyncSession, profissional: PerfilProfissional) -> list[Notificacao]:
    return list(
        (
            await sessao.execute(
                select(Notificacao).where(
                    Notificacao.usuario_id == profissional.usuario_id,
                    Notificacao.template == TEMPLATE_ALERTA_RISCO,
                )
            )
        )
        .scalars()
        .all()
    )


async def vincular(
    sessao: AsyncSession,
    prof: PerfilProfissional,
    pac: PerfilPaciente,
    status: StatusAgendamento = StatusAgendamento.CONFIRMADO,
) -> None:
    esp = await f.criar_especialidade(sessao)
    await f.criar_agendamento(
        sessao, prof, pac, esp, status=status, inicio=agora_utc() + timedelta(days=next(_dias))
    )


async def marcar(sessao: AsyncSession, pac: PerfilPaciente, *sintomas: uuid.UUID) -> None:
    await MatchingService(sessao).registrar_sintomas(pac, {s: 4 for s in sintomas})


class TestAlertarProfissionais:
    async def test_profissional_vinculado_recebe_email_sem_dado_clinico(
        self, sessao: AsyncSession
    ) -> None:
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao, nome="Marina Costa")
        await vincular(sessao, prof, pac)
        risco = await f.criar_sintoma(
            sessao, nome="Penso em não estar mais aqui", bandeira_risco=True
        )
        await marcar(sessao, pac, risco.id)

        assert await ProtocoloRiscoService(sessao).alertar_profissionais(pac) == 1

        [alerta] = await alertas_para(sessao, prof)
        assert alerta.canal is CanalNotificacao.EMAIL
        assert alerta.destino == prof.usuario.email
        # O corpo de e-mail passa por terceiros: só o primeiro nome, nada de sintoma.
        assert alerta.contexto == {"paciente_nome": "Marina"}

    async def test_sem_sintoma_de_risco_nao_alerta(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        await vincular(sessao, prof, pac)
        comum = await f.criar_sintoma(sessao)
        await marcar(sessao, pac, comum.id)

        assert await ProtocoloRiscoService(sessao).alertar_profissionais(pac) == 0
        assert await alertas_para(sessao, prof) == []

    @pytest.mark.parametrize(
        "status",
        [StatusAgendamento.PENDENTE_PAGAMENTO, StatusAgendamento.CANCELADO_PACIENTE],
    )
    async def test_sem_vinculo_de_atendimento_nao_alerta(
        self, sessao: AsyncSession, status: StatusAgendamento
    ) -> None:
        """Reserva não paga ou cancelada não dá ao profissional acesso ao dado de saúde."""
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        await vincular(sessao, prof, pac, status=status)
        risco = await f.criar_sintoma(sessao, bandeira_risco=True)
        await marcar(sessao, pac, risco.id)

        assert await ProtocoloRiscoService(sessao).alertar_profissionais(pac) == 0
        assert await alertas_para(sessao, prof) == []

    async def test_mesmo_quadro_nao_repete_e_sinal_novo_alerta_de_novo(
        self, sessao: AsyncSession
    ) -> None:
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        await vincular(sessao, prof, pac)
        r1 = await f.criar_sintoma(sessao, bandeira_risco=True)
        r2 = await f.criar_sintoma(sessao, bandeira_risco=True)
        servico = ProtocoloRiscoService(sessao)

        await marcar(sessao, pac, r1.id)
        assert await servico.alertar_profissionais(pac) == 1
        # Salvar o formulário de novo com o mesmo quadro não gera outro e-mail.
        await marcar(sessao, pac, r1.id)
        assert await servico.alertar_profissionais(pac) == 0
        # Um sinal novo, sim.
        await marcar(sessao, pac, r1.id, r2.id)
        assert await servico.alertar_profissionais(pac) == 1
        assert len(await alertas_para(sessao, prof)) == 2

    async def test_alerta_cada_profissional_vinculado(self, sessao: AsyncSession) -> None:
        pac = await f.criar_paciente(sessao)
        a = await f.criar_profissional(sessao)
        b = await f.criar_profissional(sessao, nome="Dr. Bruno Lima")
        alheio = await f.criar_profissional(sessao, nome="Dra. Carla Reis")
        await vincular(sessao, a, pac, StatusAgendamento.REALIZADO)
        await vincular(sessao, b, pac)
        risco = await f.criar_sintoma(sessao, bandeira_risco=True)
        await marcar(sessao, pac, risco.id)

        assert await ProtocoloRiscoService(sessao).alertar_profissionais(pac) == 2
        assert len(await alertas_para(sessao, a)) == 1
        assert len(await alertas_para(sessao, b)) == 1
        assert await alertas_para(sessao, alheio) == []


class TestAlertasDoProfissional:
    async def test_lista_so_pacientes_vinculados_com_risco(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        com_risco = await f.criar_paciente(sessao, nome="Marina Costa")
        sem_risco = await f.criar_paciente(sessao, nome="Pedro Alves")
        de_outro = await f.criar_paciente(sessao, nome="Luiza Prado")
        await vincular(sessao, prof, com_risco)
        await vincular(sessao, prof, sem_risco)
        await vincular(sessao, await f.criar_profissional(sessao), de_outro)

        risco = await f.criar_sintoma(sessao, nome="Machuco a mim mesmo", bandeira_risco=True)
        comum = await f.criar_sintoma(sessao, nome="Insônia")
        await marcar(sessao, com_risco, risco.id, comum.id)
        await marcar(sessao, sem_risco, comum.id)
        await marcar(sessao, de_outro, risco.id)

        [alerta] = await ProtocoloRiscoService(sessao).alertas_do_profissional(prof.usuario_id)
        assert alerta.paciente_id == com_risco.usuario_id
        assert alerta.paciente_nome == "Marina Costa"
        # Só o sintoma de risco aparece no aviso.
        assert alerta.sintomas == ["Machuco a mim mesmo"]


class TestConfirmacaoDeConsulta:
    async def test_profissional_recem_vinculado_e_avisado(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Paciente que já sinalizou risco marca com um profissional novo: ele é avisado
        assim que a consulta é confirmada, sem esperar o paciente mexer nos sintomas."""
        from app.providers.pagamento.fake import FakePaymentProvider
        from app.services.checkout_service import CheckoutService
        from app.services.parametros_service import ParametrosService
        from tests.servicos.test_credito_service import _comprar_pacote

        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        esp = await f.criar_especialidade(sessao)
        risco = await f.criar_sintoma(sessao, bandeira_risco=True)
        await marcar(sessao, pac, risco.id)
        await _comprar_pacote(sessao, pac, prof, esp)
        agendamento = await f.criar_agendamento(
            sessao, prof, pac, esp, status=StatusAgendamento.PENDENTE_PAGAMENTO
        )
        # Antes da confirmação não há vínculo, logo não há aviso.
        assert await alertas_para(sessao, prof) == []

        # Como numa requisição real: o checkout carrega o agendamento do banco,
        # com as relações, em vez de reaproveitar os objetos da fábrica.
        sessao.expunge_all()
        checkout = CheckoutService(
            sessao, ParametrosService(sessao, settings), FakePaymentProvider()
        )
        await checkout.usar_credito(paciente=pac, agendamento_id=agendamento.id)

        assert len(await alertas_para(sessao, prof)) == 1
