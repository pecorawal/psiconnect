"""Tempo e fuso horário.

Regra única do projeto: **tudo em UTC no banco (TIMESTAMPTZ), converte só nas
bordas** (renderização e entrada do usuário).

Duas armadilhas que este módulo existe para evitar:

1. ``datetime.now()`` e ``datetime.utcnow()`` produzem datetimes *naive*. O
   spike anterior usava isso e calculava "o dia" com ``replace(hour=0)``, o que
   dava o dia UTC e não o dia local do profissional -- errado em qualquer fuso
   negativo depois das 21h. A regra de lint ``DTZ`` do ruff proíbe ambos; use
   ``agora_utc()``.

2. ``DisponibilidadeRecorrente`` guarda hora **local de parede**, não UTC. Se
   guardássemos "terça 17:00 UTC" e o horário de verão voltasse (o Brasil o
   aboliu em 2019, mas isso é reversível por decreto), a agenda de todo mundo
   deslizaria 1 hora. Guardando "terça 14:00 local", 14h continua sendo 14h.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ_BR = ZoneInfo("America/Sao_Paulo")
"""Fuso padrão do produto. Cada usuário pode ter o seu (ex.: America/Manaus)."""

MINUTOS_POR_DIA = 24 * 60


def agora_utc() -> datetime:
    """Instante atual, sempre *aware* e em UTC."""
    return datetime.now(UTC)


def para_utc(dt: datetime) -> datetime:
    """Converte para UTC. Rejeita datetime naive em vez de adivinhar o fuso."""
    if dt.tzinfo is None:
        raise ValueError(
            "datetime naive não é aceito: informe o fuso explicitamente. "
            "Use combinar_local() para converter hora local em UTC."
        )
    return dt.astimezone(UTC)


def para_local(dt: datetime, tz: ZoneInfo = TZ_BR) -> datetime:
    """Converte um instante para o fuso indicado (para renderizar)."""
    return para_utc(dt).astimezone(tz)


def resolver_horario_local(dia: date, hora: time, tz: ZoneInfo = TZ_BR) -> datetime:
    """Combina data + hora local num instante absoluto, tratando transições de DST.

    Em transições de horário de verão existem horas *inexistentes* (o relógio
    pula de 23:59 para 01:00) e horas *ambíguas* (a mesma hora acontece duas
    vezes). PEP 495 resolve a ambiguidade com ``fold``; adotamos ``fold=0``, ou
    seja, a primeira ocorrência.

    Para horas inexistentes, empurramos para o próximo instante válido: é
    preferível a sessão começar 1h depois a a expansão da agenda estourar.
    """
    candidato = datetime.combine(dia, hora, tzinfo=tz)
    # Se a hora não existe no fuso, o round-trip não bate: normaliza.
    if para_local(candidato, tz).timetz().replace(tzinfo=None) != hora:
        candidato = datetime.combine(dia, hora, tzinfo=tz) + timedelta(hours=1)
    return para_utc(candidato)


def combinar_local(dia: date, minutos_do_dia: int, tz: ZoneInfo = TZ_BR) -> datetime:
    """Combina uma data local com minutos desde a meia-noite, devolvendo UTC.

    ``DisponibilidadeRecorrente`` guarda janelas em minutos desde a meia-noite
    (0..1440) porque isso permite usar ``int4range`` na constraint EXCLUDE do
    Postgres, o que o tipo ``TIME`` não permite.

    ``minutos_do_dia == 1440`` significa meia-noite do dia seguinte (fim de
    janela exclusivo), e é aceito de propósito.
    """
    if not 0 <= minutos_do_dia <= MINUTOS_POR_DIA:
        raise ValueError(f"minutos_do_dia fora de 0..{MINUTOS_POR_DIA}: {minutos_do_dia}")
    if minutos_do_dia == MINUTOS_POR_DIA:
        return resolver_horario_local(dia + timedelta(days=1), time(0, 0), tz)
    return resolver_horario_local(dia, time(minutos_do_dia // 60, minutos_do_dia % 60), tz)


def dia_local(dt: datetime, tz: ZoneInfo = TZ_BR) -> date:
    """O dia-calendário a que o instante pertence *no fuso do usuário*.

    É isto que a regra R1 (máx. 10h/dia) precisa: o dia do profissional, não o
    dia UTC.
    """
    return para_local(dt, tz).date()


def limites_do_dia_local(dia: date, tz: ZoneInfo = TZ_BR) -> tuple[datetime, datetime]:
    """Intervalo UTC semiaberto ``[inicio, fim)`` que cobre um dia local."""
    return combinar_local(dia, 0, tz), combinar_local(dia + timedelta(days=1), 0, tz)


def limites_do_mes_local(ano: int, mes: int, tz: ZoneInfo = TZ_BR) -> tuple[datetime, datetime]:
    """Intervalo UTC semiaberto ``[inicio, fim)`` que cobre um mês local.

    Semiaberto, e não ``BETWEEN`` com o último instante do mês: o fim do mês em
    UTC cai no meio da madrugada seguinte no horário local, e um ``<=`` ali
    incluiria ou perderia lançamentos da virada conforme o fuso.
    """
    inicio = combinar_local(date(ano, mes, 1), 0, tz)
    proximo = date(ano + 1, 1, 1) if mes == 12 else date(ano, mes + 1, 1)
    return inicio, combinar_local(proximo, 0, tz)


def semana_iso(dt: datetime, tz: ZoneInfo = TZ_BR) -> tuple[int, int]:
    """``(ano_iso, numero_da_semana)`` no fuso local.

    Usa o calendário ISO-8601: a semana começa na segunda-feira. O spike
    calculava a semana com ``dt - timedelta(days=dt.weekday())`` sobre um
    datetime naive, o que errava a virada de ano.
    """
    ano, semana, _ = para_local(dt, tz).isocalendar()
    return ano, semana


def limites_da_semana_local(dt: datetime, tz: ZoneInfo = TZ_BR) -> tuple[datetime, datetime]:
    """Intervalo UTC ``[segunda 00:00, segunda seguinte 00:00)`` da semana de ``dt``."""
    dia = dia_local(dt, tz)
    segunda = dia - timedelta(days=dia.weekday())
    return combinar_local(segunda, 0, tz), combinar_local(segunda + timedelta(days=7), 0, tz)


def dia_semana(dt: datetime, tz: ZoneInfo = TZ_BR) -> int:
    """Dia da semana local, 0 = segunda .. 6 = domingo (igual a ``date.weekday()``)."""
    return para_local(dt, tz).weekday()


def minutos_do_dia(dt: datetime, tz: ZoneInfo = TZ_BR) -> int:
    """Minutos desde a meia-noite local."""
    local = para_local(dt, tz)
    return local.hour * 60 + local.minute


def duracao_min(inicio: datetime, fim: datetime) -> int:
    """Duração em minutos inteiros entre dois instantes."""
    return int((para_utc(fim) - para_utc(inicio)).total_seconds() // 60)


def formatar_hhmm(minutos: int) -> str:
    """``570`` -> ``'09:30'``. Para renderizar janelas de disponibilidade."""
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def parse_hhmm(texto: str) -> int:
    """``'09:30'`` -> ``570``. Para ler o formulário de disponibilidade."""
    horas, _, mins = texto.strip().partition(":")
    total = int(horas) * 60 + int(mins)
    if not 0 <= total <= MINUTOS_POR_DIA:
        raise ValueError(f"horário fora do dia: {texto!r}")
    return total
