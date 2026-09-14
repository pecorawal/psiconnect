"""Reserva de horário — R1, R2 e R6 juntos.

O ponto delicado deste módulo é **concorrência**.

``EXCLUDE USING gist`` resolve R6 (sobreposição) de forma definitiva: é o banco
quem decide, e nem um INSERT manual fura. Mas R1 (10h/dia) e R2 (3 sessões por
semana) são restrições **agregadas** -- dependem de uma soma sobre linhas
existentes. Duas transações simultâneas podem ambas ler "9h30 agendadas" e ambas
inserir 50 min, resultando em 10h20. Nenhuma constraint declarativa cobre isso.

A solução é tomar ``pg_advisory_xact_lock`` antes de ler, sempre na mesma ordem
(profissional, depois paciente) para não deadlockar. Os locks caem sozinhos no
fim da transação.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import (
    AgendamentoNoPassado,
    AntecedenciaInsuficiente,
    NaoEncontrado,
    SlotIndisponivel,
)
from app.core.logging import get_logger
from app.core.tempo import (
    TZ_BR,
    agora_utc,
    dia_local,
    limites_da_semana_local,
    limites_do_dia_local,
    semana_iso,
)
from app.models import (
    STATUS_OCUPAM_AGENDA,
    Agendamento,
    PerfilPaciente,
    PerfilProfissional,
    StatusAgendamento,
)
from app.models.perfil import ProfissionalEspecialidade
from app.services.parametros_service import ParametrosService
from app.services.regras.limites import (
    JanelaAgendada,
    valida_limite_horas_dia,
    valida_limite_sessoes_semana,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PedidoReserva:
    paciente: PerfilPaciente
    profissional: PerfilProfissional
    especialidade_id: uuid.UUID
    inicio_utc: datetime
    disponibilidade_origem_id: uuid.UUID | None = None
    serie_recorrencia_id: uuid.UUID | None = None


class AgendamentoService:
    def __init__(self, sessao: AsyncSession, parametros: ParametrosService) -> None:
        self.sessao = sessao
        self.parametros = parametros

    async def reservar(self, pedido: PedidoReserva) -> Agendamento:
        """Cria o agendamento em ``PENDENTE_PAGAMENTO``, com o slot já travado.

        O horário fica ocupado desde já (o status participa da constraint
        EXCLUDE): como o fluxo é sintomas → horário → pagamento, sem isso dois
        pacientes pagariam pelo mesmo horário e um ficaria sem sessão.
        """
        agora = agora_utc()
        prof = pedido.profissional

        if pedido.inicio_utc <= agora:
            raise AgendamentoNoPassado()

        antecedencia = timedelta(hours=prof.antecedencia_minima_agendamento_h)
        if pedido.inicio_utc < agora + antecedencia:
            raise AntecedenciaInsuficiente(
                f"Escolha um horário com pelo menos "
                f"{prof.antecedencia_minima_agendamento_h}h de antecedência."
            )

        duracao = prof.duracao_sessao_min or await self.parametros.duracao_sessao_min()
        fim_utc = pedido.inicio_utc + timedelta(minutes=duracao)
        valor = await self._preco(prof, pedido.especialidade_id)

        tz_prof = TZ_BR
        tz_pac = TZ_BR
        dia = dia_local(pedido.inicio_utc, tz_prof)
        ano, semana = semana_iso(pedido.inicio_utc, tz_pac)

        # --- Locks, sempre nesta ordem: profissional, depois paciente -------
        await self._lock(f"prof_dia:{prof.usuario_id}:{dia.isoformat()}")
        await self._lock(f"pac_sem:{pedido.paciente.usuario_id}:{ano}-{semana}")

        # --- R1: horas por dia do profissional ------------------------------
        limite_horas = prof.limite_horas_dia or await self.parametros.limite_horas_dia()
        valida_limite_horas_dia(
            await self._janelas_do_dia(prof.usuario_id, pedido.inicio_utc, tz_prof),
            duracao,
            limite_horas,
        )

        # --- R2: sessões por semana do paciente -----------------------------
        valida_limite_sessoes_semana(
            await self._sessoes_na_semana(pedido.paciente.usuario_id, pedido.inicio_utc, tz_pac),
            await self.parametros.limite_sessoes_semana(),
        )

        ttl = await self.parametros.ttl_reserva_pagamento_min()
        agendamento = Agendamento(
            paciente_id=pedido.paciente.usuario_id,
            profissional_id=prof.usuario_id,
            especialidade_id=pedido.especialidade_id,
            inicio_utc=pedido.inicio_utc,
            fim_utc=fim_utc,
            duracao_min=duracao,
            status=StatusAgendamento.PENDENTE_PAGAMENTO,
            disponibilidade_origem_id=pedido.disponibilidade_origem_id,
            serie_recorrencia_id=pedido.serie_recorrencia_id,
            valor_centavos=valor,
            reserva_expira_em=agora + timedelta(minutes=ttl),
        )
        self.sessao.add(agendamento)

        try:
            await self.sessao.flush()
        except IntegrityError as exc:
            await self.sessao.rollback()
            # --- R6: a palavra final é do banco -----------------------------
            if "ex_agendamento_" in str(exc.orig):
                raise SlotIndisponivel() from exc
            raise

        log.info(
            "agendamento.reservado",
            agendamento_id=str(agendamento.id),
            expira_em=agendamento.reserva_expira_em.isoformat()
            if agendamento.reserva_expira_em
            else None,
        )
        return agendamento

    async def confirmar(self, agendamento_id: uuid.UUID) -> Agendamento:
        """Chamado quando o pagamento é aprovado."""
        agendamento = await self._buscar(agendamento_id)
        if agendamento.status is not StatusAgendamento.PENDENTE_PAGAMENTO:
            return agendamento
        agendamento.status = StatusAgendamento.CONFIRMADO
        agendamento.reserva_expira_em = None
        await self.sessao.flush()
        log.info("agendamento.confirmado", agendamento_id=str(agendamento.id))
        return agendamento

    async def cancelar(
        self,
        agendamento_id: uuid.UUID,
        *,
        por_id: uuid.UUID,
        pelo_paciente: bool,
        motivo: str | None = None,
    ) -> Agendamento:
        agendamento = await self._buscar(agendamento_id)
        agendamento.status = (
            StatusAgendamento.CANCELADO_PACIENTE
            if pelo_paciente
            else StatusAgendamento.CANCELADO_PROFISSIONAL
        )
        agendamento.cancelado_em = agora_utc()
        agendamento.cancelado_por_id = por_id
        agendamento.motivo_cancelamento = motivo
        await self.sessao.flush()

        # Devolve o crédito ao pacote. Sem isto, cancelar uma sessão de um
        # pacote de 10 queimaria o crédito -- o paciente pagaria por uma sessão
        # que não aconteceu.
        #
        # NOTA: devolve sempre, sem olhar antecedência. A política de
        # cancelamento (quantas horas antes é gratuito) é questão aberta em
        # docs/05-roadmap.md; até haver decisão, o benefício da dúvida fica com
        # quem pagou.
        from app.services.credito_service import CreditoService

        await CreditoService(self.sessao).devolver(agendamento.id)
        return agendamento

    async def expirar_reservas_vencidas(self) -> int:
        """Libera slots cujo pagamento não veio a tempo. Roda no worker.

        É a contrapartida de PENDENTE_PAGAMENTO ocupar a agenda: sem isto, um
        checkout abandonado bloquearia o horário para sempre.
        """
        resultado = await self.sessao.execute(
            update(Agendamento)
            .where(
                Agendamento.status == StatusAgendamento.PENDENTE_PAGAMENTO,
                Agendamento.reserva_expira_em.is_not(None),
                Agendamento.reserva_expira_em < agora_utc(),
            )
            .values(status=StatusAgendamento.EXPIRADO, reserva_expira_em=None)
        )
        quantidade = int(getattr(resultado, "rowcount", 0) or 0)
        if quantidade:
            log.info("agendamento.reservas_expiradas", quantidade=quantidade)
        return quantidade

    # --- Internos -----------------------------------------------------------

    async def _lock(self, chave: str) -> None:
        """Advisory lock transacional.

        ``hashtextextended`` transforma a string em bigint. O lock não bloqueia
        leitura de ninguém -- só serializa quem disputa exatamente a mesma
        chave (o mesmo profissional no mesmo dia, o mesmo paciente na mesma
        semana).
        """
        await self.sessao.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:chave, 0))"),
            {"chave": chave},
        )

    async def _janelas_do_dia(
        self, profissional_id: uuid.UUID, referencia: datetime, tz: object
    ) -> list[JanelaAgendada]:
        # O dia é o LOCAL do profissional, não o dia UTC: às 21h em São Paulo já
        # é o dia seguinte em UTC, e contar pelo dia UTC atribuiria as consultas
        # noturnas ao dia errado.
        inicio, fim = limites_do_dia_local(dia_local(referencia, tz), tz)  # type: ignore[arg-type]
        linhas = (
            await self.sessao.execute(
                select(Agendamento.inicio_utc, Agendamento.fim_utc).where(
                    Agendamento.profissional_id == profissional_id,
                    Agendamento.status.in_(STATUS_OCUPAM_AGENDA),
                    Agendamento.inicio_utc >= inicio,
                    Agendamento.inicio_utc < fim,
                )
            )
        ).all()
        return [JanelaAgendada(i, f) for i, f in linhas]

    async def _sessoes_na_semana(
        self, paciente_id: uuid.UUID, referencia: datetime, tz: object
    ) -> int:
        inicio, fim = limites_da_semana_local(referencia, tz)  # type: ignore[arg-type]
        total = await self.sessao.scalar(
            select(func.count())
            .select_from(Agendamento)
            .where(
                Agendamento.paciente_id == paciente_id,
                Agendamento.status.in_(STATUS_OCUPAM_AGENDA),
                Agendamento.inicio_utc >= inicio,
                Agendamento.inicio_utc < fim,
            )
        )
        return int(total or 0)

    async def _preco(self, prof: PerfilProfissional, especialidade_id: uuid.UUID) -> int:
        preco = await self.sessao.scalar(
            select(ProfissionalEspecialidade.preco_padrao_centavos).where(
                ProfissionalEspecialidade.profissional_id == prof.usuario_id,
                ProfissionalEspecialidade.especialidade_id == especialidade_id,
                ProfissionalEspecialidade.ativo.is_(True),
            )
        )
        if preco is None:
            raise NaoEncontrado("Este profissional não atende essa especialidade.")
        return int(preco)

    async def _buscar(self, agendamento_id: uuid.UUID) -> Agendamento:
        agendamento = await self.sessao.get(Agendamento, agendamento_id)
        if agendamento is None:
            raise NaoEncontrado("Agendamento não encontrado.")
        return agendamento
