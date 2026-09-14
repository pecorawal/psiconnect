"""Planos: avulso, pacote de 5 e pacote de 10 sessões."""

from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Plano, SlugPlano


class Def(NamedTuple):
    slug: SlugPlano
    nome: str
    descricao: str
    quantidade: int
    validade_dias: int
    desconto: Decimal
    ordem: int


PLANOS: tuple[Def, ...] = (
    Def(
        SlugPlano.AVULSO,
        "Sessão avulsa",
        "Uma sessão, sem compromisso de continuidade.",
        quantidade=1,
        validade_dias=90,
        desconto=Decimal("0"),
        ordem=1,
    ),
    Def(
        SlugPlano.PACOTE_5,
        "Pacote de 5 sessões",
        "Cinco sessões com o mesmo profissional, com desconto.",
        quantidade=5,
        validade_dias=180,
        desconto=Decimal("5"),
        ordem=2,
    ),
    Def(
        SlugPlano.PACOTE_10,
        "Pacote de 10 sessões",
        "Dez sessões com o mesmo profissional, com o melhor desconto.",
        quantidade=10,
        validade_dias=365,
        desconto=Decimal("10"),
        ordem=3,
    ),
)


async def semear_planos(sessao: AsyncSession) -> int:
    existentes = set((await sessao.execute(select(Plano.slug))).scalars().all())
    novos = 0
    for d in PLANOS:
        if d.slug in existentes:
            continue
        sessao.add(
            Plano(
                slug=d.slug,
                nome=d.nome,
                descricao=d.descricao,
                quantidade_sessoes=d.quantidade,
                validade_dias=d.validade_dias,
                desconto_percentual=d.desconto,
                ordem_exibicao=d.ordem,
            )
        )
        novos += 1
    await sessao.flush()
    return novos
