"""Dinheiro.

Regra do projeto: **dinheiro é sempre ``int`` em centavos**. Nunca ``float``.

O spike anterior usava ``Float`` para pontuação e não modelava valores, mas o
erro clássico aparece assim que existe comissão: ``0.05 * 14990`` em float dá
``749.4999999999999``, e o arredondamento vira uma discussão com o profissional
sobre um centavo. Em centavos inteiros o problema não existe, e o arredondamento
das divisões é explícito via ``Decimal``.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

Centavos = int
"""Alias semântico. Todo campo monetário do domínio usa este tipo."""

CEM = Decimal(100)


def reais_para_centavos(valor: str | Decimal | int) -> Centavos:
    """``'149,90'`` ou ``Decimal('149.90')`` -> ``14990``."""
    if isinstance(valor, str):
        valor = Decimal(valor.strip().replace(".", "").replace(",", "."))
    return int((Decimal(valor) * CEM).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def centavos_para_decimal(centavos: Centavos) -> Decimal:
    """``14990`` -> ``Decimal('149.90')``."""
    return (Decimal(centavos) / CEM).quantize(Decimal("0.01"))


def formatar_brl(centavos: Centavos) -> str:
    """``14990`` -> ``'R$ 149,90'``. Formatação pt-BR sem depender de locale."""
    negativo = centavos < 0
    inteiro, resto = divmod(abs(centavos), 100)
    milhares = f"{inteiro:,}".replace(",", ".")
    return f"{'-' if negativo else ''}R$ {milhares},{resto:02d}"


def percentual_de(centavos: Centavos, percentual: Decimal | float | str) -> Centavos:
    """Aplica um percentual sobre um valor, arredondando meio-para-cima.

    ``percentual_de(15000, 12)`` -> ``1800`` (12% de R$ 150,00 = R$ 18,00).
    """
    pct = Decimal(str(percentual))
    bruto = (Decimal(centavos) * pct) / CEM
    return int(bruto.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def ratear(total: Centavos, pesos: list[int]) -> list[Centavos]:
    """Divide ``total`` proporcionalmente a ``pesos`` sem perder centavos.

    O resto da divisão inteira é distribuído nas primeiras parcelas, de modo que
    ``sum(ratear(t, p)) == t`` sempre. Usado para dividir o valor de um pacote
    entre as sessões que o compõem.
    """
    if not pesos or (soma := sum(pesos)) <= 0:
        raise ValueError("pesos deve ser uma lista não vazia de inteiros positivos")
    partes = [total * peso // soma for peso in pesos]
    resto = total - sum(partes)
    for i in range(resto):
        partes[i % len(partes)] += 1
    return partes
