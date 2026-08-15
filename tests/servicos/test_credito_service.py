"""Créditos de pacote: consumo, devolução, validade e concorrência.

O que estes testes protegem:

1. **Cobrar duas vezes pela mesma sessão** — quem tem crédito não pode ser
   levado a pagar de novo.
2. **Gastar o mesmo crédito duas vezes** — duas requisições simultâneas.
3. **Crédito usado com outro profissional** — quebraria a economia do pacote.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tempo import agora_utc
from app.models import (
    CompraPlano,
    CreditoSessao,
    Especialidade,
    PerfilPaciente,
    PerfilProfissional,
    StatusAgendamento,
    StatusCompra,
    StatusCredito,
)
from app.services.credito_service import CreditoService, SemCreditoDisponivel
from tests.fabricas import (
    criar_agendamento,
    criar_especialidade,
    criar_paciente,
    criar_profissional,
)


async def _comprar_pacote(
    sessao: AsyncSession,
    paciente: PerfilPaciente,
    profissional: PerfilProfissional,
    especialidade: Especialidade,
    *,
    quantidade: int = 5,
    expira_em_dias: int | None = 180,
    status: StatusCompra = StatusCompra.ATIVA,
) -> CompraPlano:
    from app.models import Plano, SlugPlano

    plano = await sessao.scalar(select(Plano).where(Plano.slug == SlugPlano.PACOTE_5))
    assert plano is not None, "seed de planos ausente"

    expira = (
        agora_utc() + timedelta(days=expira_em_dias) if expira_em_dias is not None else None
    )
    compra = CompraPlano(
        paciente_id=paciente.usuario_id,
        profissional_id=profissional.usuario_id,
        plano_id=plano.id,
        especialidade_id=especialidade.id,
        quantidade_total=quantidade,
        valor_sessao_centavos=13500,
        valor_total_centavos=13500 * quantidade,
        percentual_comissao_aplicado=12,
        status=status,
        expira_em=expira,
    )
    sessao.add(compra)
    await sessao.flush()
    sessao.add_all(
        [
            CreditoSessao(compra_plano_id=compra.id, expira_em=expira)
            for _ in range(quantidade)
        ]
    )
    await sessao.flush()
    return compra


class TestSaldo:
    async def test_pacote_recem_comprado_mostra_todos_os_creditos(
        self, sessao: AsyncSession
    ) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(sessao, paciente, profissional, especialidade)

        saldos = await CreditoService(sessao).saldo(paciente.usuario_id)
        assert len(saldos) == 1
        assert saldos[0].disponiveis == 5

    async def test_compra_cancelada_nao_gera_saldo(self, sessao: AsyncSession) -> None:
        """Um estorno cancela a compra: os créditos morrem junto."""
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(
            sessao, paciente, profissional, especialidade, status=StatusCompra.CANCELADA
        )

        assert await CreditoService(sessao).saldo(paciente.usuario_id) == []

    async def test_credito_vencido_nao_aparece(self, sessao: AsyncSession) -> None:
        """O filtro é por data, não por status.

        Entre o vencimento e a passagem do worker existe uma janela em que o
        status ainda diz DISPONIVEL. Mostrar saldo nessa janela ofereceria uma
        sessão que o consumo recusaria no clique seguinte.
        """
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(
            sessao, paciente, profissional, especialidade, expira_em_dias=-1
        )

        assert await CreditoService(sessao).saldo(paciente.usuario_id) == []


class TestConsumo:
    async def test_consumir_gasta_exatamente_um(self, sessao: AsyncSession) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(sessao, paciente, profissional, especialidade)
        agendamento = await criar_agendamento(
            sessao,
            profissional=profissional,
            paciente=paciente,
            especialidade=especialidade,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )

        servico = CreditoService(sessao)
        await servico.consumir(agendamento)

        saldos = await servico.saldo(paciente.usuario_id)
        assert saldos[0].disponiveis == 4

    async def test_credito_de_outro_profissional_nao_serve(
        self, sessao: AsyncSession
    ) -> None:
        """A economia do pacote depende disto.

        Sem a amarração, o paciente compraria dez sessões com o profissional
        mais barato e as gastaria com o mais caro.
        """
        especialidade = await criar_especialidade(sessao)
        profissional_a = await criar_profissional(sessao)
        profissional_b = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(sessao, paciente, profissional_a, especialidade)

        agendamento = await criar_agendamento(
            sessao,
            profissional=profissional_b,
            paciente=paciente,
            especialidade=especialidade,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )

        with pytest.raises(SemCreditoDisponivel):
            await CreditoService(sessao).consumir(agendamento)

    async def test_credito_de_outra_especialidade_nao_serve(
        self, sessao: AsyncSession
    ) -> None:
        """O preço varia por especialidade dentro do mesmo profissional."""
        esp_a = await criar_especialidade(sessao)
        esp_b = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(sessao, paciente, profissional, esp_a)

        agendamento = await criar_agendamento(
            sessao,
            profissional=profissional,
            paciente=paciente,
            especialidade=esp_b,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )

        with pytest.raises(SemCreditoDisponivel):
            await CreditoService(sessao).consumir(agendamento)

    async def test_pacote_esgotado_recusa(self, sessao: AsyncSession) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(sessao, paciente, profissional, especialidade, quantidade=1)

        primeiro = await criar_agendamento(
            sessao,
            profissional=profissional,
            paciente=paciente,
            especialidade=especialidade,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )
        segundo = await criar_agendamento(
            sessao,
            profissional=profissional,
            paciente=paciente,
            especialidade=especialidade,
            inicio=agora_utc() + timedelta(days=3),
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )

        servico = CreditoService(sessao)
        await servico.consumir(primeiro)
        with pytest.raises(SemCreditoDisponivel):
            await servico.consumir(segundo)

    async def test_consome_o_que_vence_primeiro(self, sessao: AsyncSession) -> None:
        """Gastar o de validade longa deixaria o curto vencer sem uso."""
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)

        longa = await _comprar_pacote(
            sessao, paciente, profissional, especialidade, quantidade=1, expira_em_dias=180
        )
        curta = await _comprar_pacote(
            sessao, paciente, profissional, especialidade, quantidade=1, expira_em_dias=10
        )

        agendamento = await criar_agendamento(
            sessao,
            profissional=profissional,
            paciente=paciente,
            especialidade=especialidade,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )
        credito = await CreditoService(sessao).consumir(agendamento)

        assert credito.compra_plano_id == curta.id
        assert credito.compra_plano_id != longa.id


class TestDevolucao:
    async def test_cancelar_devolve_o_credito(self, sessao: AsyncSession) -> None:
        """Cancelar não pode queimar a sessão do pacote."""
        from app.core.config import get_settings
        from app.services.agendamento_service import AgendamentoService
        from app.services.parametros_service import ParametrosService

        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(sessao, paciente, profissional, especialidade)
        agendamento = await criar_agendamento(
            sessao,
            profissional=profissional,
            paciente=paciente,
            especialidade=especialidade,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )

        servico = CreditoService(sessao)
        await servico.consumir(agendamento)
        assert (await servico.saldo(paciente.usuario_id))[0].disponiveis == 4

        agenda = AgendamentoService(sessao, ParametrosService(sessao, get_settings()))
        await agenda.cancelar(
            agendamento.id, por_id=paciente.usuario_id, pelo_paciente=True
        )

        assert (await servico.saldo(paciente.usuario_id))[0].disponiveis == 5

    async def test_credito_vencido_nao_volta_para_disponivel(
        self, sessao: AsyncSession
    ) -> None:
        """Devolver um crédito morto o faria aparecer no saldo sem servir."""
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        compra = await _comprar_pacote(
            sessao, paciente, profissional, especialidade, quantidade=1
        )
        agendamento = await criar_agendamento(
            sessao,
            profissional=profissional,
            paciente=paciente,
            especialidade=especialidade,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
        )

        servico = CreditoService(sessao)
        credito = await servico.consumir(agendamento)

        # O crédito vence enquanto a sessão está marcada.
        credito.expira_em = agora_utc() - timedelta(days=1)
        await sessao.flush()

        devolvido = await servico.devolver(agendamento.id)
        assert devolvido is not None
        assert devolvido.status is StatusCredito.EXPIRADO
        assert await servico.saldo(paciente.usuario_id) == []
        assert compra.id is not None


class TestExpiracao:
    async def test_worker_marca_vencidos(self, sessao: AsyncSession) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        compra = await _comprar_pacote(
            sessao, paciente, profissional, especialidade, quantidade=3, expira_em_dias=-1
        )

        quantos = await CreditoService(sessao).expirar_vencidos()
        assert quantos == 3

        restantes = (
            await sessao.scalars(
                select(CreditoSessao).where(
                    CreditoSessao.compra_plano_id == compra.id,
                    CreditoSessao.status == StatusCredito.DISPONIVEL,
                )
            )
        ).all()
        assert restantes == []

    async def test_nao_expira_o_que_ainda_vale(self, sessao: AsyncSession) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _comprar_pacote(sessao, paciente, profissional, especialidade)

        assert await CreditoService(sessao).expirar_vencidos() == 0


class TestConcorrencia:
    async def test_duas_transacoes_nao_gastam_o_mesmo_credito(
        self, settings, conexao
    ) -> None:
        """Duas sessões simultâneas, um crédito só: exatamente uma vence.

        Sem `FOR UPDATE SKIP LOCKED`, as duas leriam a mesma linha DISPONIVEL e
        marcariam sessão — o paciente teria duas consultas pagando uma.

        Precisa de conexões reais e separadas: savepoint na mesma conexão não
        reproduz disputa de lock.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.db.sessao import get_sessionmaker, init_engine

        init_engine(settings)
        fabrica: async_sessionmaker = get_sessionmaker()

        # Preparação em transação própria, commitada para as duas enxergarem.
        async with fabrica() as preparo:
            especialidade = await criar_especialidade(preparo)
            profissional = await criar_profissional(preparo)
            paciente = await criar_paciente(preparo)
            await _comprar_pacote(
                preparo, paciente, profissional, especialidade, quantidade=1
            )
            a1 = await criar_agendamento(
                preparo,
                profissional=profissional,
                paciente=paciente,
                especialidade=especialidade,
                status=StatusAgendamento.PENDENTE_PAGAMENTO,
            )
            a2 = await criar_agendamento(
                preparo,
                profissional=profissional,
                paciente=paciente,
                especialidade=especialidade,
                inicio=agora_utc() + timedelta(days=5),
                status=StatusAgendamento.PENDENTE_PAGAMENTO,
            )
            ids = (a1.id, a2.id, paciente.usuario_id)
            await preparo.commit()

        a1_id, a2_id, paciente_id = ids
        sucessos = 0
        falhas = 0

        async with fabrica() as s1, fabrica() as s2:
            await s1.begin()
            await s2.begin()
            # Sem isto, uma regressão que remova o SKIP LOCKED faria o segundo
            # UPDATE esperar o primeiro commit -- e o teste travaria o CI em vez
            # de falhar. Com timeout, a regressão vira erro em 2 segundos.
            from sqlalchemy import text

            await s1.execute(text("SET LOCAL lock_timeout = '2s'"))
            await s2.execute(text("SET LOCAL lock_timeout = '2s'"))
            ag1 = await s1.get(type(a1), a1_id)
            ag2 = await s2.get(type(a2), a2_id)
            assert ag1 is not None and ag2 is not None

            for sessao_, agendamento_ in ((s1, ag1), (s2, ag2)):
                try:
                    await CreditoService(sessao_).consumir(agendamento_)
                    sucessos += 1
                except SemCreditoDisponivel:
                    falhas += 1

            await s1.commit()
            await s2.rollback()

        assert sucessos == 1, "duas transações gastaram o mesmo crédito"
        assert falhas == 1

        # Limpeza: este teste commita de verdade, fora da transação do fixture.
        async with fabrica() as limpeza:
            from sqlalchemy import delete

            from app.models import Agendamento, Usuario

            await limpeza.execute(
                delete(CreditoSessao).where(
                    CreditoSessao.compra_plano_id.in_(
                        select(CompraPlano.id).where(
                            CompraPlano.paciente_id == paciente_id
                        )
                    )
                )
            )
            await limpeza.execute(
                delete(CompraPlano).where(CompraPlano.paciente_id == paciente_id)
            )
            await limpeza.execute(
                delete(Agendamento).where(Agendamento.id.in_([a1_id, a2_id]))
            )
            await limpeza.commit()
            assert isinstance(paciente_id, uuid.UUID)
            assert Usuario is not None
