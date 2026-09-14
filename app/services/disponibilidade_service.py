"""Disponibilidade do profissional e cálculo de slots livres."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import DisponibilidadeSobreposta, JanelaInvalida, NaoEncontrado
from app.core.tempo import TZ_BR, agora_utc, combinar_local
from app.models import (
    STATUS_OCUPAM_AGENDA,
    Agendamento,
    BloqueioAgenda,
    DisponibilidadeRecorrente,
    PerfilProfissional,
)
from app.services.regras.slots import (
    Intervalo,
    RegraDisponibilidade,
    Slot,
    dias_entre,
    gerar_slots,
)

#: Passo entre slots oferecidos. 60 min com sessão de 50 deixa 10 de intervalo.
PASSO_PADRAO_MIN = 60
#: Horizonte de busca exibido ao paciente.
JANELA_BUSCA_DIAS = 21


class DisponibilidadeService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    # --- CRUD da regra ------------------------------------------------------

    async def listar(self, profissional_id: uuid.UUID) -> list[DisponibilidadeRecorrente]:
        return list(
            (
                await self.sessao.execute(
                    select(DisponibilidadeRecorrente)
                    .where(
                        DisponibilidadeRecorrente.profissional_id == profissional_id,
                        DisponibilidadeRecorrente.ativo.is_(True),
                    )
                    .order_by(
                        DisponibilidadeRecorrente.dia_semana,
                        DisponibilidadeRecorrente.inicio_min,
                    )
                )
            )
            .scalars()
            .all()
        )

    async def adicionar(
        self,
        profissional_id: uuid.UUID,
        *,
        dia_semana: int,
        inicio_min: int,
        fim_min: int,
        vigencia_inicio: date | None = None,
    ) -> DisponibilidadeRecorrente:
        if fim_min <= inicio_min:
            raise JanelaInvalida(campo="fim")

        disp = DisponibilidadeRecorrente(
            profissional_id=profissional_id,
            dia_semana=dia_semana,
            inicio_min=inicio_min,
            fim_min=fim_min,
            vigencia_inicio=vigencia_inicio or agora_utc().date(),
        )
        self.sessao.add(disp)
        try:
            await self.sessao.flush()
        except IntegrityError as exc:
            await self.sessao.rollback()
            # A constraint EXCLUDE é quem de fato decide; aqui só traduzimos.
            if "ex_disponibilidade_sem_sobreposicao" in str(exc.orig):
                raise DisponibilidadeSobreposta() from exc
            raise
        return disp

    async def remover(self, profissional_id: uuid.UUID, disponibilidade_id: uuid.UUID) -> None:
        """Desativa em vez de apagar.

        Agendamentos existentes apontam para a disponibilidade que os originou;
        manter a linha (inativa) preserva esse rastro. E a EXCLUDE só considera
        `WHERE ativo`, então o horário fica livre de imediato.
        """
        disp = await self.sessao.scalar(
            select(DisponibilidadeRecorrente).where(
                DisponibilidadeRecorrente.id == disponibilidade_id,
                DisponibilidadeRecorrente.profissional_id == profissional_id,
            )
        )
        if disp is None:
            raise NaoEncontrado("Horário não encontrado na sua agenda.")
        disp.ativo = False
        await self.sessao.flush()

    # --- Slots --------------------------------------------------------------

    async def slots_disponiveis(
        self,
        profissional: PerfilProfissional,
        *,
        duracao_min: int,
        de: date | None = None,
        ate: date | None = None,
        passo_min: int = PASSO_PADRAO_MIN,
    ) -> list[Slot]:
        """Expande a regra e subtrai bloqueios e horários ocupados.

        Nada disso é persistido (ADR 0006): o cálculo é feito na hora, sobre um
        horizonte curto.
        """
        tz = TZ_BR
        hoje = agora_utc().astimezone(tz).date()
        de = de or hoje
        ate = ate or (de + timedelta(days=JANELA_BUSCA_DIAS))

        regras = [
            RegraDisponibilidade(
                id=d.id,
                dia_semana=d.dia_semana,
                inicio_min=d.inicio_min,
                fim_min=d.fim_min,
                vigencia_inicio=d.vigencia_inicio,
                vigencia_fim=d.vigencia_fim,
            )
            for d in await self.listar(profissional.usuario_id)
        ]
        if not regras:
            return []

        inicio_janela = combinar_local(de, 0, tz)
        fim_janela = combinar_local(ate + timedelta(days=1), 0, tz)

        ocupados = [
            Intervalo(a.inicio_utc, a.fim_utc)
            for a in (
                await self.sessao.execute(
                    select(Agendamento).where(
                        Agendamento.profissional_id == profissional.usuario_id,
                        Agendamento.status.in_(STATUS_OCUPAM_AGENDA),
                        Agendamento.fim_utc > inicio_janela,
                        Agendamento.inicio_utc < fim_janela,
                    )
                )
            )
            .scalars()
            .all()
        ]

        bloqueios = [
            Intervalo(b.inicio_utc, b.fim_utc)
            for b in (
                await self.sessao.execute(
                    select(BloqueioAgenda).where(
                        BloqueioAgenda.profissional_id == profissional.usuario_id,
                        BloqueioAgenda.fim_utc > inicio_janela,
                        BloqueioAgenda.inicio_utc < fim_janela,
                    )
                )
            )
            .scalars()
            .all()
        ]

        # Antecedência mínima: ninguém marca para daqui a 5 minutos.
        minimo = agora_utc() + timedelta(hours=profissional.antecedencia_minima_agendamento_h)

        return gerar_slots(
            regras,
            dias_entre(de, ate),
            duracao_min=duracao_min,
            passo_min=passo_min,
            ocupados=ocupados,
            bloqueios=bloqueios,
            a_partir_de=minimo,
            tz=tz,
        )
