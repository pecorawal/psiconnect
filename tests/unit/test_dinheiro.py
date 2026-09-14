"""Testes de dinheiro.

A invariante que importa: nenhum centavo aparece ou desaparece.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.core.dinheiro import (
    centavos_para_decimal,
    formatar_brl,
    percentual_de,
    ratear,
    reais_para_centavos,
)

pytestmark = pytest.mark.unit


class TestConversao:
    @pytest.mark.parametrize(
        ("entrada", "esperado"),
        [("149,90", 14990), ("1.499,90", 149990), ("0,01", 1), ("150", 15000)],
    )
    def test_reais_para_centavos_formato_br(self, entrada: str, esperado: int) -> None:
        assert reais_para_centavos(entrada) == esperado

    def test_centavos_para_decimal(self) -> None:
        assert centavos_para_decimal(14990) == Decimal("149.90")

    @pytest.mark.parametrize(
        ("centavos", "esperado"),
        [
            (14990, "R$ 149,90"),
            (750, "R$ 7,50"),
            (0, "R$ 0,00"),
            (149990, "R$ 1.499,90"),
            (-750, "-R$ 7,50"),
        ],
    )
    def test_formatar_brl(self, centavos: int, esperado: str) -> None:
        assert formatar_brl(centavos) == esperado


class TestComissao:
    def test_comissao_de_5_por_cento(self) -> None:
        """O caso do plano: 5% sobre R$ 150,00 = R$ 7,50."""
        assert percentual_de(15000, 5) == 750

    def test_arredonda_meio_para_cima(self) -> None:
        # 5% de R$ 149,90 = 749,5 centavos -> 750.
        assert percentual_de(14990, 5) == 750

    def test_float_nao_contamina_o_resultado(self) -> None:
        """0.05 * 14990 em float dá 749.4999999999999 e arredondaria para 749."""
        assert percentual_de(14990, Decimal("5")) == 750


class TestRateio:
    def test_divide_sem_perder_centavo(self) -> None:
        # Pacote de R$ 100,00 em 3 sessões: 3334 + 3333 + 3333.
        partes = ratear(10000, [1, 1, 1])
        assert sum(partes) == 10000
        assert partes == [3334, 3333, 3333]

    def test_rejeita_pesos_vazios(self) -> None:
        with pytest.raises(ValueError, match="pesos"):
            ratear(1000, [])

    @given(
        total=st.integers(min_value=0, max_value=10_000_000),
        pesos=st.lists(st.integers(min_value=1, max_value=50), min_size=1, max_size=12),
    )
    def test_propriedade_soma_sempre_bate(self, total: int, pesos: list[int]) -> None:
        """Para qualquer total e quaisquer pesos, a soma das partes é o total."""
        assert sum(ratear(total, pesos)) == total
