"""Créditos de sessão já comprados.

Existe porque os pacotes não funcionavam: `PACOTE_10` criava dez créditos,
consumia um na compra e deixava nove órfãos — não havia caminho no código para
gastá-los. Quem comprasse dez sessões marcava uma e pagaria de novo pelas
outras nove.

## Um crédito serve para quê

Um crédito vale para **o mesmo profissional e a mesma especialidade** da compra.
A amarração ao profissional já estava no modelo (senão o paciente compraria dez
sessões com o mais barato e as usaria com o mais caro); a da especialidade vem
do mesmo raciocínio, porque o preço varia por especialidade dentro do mesmo
profissional.

## Ordem de consumo

Sempre o **que vence primeiro**. O contrário deixaria o paciente perder crédito
válido enquanto gasta um de validade longa — e a reclamação seria justa.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import ErroDominio
from app.core.logging import get_logger
from app.core.tempo import agora_utc
from app.models import (
    Agendamento,
    CompraPlano,
    CreditoSessao,
    StatusCompra,
    StatusCredito,
)

log = get_logger(__name__)


class SemCreditoDisponivel(ErroDominio):
    codigo = "sem_credito"
    mensagem_padrao = "Você não tem sessões disponíveis para este profissional."


@dataclass(frozen=True, slots=True)
class SaldoCreditos:
    """Quantos créditos e até quando, para uma combinação profissional+especialidade."""

    compra_id: uuid.UUID
    profissional_id: uuid.UUID
    profissional_nome: str
    especialidade_id: uuid.UUID
    especialidade_nome: str
    disponiveis: int
    expira_em: datetime | None


class CreditoService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    # --- Consulta -----------------------------------------------------------

    def _base_disponiveis(self, paciente_id: uuid.UUID) -> Select[tuple[CreditoSessao]]:
        """Créditos que o paciente pode gastar agora.

        Três condições, todas necessárias: o crédito está disponível, a compra
        está ativa (um estorno cancela a compra inteira) e nada venceu. Filtrar
        vencimento no SQL, e não em Python, evita mostrar saldo que some no
        clique seguinte.
        """
        agora = agora_utc()
        return (
            select(CreditoSessao)
            .join(CompraPlano, CreditoSessao.compra_plano_id == CompraPlano.id)
            .where(
                CompraPlano.paciente_id == paciente_id,
                CompraPlano.status == StatusCompra.ATIVA,
                CreditoSessao.status == StatusCredito.DISPONIVEL,
                (CreditoSessao.expira_em.is_(None)) | (CreditoSessao.expira_em > agora),
            )
        )

    async def saldo(self, paciente_id: uuid.UUID) -> list[SaldoCreditos]:
        """Saldo agrupado, para o painel e para a tela de agendamento."""
        from app.models import Especialidade, PerfilProfissional, Usuario

        consulta = (
            select(
                CompraPlano.id,
                CompraPlano.profissional_id,
                Usuario.nome_completo,
                CompraPlano.especialidade_id,
                Especialidade.nome,
                func.count(CreditoSessao.id),
                func.min(CreditoSessao.expira_em),
            )
            .select_from(CreditoSessao)
            .join(CompraPlano, CreditoSessao.compra_plano_id == CompraPlano.id)
            .join(
                PerfilProfissional,
                CompraPlano.profissional_id == PerfilProfissional.usuario_id,
            )
            .join(Usuario, PerfilProfissional.usuario_id == Usuario.id)
            .join(Especialidade, CompraPlano.especialidade_id == Especialidade.id)
            .where(
                CompraPlano.paciente_id == paciente_id,
                CompraPlano.status == StatusCompra.ATIVA,
                CreditoSessao.status == StatusCredito.DISPONIVEL,
                (CreditoSessao.expira_em.is_(None))
                | (CreditoSessao.expira_em > agora_utc()),
            )
            .group_by(
                CompraPlano.id,
                CompraPlano.profissional_id,
                Usuario.nome_completo,
                CompraPlano.especialidade_id,
                Especialidade.nome,
            )
            .order_by(func.min(CreditoSessao.expira_em))
        )
        linhas = (await self.sessao.execute(consulta)).all()
        return [
            SaldoCreditos(
                compra_id=linha[0],
                profissional_id=linha[1],
                profissional_nome=linha[2],
                especialidade_id=linha[3],
                especialidade_nome=linha[4],
                disponiveis=linha[5],
                expira_em=linha[6],
            )
            for linha in linhas
        ]

    async def tem_credito_para(
        self,
        paciente_id: uuid.UUID,
        profissional_id: uuid.UUID,
        especialidade_id: uuid.UUID,
    ) -> int:
        """Quantos créditos servem para este profissional e especialidade."""
        consulta = self._base_disponiveis(paciente_id).where(
            CompraPlano.profissional_id == profissional_id,
            CompraPlano.especialidade_id == especialidade_id,
        )
        total = await self.sessao.scalar(
            select(func.count()).select_from(consulta.subquery())
        )
        return int(total or 0)

    # --- Consumo ------------------------------------------------------------

    async def consumir(self, agendamento: Agendamento) -> CreditoSessao:
        """Gasta um crédito e confirma o agendamento.

        `with_for_update(skip_locked=True)` é o que impede duas requisições
        simultâneas gastarem o mesmo crédito: a segunda ignora a linha travada
        e pega a próxima, ou não acha nenhuma e recebe erro — em vez de as duas
        marcarem sessão com um crédito só.
        """
        credito = await self.sessao.scalar(
            self._base_disponiveis(agendamento.paciente_id)
            .where(
                CompraPlano.profissional_id == agendamento.profissional_id,
                CompraPlano.especialidade_id == agendamento.especialidade_id,
            )
            # Vence primeiro, gasta primeiro.
            .order_by(CreditoSessao.expira_em.asc().nullslast(), CreditoSessao.criado_em)
            .limit(1)
            .with_for_update(skip_locked=True, of=CreditoSessao)
        )
        if credito is None:
            raise SemCreditoDisponivel()

        credito.status = StatusCredito.CONSUMIDO
        credito.agendamento_id = agendamento.id
        credito.consumido_em = agora_utc()
        await self.sessao.flush()

        log.info(
            "credito.consumido",
            credito_id=str(credito.id),
            agendamento_id=str(agendamento.id),
        )
        return credito

    async def devolver(self, agendamento_id: uuid.UUID) -> CreditoSessao | None:
        """Devolve o crédito de um agendamento cancelado.

        Só devolve se o crédito ainda não venceu. Um crédito vencido que volta
        para `DISPONIVEL` apareceria no saldo sem poder ser usado -- pior do que
        não aparecer.
        """
        credito = await self.sessao.scalar(
            select(CreditoSessao).where(
                CreditoSessao.agendamento_id == agendamento_id,
                CreditoSessao.status == StatusCredito.CONSUMIDO,
            )
        )
        if credito is None:
            return None

        if credito.expira_em is not None and credito.expira_em <= agora_utc():
            credito.status = StatusCredito.EXPIRADO
            credito.agendamento_id = None
            await self.sessao.flush()
            log.info("credito.devolvido_ja_vencido", credito_id=str(credito.id))
            return credito

        credito.status = StatusCredito.DISPONIVEL
        credito.agendamento_id = None
        credito.consumido_em = None
        await self.sessao.flush()
        log.info("credito.devolvido", credito_id=str(credito.id))
        return credito

    async def expirar_vencidos(self) -> int:
        """Marca como EXPIRADO o que passou da validade. Chamado pelo worker.

        Sem isto, um crédito vencido continuaria com status `DISPONIVEL` no
        banco. As consultas já filtram por data, então nada seria vendido duas
        vezes -- mas relatório e conciliação leriam o número errado.
        """
        vencidos = (
            await self.sessao.scalars(
                select(CreditoSessao)
                .where(
                    CreditoSessao.status == StatusCredito.DISPONIVEL,
                    CreditoSessao.expira_em.is_not(None),
                    CreditoSessao.expira_em <= agora_utc(),
                )
                .with_for_update(skip_locked=True)
            )
        ).all()

        for credito in vencidos:
            credito.status = StatusCredito.EXPIRADO

        if vencidos:
            await self.sessao.flush()
            log.info("credito.expirados", quantidade=len(vencidos))
        return len(vencidos)
