"""AgendamentoService: R1, R2, R6 e expiração de reserva."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.erros import (
    AgendamentoNoPassado,
    AntecedenciaInsuficiente,
    LimiteHorasDiaExcedido,
    LimiteSessoesSemanaExcedido,
    SlotIndisponivel,
)
from app.core.tempo import TZ_BR, agora_utc, combinar_local
from app.models import StatusAgendamento
from app.services.agendamento_service import AgendamentoService, PedidoReserva
from app.services.parametros_service import ParametrosService
from tests import fabricas as f

pytestmark = [pytest.mark.db, pytest.mark.servicos]


def servico(sessao: AsyncSession, settings: Settings) -> AgendamentoService:
    ParametrosService.invalidar_cache()
    return AgendamentoService(sessao, ParametrosService(sessao, settings))


async def cenario(sessao: AsyncSession):  # type: ignore[no-untyped-def]
    prof = await f.criar_profissional(sessao)
    pac = await f.criar_paciente(sessao)
    esp = await f.criar_especialidade(sessao)
    await f.adicionar_especialidade(sessao, prof, esp, ordem=1, preco_centavos=15000)
    return prof, pac, esp


def amanha(hora: int, minuto: int = 0):  # type: ignore[no-untyped-def]
    dia = (agora_utc().astimezone(TZ_BR) + timedelta(days=2)).date()
    return combinar_local(dia, hora * 60 + minuto, TZ_BR)


class TestReservaBasica:
    async def test_reserva_fica_pendente_de_pagamento(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof, pac, esp = await cenario(sessao)
        ag = await servico(sessao, settings).reservar(PedidoReserva(pac, prof, esp.id, amanha(10)))
        assert ag.status is StatusAgendamento.PENDENTE_PAGAMENTO
        assert ag.valor_centavos == 15000
        assert ag.duracao_min == 50
        # O slot já está travado: é o que impede dois pacientes pagarem o mesmo.
        assert ag.reserva_expira_em is not None

    async def test_confirmar_limpa_a_expiracao(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof, pac, esp = await cenario(sessao)
        s = servico(sessao, settings)
        ag = await s.reservar(PedidoReserva(pac, prof, esp.id, amanha(10)))
        confirmado = await s.confirmar(ag.id)
        assert confirmado.status is StatusAgendamento.CONFIRMADO
        assert confirmado.reserva_expira_em is None

    async def test_nao_agenda_no_passado(self, sessao: AsyncSession, settings: Settings) -> None:
        prof, pac, esp = await cenario(sessao)
        with pytest.raises(AgendamentoNoPassado):
            await servico(sessao, settings).reservar(
                PedidoReserva(pac, prof, esp.id, agora_utc() - timedelta(hours=1))
            )

    async def test_exige_antecedencia_minima(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof, pac, esp = await cenario(sessao)
        with pytest.raises(AntecedenciaInsuficiente):
            await servico(sessao, settings).reservar(
                PedidoReserva(pac, prof, esp.id, agora_utc() + timedelta(minutes=30))
            )


class TestR1LimiteHorasDia:
    async def test_conta_minutos_e_nao_consultas(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """O bug do spike: 10 consultas de 50 min são 8h20, não 10h.

        A 11ª ainda cabe; é a 13ª que estoura (12 × 50 = 10h).
        """
        prof, _, esp = await cenario(sessao)
        s = servico(sessao, settings)

        # 12 sessões de 50 min = 600 min = exatamente 10h. Cada uma com um
        # paciente diferente, senão R2 (3/semana) dispararia antes de R1.
        for i in range(12):
            paciente = await f.criar_paciente(sessao)
            await s.reservar(PedidoReserva(paciente, prof, esp.id, amanha(8 + i)))

        outro = await f.criar_paciente(sessao)
        with pytest.raises(LimiteHorasDiaExcedido):
            await s.reservar(PedidoReserva(outro, prof, esp.id, amanha(21)))

    async def test_o_dia_e_o_local_do_profissional(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Às 21h em São Paulo já é o dia seguinte em UTC.

        Se o serviço contasse pelo dia UTC, uma consulta das 21h seria atribuída
        ao dia seguinte e o limite do dia corrente ficaria frouxo.
        """
        prof, pac, esp = await cenario(sessao)
        s = servico(sessao, settings)
        noite = amanha(21)
        assert noite.astimezone(TZ_BR).hour == 21
        assert noite.hour == 0  # já é o dia seguinte em UTC

        ag = await s.reservar(PedidoReserva(pac, prof, esp.id, noite))
        janelas = await s._janelas_do_dia(prof.usuario_id, noite, TZ_BR)
        assert len(janelas) == 1  # encontrou pelo dia LOCAL
        assert ag.id is not None


class TestR2LimiteSessoesSemana:
    async def test_quarta_sessao_na_semana_e_bloqueada(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof, pac, esp = await cenario(sessao)
        s = servico(sessao, settings)

        base = agora_utc().astimezone(TZ_BR)
        # Segunda-feira da semana que vem, para os 4 horários caírem na mesma.
        segunda = (base + timedelta(days=7 - base.weekday())).date()

        for dia_offset in range(3):
            dia = segunda + timedelta(days=dia_offset)
            await s.reservar(PedidoReserva(pac, prof, esp.id, combinar_local(dia, 10 * 60, TZ_BR)))

        with pytest.raises(LimiteSessoesSemanaExcedido):
            await s.reservar(
                PedidoReserva(
                    pac, prof, esp.id, combinar_local(segunda + timedelta(days=3), 10 * 60, TZ_BR)
                )
            )


class TestR6Sobreposicao:
    async def test_slot_ocupado_e_recusado(self, sessao: AsyncSession, settings: Settings) -> None:
        prof, pac1, esp = await cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        s = servico(sessao, settings)
        horario = amanha(10)

        await s.reservar(PedidoReserva(pac1, prof, esp.id, horario))
        with pytest.raises(SlotIndisponivel):
            await s.reservar(PedidoReserva(pac2, prof, esp.id, horario))

    async def test_sessoes_encostadas_sao_permitidas(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """10:00–10:50 e 10:50–11:40 convivem (range '[)')."""
        prof, pac1, esp = await cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        s = servico(sessao, settings)

        await s.reservar(PedidoReserva(pac1, prof, esp.id, amanha(10)))
        await s.reservar(PedidoReserva(pac2, prof, esp.id, amanha(10, 50)))


class TestExpiracaoDeReserva:
    async def test_reserva_vencida_libera_o_horario(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof, pac1, esp = await cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        s = servico(sessao, settings)
        horario = amanha(10)

        ag = await s.reservar(PedidoReserva(pac1, prof, esp.id, horario))
        # Simula o TTL já vencido.
        ag.reserva_expira_em = agora_utc() - timedelta(minutes=1)
        await sessao.flush()

        assert await s.expirar_reservas_vencidas() >= 1
        await sessao.refresh(ag)
        assert ag.status is StatusAgendamento.EXPIRADO

        # O horário voltou ao mercado.
        await s.reservar(PedidoReserva(pac2, prof, esp.id, horario))

    async def test_reserva_no_prazo_nao_expira(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        prof, pac, esp = await cenario(sessao)
        s = servico(sessao, settings)
        ag = await s.reservar(PedidoReserva(pac, prof, esp.id, amanha(10)))
        await s.expirar_reservas_vencidas()
        await sessao.refresh(ag)
        assert ag.status is StatusAgendamento.PENDENTE_PAGAMENTO
