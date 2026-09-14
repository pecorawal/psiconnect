"""Concorrência real: duas transações disputando a mesma agenda.

Este teste **não** usa a fixture transacional: ele precisa de duas conexões de
verdade, commitando de verdade, para que uma enxergue o efeito da outra. Por
isso limpa o que criou no final.

É o teste que justifica os advisory locks. `EXCLUDE` resolve sobreposição, mas
R1 e R2 são restrições agregadas -- sem lock, duas transações leem "9h30
agendadas" ao mesmo tempo e ambas inserem.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.erros import LimiteHorasDiaExcedido, SlotIndisponivel
from app.core.tempo import TZ_BR, agora_utc, combinar_local
from app.models import StatusAgendamento
from app.services.agendamento_service import AgendamentoService, PedidoReserva
from app.services.parametros_service import ParametrosService
from tests import fabricas as f

pytestmark = [pytest.mark.db, pytest.mark.lento]

MARCA = "concorrencia"


@pytest.fixture
async def fabrica_sessoes(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessões independentes, cada uma com sua própria conexão e commit."""
    engine = create_async_engine(str(settings.database_url))
    yield async_sessionmaker(engine, expire_on_commit=False)

    # Limpeza em SQL puro: os agendamentos primeiro, por causa das FKs.
    # (ON DELETE é RESTRICT justamente para não apagar histórico por acidente.)
    async with engine.begin() as conexao:
        await conexao.execute(
            text(
                "DELETE FROM agendamentos WHERE paciente_id IN "
                "(SELECT id FROM usuarios WHERE email LIKE :padrao) "
                "OR profissional_id IN (SELECT id FROM usuarios WHERE email LIKE :padrao)"
            ),
            {"padrao": f"%{MARCA}%"},
        )
        await conexao.execute(
            text("DELETE FROM usuarios WHERE email LIKE :padrao"), {"padrao": f"%{MARCA}%"}
        )
    await engine.dispose()


def _email(prefixo: str) -> str:
    return f"{prefixo}-{MARCA}-{uuid.uuid4().hex[:8]}@teste.br"


async def _preparar(
    fabrica: async_sessionmaker[AsyncSession], quantos_pacientes: int
) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    """Cria profissional, especialidade e pacientes, com commit de verdade."""
    async with fabrica() as s, s.begin():
        prof = await f.criar_profissional(
            s,
            nome=f"Prof {MARCA}",
            registro_numero=uuid.uuid4().hex[:10],
            email=_email("prof"),
        )
        esp = await f.criar_especialidade(s)
        await f.adicionar_especialidade(s, prof, esp, ordem=1)
        pacientes = [
            (await f.criar_paciente(s, email=_email("pac"))).usuario_id
            for _ in range(quantos_pacientes)
        ]
        return prof.usuario_id, esp.id, pacientes


async def _reservar_em_transacao_propria(
    fabrica: async_sessionmaker[AsyncSession],
    settings: Settings,
    prof_id: uuid.UUID,
    pac_id: uuid.UUID,
    esp_id: uuid.UUID,
    inicio,  # type: ignore[no-untyped-def]
) -> str:
    """Devolve ``'ok'`` ou o código do erro de domínio."""
    from app.models import PerfilPaciente, PerfilProfissional

    async with fabrica() as s:
        try:
            async with s.begin():
                prof = await s.get(PerfilProfissional, prof_id)
                pac = await s.get(PerfilPaciente, pac_id)
                assert prof is not None and pac is not None
                servico = AgendamentoService(s, ParametrosService(s, settings))
                await servico.reservar(PedidoReserva(pac, prof, esp_id, inicio))
            return "ok"
        except (SlotIndisponivel, LimiteHorasDiaExcedido) as exc:
            return exc.codigo


class TestConcorrencia:
    async def test_mesmo_slot_apenas_um_vence(
        self, fabrica_sessoes: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        """R6 sob concorrência: a constraint EXCLUDE decide.

        Dez pacientes disputam o mesmo horário ao mesmo tempo. Exatamente um
        consegue -- não zero (deadlock) nem dois (overbooking).
        """
        prof_id, esp_id, pacientes = await _preparar(fabrica_sessoes, 10)
        horario = combinar_local(
            (agora_utc().astimezone(TZ_BR) + timedelta(days=3)).date(), 10 * 60, TZ_BR
        )

        resultados = await asyncio.gather(
            *[
                _reservar_em_transacao_propria(
                    fabrica_sessoes, settings, prof_id, pac, esp_id, horario
                )
                for pac in pacientes[:10]
            ]
        )

        assert resultados.count("ok") == 1, f"esperado exatamente 1 sucesso, veio {resultados}"
        assert all(r in ("ok", "slot_indisponivel") for r in resultados)

    async def test_limite_de_horas_nao_e_furado_por_corrida(
        self, fabrica_sessoes: async_sessionmaker[AsyncSession], settings: Settings
    ) -> None:
        """R1 sob concorrência: é aqui que só a constraint não bastaria.

        Quinze pacientes tentam horários **diferentes** no mesmo dia, todos ao
        mesmo tempo. Nenhum viola a EXCLUDE (não há sobreposição), mas o limite
        de 10h/dia é agregado: sem `pg_advisory_xact_lock`, várias transações
        leem o mesmo total e todas passam.

        Com sessão de 50 min, 10h comportam exatamente 12 sessões.
        """
        prof_id, esp_id, pacientes = await _preparar(fabrica_sessoes, 15)
        dia = (agora_utc().astimezone(TZ_BR) + timedelta(days=4)).date()

        resultados = await asyncio.gather(
            *[
                _reservar_em_transacao_propria(
                    fabrica_sessoes,
                    settings,
                    prof_id,
                    pac,
                    esp_id,
                    combinar_local(dia, (7 + i) * 60, TZ_BR),
                )
                for i, pac in enumerate(pacientes[:15])
            ]
        )

        aceitos = resultados.count("ok")
        assert aceitos == 12, f"o limite de 10h foi furado: {aceitos} aceitos ({resultados})"

        # Confere no banco, não só no retorno.
        async with fabrica_sessoes() as s:
            total = await s.scalar(
                text(
                    """
                    SELECT COALESCE(SUM(duracao_min), 0) FROM agendamentos
                    WHERE profissional_id = :prof AND status = :status
                    """
                ),
                {"prof": prof_id, "status": StatusAgendamento.PENDENTE_PAGAMENTO.value},
            )
        assert int(total or 0) <= 10 * 60
