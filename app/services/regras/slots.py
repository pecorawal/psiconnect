"""Expansão da disponibilidade em slots — funções puras.

O ``Slot`` é **derivado**, nunca persistido (ADR 0006). Pré-gerá-lo no banco
exigiria cron, teria condição de corrida com o agendamento e faria a tabela
crescer sem limite. Aqui a regra é expandida sob demanda:

    disponibilidade recorrente
      − bloqueios (férias, feriado)
      − horários já ocupados
      − o que já passou / está dentro da antecedência mínima
      = slots livres
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.tempo import TZ_BR, combinar_local


@dataclass(frozen=True, slots=True)
class RegraDisponibilidade:
    """Projeção da ``DisponibilidadeRecorrente``, sem ORM."""

    id: object
    dia_semana: int
    inicio_min: int
    fim_min: int
    vigencia_inicio: date
    vigencia_fim: date | None = None

    def vigente_em(self, dia: date) -> bool:
        if dia < self.vigencia_inicio:
            return False
        return self.vigencia_fim is None or dia <= self.vigencia_fim


@dataclass(frozen=True, slots=True)
class Intervalo:
    inicio_utc: datetime
    fim_utc: datetime

    def sobrepoe(self, outro: Intervalo) -> bool:
        """Sobreposição com fim exclusivo: 10:00–10:50 e 10:50–11:40 **não**
        se sobrepõem. Espelha o range ``'[)'`` das constraints do Postgres."""
        return self.inicio_utc < outro.fim_utc and outro.inicio_utc < self.fim_utc


@dataclass(frozen=True, slots=True)
class Slot:
    inicio_utc: datetime
    fim_utc: datetime
    disponibilidade_id: object

    @property
    def intervalo(self) -> Intervalo:
        return Intervalo(self.inicio_utc, self.fim_utc)


def expandir_regra(
    regra: RegraDisponibilidade,
    dia: date,
    duracao_min: int,
    passo_min: int,
    tz: ZoneInfo = TZ_BR,
) -> list[Slot]:
    """Fatia uma janela de um dia em slots de ``duracao_min``.

    Só gera slots que **cabem inteiros** na janela: uma janela 09:00–12:00 com
    sessão de 50 min e passo de 60 rende 09:00, 10:00 e 11:00 — não 11:30, que
    terminaria depois das 12:00.
    """
    if not regra.vigente_em(dia) or dia.weekday() != regra.dia_semana:
        return []

    slots: list[Slot] = []
    minuto = regra.inicio_min
    while minuto + duracao_min <= regra.fim_min:
        inicio = combinar_local(dia, minuto, tz)
        slots.append(
            Slot(
                inicio_utc=inicio,
                fim_utc=inicio + timedelta(minutes=duracao_min),
                disponibilidade_id=regra.id,
            )
        )
        minuto += passo_min
    return slots


def gerar_slots(
    regras: Sequence[RegraDisponibilidade],
    dias: Iterable[date],
    *,
    duracao_min: int,
    passo_min: int,
    ocupados: Sequence[Intervalo] = (),
    bloqueios: Sequence[Intervalo] = (),
    a_partir_de: datetime | None = None,
    tz: ZoneInfo = TZ_BR,
) -> list[Slot]:
    """Todos os slots livres nos dias pedidos, já ordenados.

    ``a_partir_de`` corta o passado e a antecedência mínima de uma vez: quem
    chama passa ``agora + antecedencia``.
    """
    indisponiveis = list(ocupados) + list(bloqueios)
    livres: list[Slot] = []

    for dia in dias:
        for regra in regras:
            for slot in expandir_regra(regra, dia, duracao_min, passo_min, tz):
                if a_partir_de is not None and slot.inicio_utc < a_partir_de:
                    continue
                if any(slot.intervalo.sobrepoe(i) for i in indisponiveis):
                    continue
                livres.append(slot)

    livres.sort(key=lambda s: s.inicio_utc)
    return livres


def dias_entre(inicio: date, fim: date) -> list[date]:
    return [inicio + timedelta(days=n) for n in range((fim - inicio).days + 1)]


def sugerir_recorrencia(
    slot_escolhido: Slot,
    semanas: int,
    *,
    vezes_por_semana: int = 1,
    duracao_min: int = 50,
) -> list[Intervalo]:
    """R3 — sugere repetir o mesmo horário nas próximas semanas.

    Devolve apenas *sugestões*: quem valida contra o limite semanal (R2) e a
    disponibilidade real é o ``AgendamentoService``. Aqui não há I/O nem regra
    de negócio além da aritmética de datas.
    """
    if vezes_por_semana < 1 or semanas < 1:
        return []
    sugestoes: list[Intervalo] = []
    for semana in range(1, semanas + 1):
        inicio = slot_escolhido.inicio_utc + timedelta(weeks=semana)
        sugestoes.append(Intervalo(inicio, inicio + timedelta(minutes=duracao_min)))
    return sugestoes
