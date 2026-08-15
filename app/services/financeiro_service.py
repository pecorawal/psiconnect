"""Extrato financeiro do profissional.

## Extrato, não saldo

Pelo split da [ADR 0004](../../docs/adr/0004-mercado-pago-split.md), o dinheiro
**não passa pela plataforma**: o pagamento entra direto na conta do profissional
no Mercado Pago, já descontada a comissão. A plataforma nunca fica devendo nada
a ninguém, e o prazo de liberação é do Mercado Pago.

Isso decide a linguagem da tela inteira. "Saldo a receber da plataforma" seria
falso e criaria a expectativa errada — o profissional cobraria de nós um repasse
que nunca vai existir. O que a tela mostra é **o que foi cobrado do paciente e
como aquele valor se repartiu**.

## Recebido não é o mesmo que entregue

Num pacote de 10 sessões o profissional recebe tudo na compra, mas ainda deve
dez atendimentos. Tratar isso como receita realizada esconde trabalho já pago —
se ele sair da plataforma amanhã, deve dez sessões ou a devolução.

Por isso o resumo separa **recebido** de **sessões a entregar**. É a informação
que um extrato bancário não daria e que muda a decisão de quem lê.

## Uma advertência enquanto o OAuth não existe

Sem a conexão da conta (`mp_user_id` vazio), não há split: o dinheiro cai na
conta da plataforma. Os números aqui continuam corretos como *cálculo*, mas a
liquidação não acontece como a tela descreve. O serviço expõe
``split_ativo`` para que a tela possa dizer isso em vez de mentir por omissão.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.tempo import agora_utc, limites_do_mes_local, para_local
from app.models import (
    Agendamento,
    CompraPlano,
    CreditoSessao,
    Especialidade,
    Pagamento,
    PerfilProfissional,
    StatusCompra,
    StatusCredito,
    StatusPagamento,
    Usuario,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResumoFinanceiro:
    """Totais do período, todos em centavos."""

    bruto_centavos: int
    taxa_provedor_centavos: int
    comissao_plataforma_centavos: int
    imposto_retido_centavos: int
    liquido_centavos: int

    #: Cobranças criadas que ainda não foram aprovadas (Pix não pago, por ex.).
    pendente_centavos: int
    estornado_centavos: int

    quantidade_pagamentos: int
    #: Sessões já pagas que ainda não foram atendidas. Trabalho devido.
    sessoes_a_entregar: int

    #: False enquanto o profissional não conectou a conta do Mercado Pago.
    split_ativo: bool

    @property
    def percentual_efetivo(self) -> float:
        """Quanto do bruto ficou retido, somando taxa e comissão.

        É o número que o profissional realmente quer saber, e que nenhuma das
        parcelas isoladas responde.
        """
        if self.bruto_centavos == 0:
            return 0.0
        retido = self.taxa_provedor_centavos + self.comissao_plataforma_centavos
        return round(100 * retido / self.bruto_centavos, 2)


@dataclass(frozen=True, slots=True)
class Movimento:
    """Uma linha do extrato."""

    pagamento_id: uuid.UUID
    data: datetime | None
    paciente_nome: str
    especialidade_nome: str
    quantidade_sessoes: int
    status: StatusPagamento
    bruto_centavos: int
    taxa_provedor_centavos: int
    comissao_centavos: int
    liquido_centavos: int


class FinanceiroService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    def _pagamentos_do_profissional(
        self, profissional_id: uuid.UUID
    ) -> Select[tuple[Pagamento]]:
        return (
            select(Pagamento)
            .join(CompraPlano, Pagamento.compra_plano_id == CompraPlano.id)
            .where(CompraPlano.profissional_id == profissional_id)
        )

    async def resumo(
        self, profissional_id: uuid.UUID, *, ano: int, mes: int
    ) -> ResumoFinanceiro:
        inicio, fim = limites_do_mes_local(ano, mes)

        # O recorte é por `aprovado_em`, não por `criado_em`: o que conta no mês
        # é quando o dinheiro entrou, não quando a cobrança foi gerada. Um Pix
        # criado dia 31 e pago dia 1º pertence ao mês seguinte.
        aprovados = self._pagamentos_do_profissional(profissional_id).where(
            Pagamento.status == StatusPagamento.APROVADO,
            Pagamento.aprovado_em >= inicio,
            Pagamento.aprovado_em < fim,
        )
        linhas = (await self.sessao.scalars(aprovados)).all()

        pendentes = await self.sessao.scalar(
            select(func.coalesce(func.sum(Pagamento.valor_bruto_centavos), 0))
            .select_from(Pagamento)
            .join(CompraPlano, Pagamento.compra_plano_id == CompraPlano.id)
            .where(
                CompraPlano.profissional_id == profissional_id,
                Pagamento.status.in_(
                    (StatusPagamento.PENDENTE, StatusPagamento.CRIADO)
                ),
            )
        )

        estornados = await self.sessao.scalar(
            select(func.coalesce(func.sum(Pagamento.valor_bruto_centavos), 0))
            .select_from(Pagamento)
            .join(CompraPlano, Pagamento.compra_plano_id == CompraPlano.id)
            .where(
                CompraPlano.profissional_id == profissional_id,
                Pagamento.status == StatusPagamento.ESTORNADO,
                Pagamento.atualizado_em >= inicio,
                Pagamento.atualizado_em < fim,
            )
        )

        # Trabalho já pago e ainda não entregue -- não é do mês, é do total:
        # um crédito comprado em janeiro segue devido em março.
        a_entregar = await self.sessao.scalar(
            select(func.count())
            .select_from(CreditoSessao)
            .join(CompraPlano, CreditoSessao.compra_plano_id == CompraPlano.id)
            .where(
                CompraPlano.profissional_id == profissional_id,
                CompraPlano.status == StatusCompra.ATIVA,
                CreditoSessao.status == StatusCredito.DISPONIVEL,
                (CreditoSessao.expira_em.is_(None))
                | (CreditoSessao.expira_em > agora_utc()),
            )
        )

        perfil = await self.sessao.get(PerfilProfissional, profissional_id)

        return ResumoFinanceiro(
            bruto_centavos=sum(p.valor_bruto_centavos for p in linhas),
            taxa_provedor_centavos=sum(p.taxa_provedor_centavos for p in linhas),
            comissao_plataforma_centavos=sum(
                p.comissao_plataforma_centavos for p in linhas
            ),
            imposto_retido_centavos=sum(p.imposto_retido_centavos for p in linhas),
            liquido_centavos=sum(p.liquido_profissional_centavos for p in linhas),
            pendente_centavos=int(pendentes or 0),
            estornado_centavos=int(estornados or 0),
            quantidade_pagamentos=len(linhas),
            sessoes_a_entregar=int(a_entregar or 0),
            split_ativo=bool(perfil and perfil.mp_user_id),
        )

    async def movimentos(
        self, profissional_id: uuid.UUID, *, ano: int, mes: int
    ) -> list[Movimento]:
        """Linhas do extrato do mês, mais recentes primeiro.

        Inclui pendentes e estornados de propósito: um extrato que só mostra o
        que deu certo esconde justamente o que o profissional precisa
        investigar.
        """
        inicio, fim = limites_do_mes_local(ano, mes)

        consulta = (
            select(
                Pagamento.id,
                Pagamento.aprovado_em,
                Pagamento.criado_em,
                Usuario.nome_completo,
                Especialidade.nome,
                CompraPlano.quantidade_total,
                Pagamento.status,
                Pagamento.valor_bruto_centavos,
                Pagamento.taxa_provedor_centavos,
                Pagamento.comissao_plataforma_centavos,
                Pagamento.liquido_profissional_centavos,
            )
            .select_from(Pagamento)
            .join(CompraPlano, Pagamento.compra_plano_id == CompraPlano.id)
            .join(Usuario, CompraPlano.paciente_id == Usuario.id)
            .join(Especialidade, CompraPlano.especialidade_id == Especialidade.id)
            .where(
                CompraPlano.profissional_id == profissional_id,
                # Aprovados entram pelo mês do pagamento; os demais, pelo mês em
                # que a cobrança nasceu -- senão um Pix nunca pago não apareceria
                # em mês nenhum.
                func.coalesce(Pagamento.aprovado_em, Pagamento.criado_em) >= inicio,
                func.coalesce(Pagamento.aprovado_em, Pagamento.criado_em) < fim,
            )
            .order_by(func.coalesce(Pagamento.aprovado_em, Pagamento.criado_em).desc())
        )

        return [
            Movimento(
                pagamento_id=linha[0],
                data=linha[1] or linha[2],
                paciente_nome=linha[3],
                especialidade_nome=linha[4],
                quantidade_sessoes=linha[5],
                status=linha[6],
                bruto_centavos=linha[7],
                taxa_provedor_centavos=linha[8],
                comissao_centavos=linha[9],
                liquido_centavos=linha[10],
            )
            for linha in (await self.sessao.execute(consulta)).all()
        ]

    async def meses_com_movimento(
        self, profissional_id: uuid.UUID
    ) -> list[tuple[int, int]]:
        """``(ano, mês)`` em que houve lançamento, do mais recente ao mais antigo.

        Alimenta o seletor de período: oferecer meses vazios seria convidar o
        profissional a concluir que perdeu dados.
        """
        datas = (
            await self.sessao.scalars(
                select(func.coalesce(Pagamento.aprovado_em, Pagamento.criado_em))
                .select_from(Pagamento)
                .join(CompraPlano, Pagamento.compra_plano_id == CompraPlano.id)
                .where(CompraPlano.profissional_id == profissional_id)
            )
        ).all()

        # Agrupa pelo mês LOCAL: um pagamento das 22h de 31/01 em São Paulo é
        # 01/02 em UTC, e cairia no mês errado se agrupado no banco.
        meses = {(para_local(d).year, para_local(d).month) for d in datas if d}
        atual = para_local(agora_utc())
        meses.add((atual.year, atual.month))
        return sorted(meses, reverse=True)

    async def proximas_sessoes_pagas(
        self, profissional_id: uuid.UUID
    ) -> list[Agendamento]:
        """Sessões confirmadas que já estão pagas — o trabalho contratado."""
        return list(
            (
                await self.sessao.scalars(
                    select(Agendamento)
                    .where(
                        Agendamento.profissional_id == profissional_id,
                        Agendamento.status == "CONFIRMADO",
                        Agendamento.inicio_utc >= agora_utc(),
                    )
                    .order_by(Agendamento.inicio_utc)
                )
            ).all()
        )
