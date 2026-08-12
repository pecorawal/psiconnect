"""Limites de agenda — R1 e R2.

Funções **puras**: recebem dataclasses, devolvem resultado ou levantam
``ErroDominio``. Zero I/O. É o que permite testá-las na velocidade da luz e
raciocinar sobre elas sem subir banco.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from app.core.erros import LimiteHorasDiaExcedido, LimiteSessoesSemanaExcedido
from app.core.tempo import duracao_min


@dataclass(frozen=True, slots=True)
class JanelaAgendada:
    inicio_utc: datetime
    fim_utc: datetime

    @property
    def minutos(self) -> int:
        return duracao_min(self.inicio_utc, self.fim_utc)


def minutos_agendados(janelas: Sequence[JanelaAgendada]) -> int:
    return sum(j.minutos for j in janelas)


def valida_limite_horas_dia(
    janelas_do_dia: Sequence[JanelaAgendada],
    duracao_nova_min: int,
    limite_horas: int,
) -> None:
    """R1 — o profissional não passa de N **horas** de atendimento por dia.

    O spike anterior contava *linhas* de agendamento e comparava com 10, ou
    seja, tratava "10 horas" como "10 consultas". Com sessão de 50 min isso dá
    8h20 (deixava passar do limite); com sessão de 90 min daria 15h (bloqueava
    cedo demais). O limite é de tempo, então conta-se tempo.
    """
    ja = minutos_agendados(janelas_do_dia)
    limite_min = limite_horas * 60
    if ja + duracao_nova_min > limite_min:
        restante = max(0, limite_min - ja)
        raise LimiteHorasDiaExcedido(
            "Este profissional já atingiu o limite de horas de atendimento neste dia.",
            detalhes={
                "minutos_agendados": ja,
                "minutos_solicitados": duracao_nova_min,
                "minutos_restantes": restante,
                "limite_horas": limite_horas,
            },
        )


def valida_limite_sessoes_semana(
    sessoes_na_semana: int, limite: int, *, adicionais: int = 1
) -> None:
    """R2 — o paciente não marca mais de N sessões por semana.

    A semana é a ISO (segunda a domingo); quem a calcula é ``core.tempo``. O
    spike usava ``dt - timedelta(days=dt.weekday())`` sobre datetime naive, o
    que erra na virada do ano -- 1º/jan/2027 cairia na semana errada e deixaria
    furar o limite.
    """
    if sessoes_na_semana + adicionais > limite:
        raise LimiteSessoesSemanaExcedido(
            f"Você já tem {sessoes_na_semana} "
            f"{'sessão marcada' if sessoes_na_semana == 1 else 'sessões marcadas'} "
            f"nesta semana. O limite é {limite}.",
            detalhes={"agendadas": sessoes_na_semana, "limite": limite},
        )


def cabe_no_dia(
    janelas_do_dia: Sequence[JanelaAgendada], duracao_min_nova: int, limite_horas: int
) -> bool:
    """Versão booleana de R1, para filtrar slots sem levantar exceção."""
    return minutos_agendados(janelas_do_dia) + duracao_min_nova <= limite_horas * 60
