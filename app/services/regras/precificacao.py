"""Repartição do valor pago — R9.

A invariante que rege tudo aqui:

    taxa_provedor + comissao_plataforma + imposto_retido + liquido == bruto

Nenhum centavo criado, nenhum perdido. Testada por propriedade em
``tests/unit/test_precificacao.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.core.dinheiro import Centavos, percentual_de
from app.models.enums import MetodoPagamento


@dataclass(frozen=True, slots=True)
class TabelaTaxas:
    """Taxas do meio de pagamento, em percentual."""

    pix: Decimal = Decimal("0.99")
    debito: Decimal = Decimal("1.99")
    credito: Decimal = Decimal("4.98")

    def para(self, metodo: MetodoPagamento) -> Decimal:
        return {
            MetodoPagamento.PIX: self.pix,
            MetodoPagamento.CARTAO_DEBITO: self.debito,
            MetodoPagamento.CARTAO_CREDITO: self.credito,
        }[metodo]


@dataclass(frozen=True, slots=True)
class ReparticaoValores:
    bruto_centavos: Centavos
    taxa_provedor_centavos: Centavos
    comissao_plataforma_centavos: Centavos
    imposto_retido_centavos: Centavos
    liquido_profissional_centavos: Centavos
    percentual_comissao: Decimal
    percentual_taxa: Decimal

    def __post_init__(self) -> None:
        soma = (
            self.taxa_provedor_centavos
            + self.comissao_plataforma_centavos
            + self.imposto_retido_centavos
            + self.liquido_profissional_centavos
        )
        if soma != self.bruto_centavos:
            raise AssertionError(
                f"repartição não fecha: {soma} != {self.bruto_centavos}. "
                "Isto é um bug de arredondamento, não uma condição de negócio."
            )

    @property
    def custo_total_centavos(self) -> Centavos:
        """O que sai do bolso do profissional."""
        return self.bruto_centavos - self.liquido_profissional_centavos

    @property
    def percentual_efetivo(self) -> Decimal:
        """Quanto o profissional realmente perde, somando tudo."""
        if self.bruto_centavos == 0:
            return Decimal(0)
        return (Decimal(self.custo_total_centavos) * 100 / Decimal(self.bruto_centavos)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )


def calcular_reparticao(
    bruto_centavos: Centavos,
    *,
    percentual_comissao: Decimal,
    metodo: MetodoPagamento,
    tabela: TabelaTaxas | None = None,
    percentual_imposto: Decimal = Decimal(0),
) -> ReparticaoValores:
    """Divide o valor pago entre taxa, comissão, imposto e líquido.

    O líquido é calculado **por subtração**, não por percentual: é assim que a
    invariante fecha sem sobrar centavo. Qualquer resíduo de arredondamento fica
    do lado da plataforma, nunca tirando do profissional.
    """
    if bruto_centavos < 0:
        raise ValueError("valor bruto não pode ser negativo")

    tabela = tabela or TabelaTaxas()
    percentual_taxa = tabela.para(metodo)

    taxa = percentual_de(bruto_centavos, percentual_taxa)
    comissao = percentual_de(bruto_centavos, percentual_comissao)
    imposto = percentual_de(bruto_centavos, percentual_imposto)

    liquido = bruto_centavos - taxa - comissao - imposto
    if liquido < 0:
        # Acontece de verdade: no crédito a taxa (~4,98%) somada a uma comissão
        # de 5% já passa de 10%; com comissão alta o líquido poderia zerar. Em
        # vez de gerar valor negativo, a plataforma absorve o excedente.
        comissao = max(0, comissao + liquido)
        liquido = bruto_centavos - taxa - comissao - imposto
        if liquido < 0:  # taxa+imposto sozinhos já passaram do bruto
            imposto = max(0, imposto + liquido)
            liquido = bruto_centavos - taxa - comissao - imposto

    return ReparticaoValores(
        bruto_centavos=bruto_centavos,
        taxa_provedor_centavos=taxa,
        comissao_plataforma_centavos=comissao,
        imposto_retido_centavos=imposto,
        liquido_profissional_centavos=max(0, liquido),
        percentual_comissao=percentual_comissao,
        percentual_taxa=percentual_taxa,
    )


def simular_todos_metodos(
    bruto_centavos: Centavos,
    *,
    percentual_comissao: Decimal,
    tabela: TabelaTaxas | None = None,
) -> dict[MetodoPagamento, ReparticaoValores]:
    """Alimenta a tela ``/profissional/simulador``.

    Cumpre o requisito do mapa mental: "a plataforma informa sobre os custos
    adicionais com pagamento por cartão e impostos, que são descontados do
    valor" -- antes de o profissional definir o preço, não depois.
    """
    return {
        metodo: calcular_reparticao(
            bruto_centavos,
            percentual_comissao=percentual_comissao,
            metodo=metodo,
            tabela=tabela,
        )
        for metodo in MetodoPagamento
    }
