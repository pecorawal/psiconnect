"""Regras puras: limites, slots e precificação."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.erros import LimiteHorasDiaExcedido, LimiteSessoesSemanaExcedido
from app.models.enums import MetodoPagamento
from app.services.regras.limites import (
    JanelaAgendada,
    cabe_no_dia,
    minutos_agendados,
    valida_limite_horas_dia,
    valida_limite_sessoes_semana,
)
from app.services.regras.precificacao import (
    TabelaTaxas,
    calcular_reparticao,
    simular_todos_metodos,
)
from app.services.regras.slots import (
    Intervalo,
    RegraDisponibilidade,
    dias_entre,
    expandir_regra,
    gerar_slots,
)

pytestmark = pytest.mark.unit


def janela(hora: int, minutos: int = 50) -> JanelaAgendada:
    inicio = datetime(2026, 8, 12, hora, 0, tzinfo=UTC)
    return JanelaAgendada(inicio, inicio + timedelta(minutes=minutos))


class TestLimiteHorasDia:
    """R1 — o limite é de HORAS, não de número de consultas."""

    def test_dez_consultas_de_50min_cabem_em_10h(self) -> None:
        """O bug do spike: ele bloqueava aqui.

        10 consultas de 50 min somam 8h20 — muito abaixo de 10h. Contar linhas
        em vez de minutos rejeitava um agendamento perfeitamente válido.
        """
        ja_agendadas = [janela(9 + i) for i in range(10)]
        assert minutos_agendados(ja_agendadas) == 500  # 8h20
        valida_limite_horas_dia(ja_agendadas, 50, limite_horas=10)

    def test_sessoes_longas_estouram_antes_de_dez_consultas(self) -> None:
        """O outro lado do mesmo bug: 7 sessões de 90 min já passam de 10h."""
        ja_agendadas = [janela(8 + i, minutos=90) for i in range(7)]
        assert minutos_agendados(ja_agendadas) == 630
        with pytest.raises(LimiteHorasDiaExcedido):
            valida_limite_horas_dia(ja_agendadas, 90, limite_horas=10)

    def test_exatamente_no_limite_e_permitido(self) -> None:
        ja = [janela(9 + i, minutos=60) for i in range(9)]  # 9h
        valida_limite_horas_dia(ja, 60, limite_horas=10)  # fecha 10h

    def test_um_minuto_alem_do_limite_e_bloqueado(self) -> None:
        ja = [janela(9 + i, minutos=60) for i in range(9)]
        with pytest.raises(LimiteHorasDiaExcedido) as exc:
            valida_limite_horas_dia(ja, 61, limite_horas=10)
        assert exc.value.detalhes["minutos_restantes"] == 60

    def test_agenda_vazia(self) -> None:
        valida_limite_horas_dia([], 50, limite_horas=10)

    def test_cabe_no_dia_e_a_versao_booleana(self) -> None:
        ja = [janela(9 + i, minutos=60) for i in range(9)]
        assert cabe_no_dia(ja, 60, 10) is True
        assert cabe_no_dia(ja, 61, 10) is False


class TestLimiteSessoesSemana:
    """R2 — máximo de 3 sessões por semana por paciente."""

    @pytest.mark.parametrize("ja_marcadas", [0, 1, 2])
    def test_ate_o_limite(self, ja_marcadas: int) -> None:
        valida_limite_sessoes_semana(ja_marcadas, limite=3)

    def test_a_quarta_e_bloqueada(self) -> None:
        with pytest.raises(LimiteSessoesSemanaExcedido, match="limite é 3"):
            valida_limite_sessoes_semana(3, limite=3)

    def test_mensagem_no_singular(self) -> None:
        with pytest.raises(LimiteSessoesSemanaExcedido, match="1 sessão marcada"):
            valida_limite_sessoes_semana(1, limite=1)

    def test_marcar_varias_de_uma_vez_respeita_o_limite(self) -> None:
        """A sugestão de recorrência (R3) não pode furar o limite semanal."""
        with pytest.raises(LimiteSessoesSemanaExcedido):
            valida_limite_sessoes_semana(2, limite=3, adicionais=2)


class TestExpansaoDeSlots:
    def test_janela_de_3h_com_sessao_de_50min_e_passo_de_60(self) -> None:
        """09:00–12:00 rende 09:00, 10:00 e 11:00.

        11:30 não entra: terminaria 12:20, fora da janela.
        """
        regra = RegraDisponibilidade(
            id="r1",
            dia_semana=2,
            inicio_min=540,
            fim_min=720,
            vigencia_inicio=date(2020, 1, 1),
        )
        slots = expandir_regra(regra, date(2026, 8, 12), duracao_min=50, passo_min=60)
        assert len(slots) == 3

    def test_ignora_dia_da_semana_diferente(self) -> None:
        regra = RegraDisponibilidade(
            id="r1",
            dia_semana=0,
            inicio_min=540,
            fim_min=720,
            vigencia_inicio=date(2020, 1, 1),
        )
        # 2026-08-12 é uma quarta (weekday 2), a regra é de segunda.
        assert expandir_regra(regra, date(2026, 8, 12), 50, 60) == []

    def test_respeita_vigencia(self) -> None:
        regra = RegraDisponibilidade(
            id="r1",
            dia_semana=2,
            inicio_min=540,
            fim_min=720,
            vigencia_inicio=date(2026, 9, 1),
        )
        assert expandir_regra(regra, date(2026, 8, 12), 50, 60) == []

    def test_ocupados_removem_slots(self) -> None:
        regra = RegraDisponibilidade(
            id="r1",
            dia_semana=2,
            inicio_min=540,
            fim_min=720,
            vigencia_inicio=date(2020, 1, 1),
        )
        dia = date(2026, 8, 12)
        todos = gerar_slots([regra], [dia], duracao_min=50, passo_min=60)
        ocupado = Intervalo(todos[1].inicio_utc, todos[1].fim_utc)
        livres = gerar_slots([regra], [dia], duracao_min=50, passo_min=60, ocupados=[ocupado])
        assert len(livres) == len(todos) - 1
        assert all(s.inicio_utc != ocupado.inicio_utc for s in livres)

    def test_bloqueio_remove_o_dia_inteiro(self) -> None:
        regra = RegraDisponibilidade(
            id="r1",
            dia_semana=2,
            inicio_min=540,
            fim_min=720,
            vigencia_inicio=date(2020, 1, 1),
        )
        dia = date(2026, 8, 12)
        ferias = Intervalo(
            datetime(2026, 8, 12, 0, 0, tzinfo=UTC), datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
        )
        assert gerar_slots([regra], [dia], duracao_min=50, passo_min=60, bloqueios=[ferias]) == []

    def test_a_partir_de_corta_o_passado(self) -> None:
        regra = RegraDisponibilidade(
            id="r1",
            dia_semana=2,
            inicio_min=540,
            fim_min=720,
            vigencia_inicio=date(2020, 1, 1),
        )
        dia = date(2026, 8, 12)
        todos = gerar_slots([regra], [dia], duracao_min=50, passo_min=60)
        corte = todos[1].inicio_utc
        restantes = gerar_slots([regra], [dia], duracao_min=50, passo_min=60, a_partir_de=corte)
        assert len(restantes) == len(todos) - 1

    def test_slots_saem_ordenados(self) -> None:
        manha = RegraDisponibilidade(
            id="m", dia_semana=2, inicio_min=540, fim_min=720, vigencia_inicio=date(2020, 1, 1)
        )
        tarde = RegraDisponibilidade(
            id="t", dia_semana=2, inicio_min=840, fim_min=1080, vigencia_inicio=date(2020, 1, 1)
        )
        # Tarde primeiro na entrada, para provar que a ordenação é do resultado.
        slots = gerar_slots([tarde, manha], [date(2026, 8, 12)], duracao_min=50, passo_min=60)
        assert slots == sorted(slots, key=lambda s: s.inicio_utc)

    def test_sessoes_consecutivas_nao_se_sobrepoem(self) -> None:
        """Fim exclusivo: 10:00–10:50 e 10:50–11:40 convivem.

        Espelha o range '[)' das constraints EXCLUDE do Postgres -- se as duas
        definições divergissem, a UI ofereceria slots que o banco rejeitaria.
        """
        a = Intervalo(
            datetime(2026, 8, 12, 10, 0, tzinfo=UTC), datetime(2026, 8, 12, 10, 50, tzinfo=UTC)
        )
        b = Intervalo(
            datetime(2026, 8, 12, 10, 50, tzinfo=UTC), datetime(2026, 8, 12, 11, 40, tzinfo=UTC)
        )
        assert not a.sobrepoe(b)
        assert not b.sobrepoe(a)

    def test_dias_entre(self) -> None:
        assert len(dias_entre(date(2026, 8, 10), date(2026, 8, 16))) == 7


class TestPrecificacao:
    """R9 — a repartição fecha sempre."""

    def test_caso_do_plano(self) -> None:
        """R$ 150,00 no Pix com comissão de 5%."""
        r = calcular_reparticao(15000, percentual_comissao=Decimal("5"), metodo=MetodoPagamento.PIX)
        assert r.comissao_plataforma_centavos == 750  # R$ 7,50
        assert r.taxa_provedor_centavos == 149  # 0,99%
        assert r.liquido_profissional_centavos == 15000 - 750 - 149

    def test_credito_custa_mais_que_a_comissao(self) -> None:
        """No crédito a taxa (~4,98%) quase iguala a comissão de 5%.

        É o dado que sustenta a questão aberta sobre quem absorve a taxa.
        """
        pix = calcular_reparticao(
            15000, percentual_comissao=Decimal("5"), metodo=MetodoPagamento.PIX
        )
        credito = calcular_reparticao(
            15000, percentual_comissao=Decimal("5"), metodo=MetodoPagamento.CARTAO_CREDITO
        )
        assert credito.taxa_provedor_centavos > pix.taxa_provedor_centavos
        assert credito.percentual_efetivo > Decimal("9")

    def test_comissao_absurda_nao_gera_liquido_negativo(self) -> None:
        r = calcular_reparticao(
            10000, percentual_comissao=Decimal("99"), metodo=MetodoPagamento.CARTAO_CREDITO
        )
        assert r.liquido_profissional_centavos >= 0

    def test_simulador_cobre_todos_os_metodos(self) -> None:
        simulacao = simular_todos_metodos(15000, percentual_comissao=Decimal("5"))
        assert set(simulacao) == set(MetodoPagamento)

    @given(
        bruto=st.integers(min_value=0, max_value=5_000_00),
        comissao=st.decimals(min_value=0, max_value=30, places=2),
        imposto=st.decimals(min_value=0, max_value=15, places=2),
        metodo=st.sampled_from(list(MetodoPagamento)),
    )
    def test_propriedade_a_reparticao_sempre_fecha(
        self, bruto: int, comissao: Decimal, imposto: Decimal, metodo: MetodoPagamento
    ) -> None:
        """Para qualquer valor e percentual, nenhum centavo some ou apareça.

        O ``__post_init__`` levanta AssertionError se a soma não bater, então
        construir o objeto já é o teste.
        """
        r = calcular_reparticao(
            bruto,
            percentual_comissao=comissao,
            metodo=metodo,
            percentual_imposto=imposto,
            tabela=TabelaTaxas(),
        )
        assert (
            r.taxa_provedor_centavos
            + r.comissao_plataforma_centavos
            + r.imposto_retido_centavos
            + r.liquido_profissional_centavos
            == bruto
        )
