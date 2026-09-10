"""Extrato financeiro: totais, recorte de período e isolamento entre contas.

O que estes testes protegem:

1. **Um profissional ver o faturamento do outro** — vazamento de dado comercial.
2. **Número errado no extrato** — mina a confiança na plataforma inteira.
3. **Lançamento sumir na virada do mês** — o recorte é local, não UTC.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tempo import agora_utc, limites_do_mes_local, para_local
from app.models import (
    CompraPlano,
    CreditoSessao,
    Especialidade,
    MetodoPagamento,
    Pagamento,
    PerfilPaciente,
    PerfilProfissional,
    StatusCompra,
    StatusCredito,
    StatusPagamento,
)
from app.services.financeiro_service import FinanceiroService
from tests.fabricas import criar_especialidade, criar_paciente, criar_profissional


async def _pagamento(
    sessao: AsyncSession,
    paciente: PerfilPaciente,
    profissional: PerfilProfissional,
    especialidade: Especialidade,
    *,
    bruto: int = 15000,
    taxa: int = 149,
    comissao: int = 1800,
    status: StatusPagamento = StatusPagamento.APROVADO,
    aprovado_em: object | None = None,
    quantidade: int = 1,
    creditos_disponiveis: int = 0,
) -> Pagamento:
    from sqlalchemy import select

    from app.models import Plano, SlugPlano

    plano = await sessao.scalar(select(Plano).where(Plano.slug == SlugPlano.AVULSO))
    assert plano is not None

    compra = CompraPlano(
        paciente_id=paciente.usuario_id,
        profissional_id=profissional.usuario_id,
        plano_id=plano.id,
        especialidade_id=especialidade.id,
        quantidade_total=quantidade,
        valor_sessao_centavos=bruto // quantidade,
        valor_total_centavos=bruto,
        percentual_comissao_aplicado=12,
        status=StatusCompra.ATIVA,
    )
    sessao.add(compra)
    await sessao.flush()

    for _ in range(creditos_disponiveis):
        sessao.add(CreditoSessao(compra_plano_id=compra.id, status=StatusCredito.DISPONIVEL))

    pagamento = Pagamento(
        compra_plano_id=compra.id,
        provedor="fake",
        provedor_pagamento_id=f"pag-{uuid.uuid4().hex[:12]}",
        metodo=MetodoPagamento.PIX,
        status=status,
        valor_bruto_centavos=bruto,
        taxa_provedor_centavos=taxa,
        comissao_plataforma_centavos=comissao,
        liquido_profissional_centavos=bruto - taxa - comissao,
        chave_idempotencia=f"compra:{compra.id}",
        aprovado_em=(
            aprovado_em
            if aprovado_em is not None
            else (agora_utc() if status is StatusPagamento.APROVADO else None)
        ),
    )
    sessao.add(pagamento)
    await sessao.flush()
    return pagamento


def _mes_atual() -> tuple[int, int]:
    agora = para_local(agora_utc())
    return agora.year, agora.month


class TestResumo:
    async def test_soma_as_parcelas_sem_perder_centavo(self, sessao: AsyncSession) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _pagamento(sessao, paciente, profissional, especialidade)
        await _pagamento(sessao, paciente, profissional, especialidade)

        ano, mes = _mes_atual()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)

        assert resumo.bruto_centavos == 30000
        assert resumo.comissao_plataforma_centavos == 3600
        assert resumo.taxa_provedor_centavos == 298
        assert resumo.liquido_centavos == 26102
        # A invariante: nada evapora entre bruto e líquido.
        assert (
            resumo.liquido_centavos
            + resumo.comissao_plataforma_centavos
            + resumo.taxa_provedor_centavos
            + resumo.imposto_retido_centavos
            == resumo.bruto_centavos
        )
        assert resumo.quantidade_pagamentos == 2

    async def test_percentual_efetivo(self, sessao: AsyncSession) -> None:
        """O número que o profissional realmente quer: quanto ficou retido."""
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _pagamento(
            sessao, paciente, profissional, especialidade, bruto=10000, taxa=99, comissao=1200
        )

        ano, mes = _mes_atual()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)
        assert resumo.percentual_efetivo == 12.99

    async def test_sem_movimento_nao_divide_por_zero(self, sessao: AsyncSession) -> None:
        profissional = await criar_profissional(sessao)
        ano, mes = _mes_atual()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)
        assert resumo.bruto_centavos == 0
        assert resumo.percentual_efetivo == 0.0

    async def test_pendente_nao_entra_no_recebido(self, sessao: AsyncSession) -> None:
        """Pix não pago não é receita. Contar seria inflar o extrato."""
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _pagamento(
            sessao,
            paciente,
            profissional,
            especialidade,
            status=StatusPagamento.PENDENTE,
        )

        ano, mes = _mes_atual()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)
        assert resumo.bruto_centavos == 0
        assert resumo.pendente_centavos == 15000

    async def test_sessoes_a_entregar(self, sessao: AsyncSession) -> None:
        """Pacote: o dinheiro entrou todo, o trabalho não foi feito.

        Sem este número, o profissional lê o recebido como receita realizada e
        não vê que deve atendimentos.
        """
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)
        await _pagamento(
            sessao,
            paciente,
            profissional,
            especialidade,
            bruto=67500,
            quantidade=5,
            creditos_disponiveis=4,
        )

        ano, mes = _mes_atual()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)
        assert resumo.sessoes_a_entregar == 4

    async def test_split_inativo_sem_conta_conectada(self, sessao: AsyncSession) -> None:
        """A tela precisa avisar que o repasse automático não está valendo."""
        profissional = await criar_profissional(sessao)
        ano, mes = _mes_atual()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)
        assert resumo.split_ativo is False

        profissional.mp_user_id = "123456"
        await sessao.flush()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)
        assert resumo.split_ativo is True


class TestIsolamento:
    async def test_profissional_nao_ve_faturamento_de_outro(self, sessao: AsyncSession) -> None:
        """Vazamento de dado comercial entre concorrentes na mesma plataforma."""
        especialidade = await criar_especialidade(sessao)
        profissional_a = await criar_profissional(sessao)
        profissional_b = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)

        await _pagamento(sessao, paciente, profissional_a, especialidade, bruto=50000)

        ano, mes = _mes_atual()
        servico = FinanceiroService(sessao)

        assert (
            await servico.resumo(profissional_a.usuario_id, ano=ano, mes=mes)
        ).bruto_centavos == 50000
        assert (
            await servico.resumo(profissional_b.usuario_id, ano=ano, mes=mes)
        ).bruto_centavos == 0
        assert await servico.movimentos(profissional_b.usuario_id, ano=ano, mes=mes) == []


class TestPeriodo:
    async def test_pagamento_de_outro_mes_nao_entra(self, sessao: AsyncSession) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)

        # Aprovado 45 dias atrás: garantidamente outro mês.
        await _pagamento(
            sessao,
            paciente,
            profissional,
            especialidade,
            aprovado_em=agora_utc() - timedelta(days=45),
        )

        ano, mes = _mes_atual()
        resumo = await FinanceiroService(sessao).resumo(profissional.usuario_id, ano=ano, mes=mes)
        assert resumo.bruto_centavos == 0

    async def test_recorte_usa_o_mes_local(self, sessao: AsyncSession) -> None:
        """Um pagamento das 23h de 31/01 em São Paulo é 01/02 em UTC.

        Se o recorte fosse por mês UTC, ele apareceria em fevereiro — e o
        profissional veria a receita de janeiro num mês em que não trabalhou.
        """
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)

        # 23h30 local do último dia de janeiro = 02h30 UTC de 1º de fevereiro.
        _, fim_janeiro = limites_do_mes_local(2026, 1)
        instante = fim_janeiro - timedelta(minutes=30)
        assert instante.month == 2, "o instante escolhido é fevereiro em UTC"

        await _pagamento(sessao, paciente, profissional, especialidade, aprovado_em=instante)

        servico = FinanceiroService(sessao)
        janeiro = await servico.resumo(profissional.usuario_id, ano=2026, mes=1)
        fevereiro = await servico.resumo(profissional.usuario_id, ano=2026, mes=2)

        assert janeiro.bruto_centavos == 15000
        assert fevereiro.bruto_centavos == 0

    async def test_virada_de_ano(self, sessao: AsyncSession) -> None:
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)

        inicio_dezembro, _ = limites_do_mes_local(2025, 12)
        await _pagamento(
            sessao,
            paciente,
            profissional,
            especialidade,
            aprovado_em=inicio_dezembro + timedelta(days=15),
        )

        servico = FinanceiroService(sessao)
        assert (
            await servico.resumo(profissional.usuario_id, ano=2025, mes=12)
        ).bruto_centavos == 15000
        assert (await servico.resumo(profissional.usuario_id, ano=2026, mes=1)).bruto_centavos == 0


class TestMovimentos:
    async def test_inclui_pendentes_e_estornados(self, sessao: AsyncSession) -> None:
        """Um extrato que só mostra o que deu certo esconde o que investigar."""
        especialidade = await criar_especialidade(sessao)
        profissional = await criar_profissional(sessao)
        paciente = await criar_paciente(sessao)

        await _pagamento(sessao, paciente, profissional, especialidade)
        await _pagamento(
            sessao, paciente, profissional, especialidade, status=StatusPagamento.PENDENTE
        )
        await _pagamento(
            sessao,
            paciente,
            profissional,
            especialidade,
            status=StatusPagamento.RECUSADO,
        )

        ano, mes = _mes_atual()
        movimentos = await FinanceiroService(sessao).movimentos(
            profissional.usuario_id, ano=ano, mes=mes
        )
        assert len(movimentos) == 3
        assert {m.status for m in movimentos} == {
            StatusPagamento.APROVADO,
            StatusPagamento.PENDENTE,
            StatusPagamento.RECUSADO,
        }

    async def test_meses_com_movimento_inclui_o_atual(self, sessao: AsyncSession) -> None:
        """O seletor sempre oferece o mês corrente, mesmo sem lançamento."""
        profissional = await criar_profissional(sessao)
        meses = await FinanceiroService(sessao).meses_com_movimento(profissional.usuario_id)
        assert _mes_atual() in meses
