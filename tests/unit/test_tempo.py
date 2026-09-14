"""Testes do módulo de tempo.

Estes cobrem exatamente as classes de bug que derrubaram o spike anterior:
dia UTC confundido com dia local, e semana calculada por subtração de weekday.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.tempo import (
    TZ_BR,
    agora_utc,
    combinar_local,
    dia_local,
    dia_semana,
    duracao_min,
    formatar_hhmm,
    limites_da_semana_local,
    limites_do_dia_local,
    minutos_do_dia,
    para_local,
    para_utc,
    parse_hhmm,
    semana_iso,
)

pytestmark = pytest.mark.unit

TZ_MANAUS = ZoneInfo("America/Manaus")


class TestAware:
    def test_agora_utc_e_aware(self) -> None:
        agora = agora_utc()
        assert agora.tzinfo is not None
        assert agora.utcoffset() is not None and agora.utcoffset().total_seconds() == 0

    def test_para_utc_rejeita_naive(self) -> None:
        # O ponto central: em vez de adivinhar o fuso, falha alto.
        with pytest.raises(ValueError, match="naive"):
            para_utc(datetime(2026, 8, 12, 14, 0))  # noqa: DTZ001

    def test_para_utc_converte_de_outro_fuso(self) -> None:
        # 14h em São Paulo (UTC-3) é 17h UTC.
        sp = datetime(2026, 8, 12, 14, 0, tzinfo=TZ_BR)
        assert para_utc(sp) == datetime(2026, 8, 12, 17, 0, tzinfo=UTC)


class TestDiaLocal:
    def test_dia_local_difere_do_dia_utc_a_noite(self) -> None:
        """O bug do spike: às 21h de São Paulo já é o dia seguinte em UTC.

        Contar "os agendamentos do dia" pelo dia UTC atribuiria as consultas
        noturnas ao dia errado, furando o limite de 10h/dia.
        """
        instante = datetime(2026, 8, 12, 23, 30, tzinfo=UTC)  # 20:30 em São Paulo
        assert instante.date() == date(2026, 8, 12)
        assert dia_local(instante, TZ_BR) == date(2026, 8, 12)

        instante_tarde = datetime(2026, 8, 13, 2, 0, tzinfo=UTC)  # 23:00 do dia 12 em SP
        assert instante_tarde.date() == date(2026, 8, 13)
        assert dia_local(instante_tarde, TZ_BR) == date(2026, 8, 12)

    def test_limites_do_dia_local_cobrem_24h(self) -> None:
        inicio, fim = limites_do_dia_local(date(2026, 8, 12), TZ_BR)
        assert duracao_min(inicio, fim) == 24 * 60
        assert para_local(inicio, TZ_BR).hour == 0
        assert dia_local(inicio, TZ_BR) == date(2026, 8, 12)

    def test_fuso_do_usuario_e_respeitado(self) -> None:
        """Paciente em Manaus (UTC-4) vê o dia conforme o fuso dele."""
        instante = datetime(2026, 8, 13, 3, 0, tzinfo=UTC)
        assert dia_local(instante, TZ_BR) == date(2026, 8, 13)  # 00:00 em SP
        assert dia_local(instante, TZ_MANAUS) == date(2026, 8, 12)  # 23:00 em Manaus


class TestSemana:
    def test_semana_iso_na_virada_do_ano(self) -> None:
        """1º de janeiro de 2027 é sexta: pertence à semana 53 de 2026.

        O spike calculava a semana com `dt - timedelta(days=dt.weekday())`, o
        que trata essa data como semana 1 de 2027 e deixa o paciente marcar mais
        sessões do que o limite na virada.
        """
        assert semana_iso(datetime(2027, 1, 1, 15, 0, tzinfo=UTC), TZ_BR) == (2026, 53)
        assert semana_iso(datetime(2027, 1, 4, 15, 0, tzinfo=UTC), TZ_BR) == (2027, 1)

    def test_mesma_semana_para_segunda_e_domingo(self) -> None:
        segunda = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
        domingo = datetime(2026, 8, 16, 15, 0, tzinfo=UTC)
        assert semana_iso(segunda, TZ_BR) == semana_iso(domingo, TZ_BR)

    def test_limites_da_semana_comecam_na_segunda(self) -> None:
        quarta = datetime(2026, 8, 12, 15, 0, tzinfo=UTC)
        inicio, fim = limites_da_semana_local(quarta, TZ_BR)
        assert dia_local(inicio, TZ_BR) == date(2026, 8, 10)  # segunda
        assert duracao_min(inicio, fim) == 7 * 24 * 60


class TestMinutosDoDia:
    def test_combinar_local_converte_para_utc(self) -> None:
        # 9h30 local em São Paulo = 12h30 UTC.
        assert combinar_local(date(2026, 8, 12), 570, TZ_BR) == datetime(
            2026, 8, 12, 12, 30, tzinfo=UTC
        )

    def test_1440_e_meia_noite_do_dia_seguinte(self) -> None:
        """Fim de janela exclusivo: 1440 = 00:00 do dia seguinte."""
        assert combinar_local(date(2026, 8, 12), 1440, TZ_BR) == combinar_local(
            date(2026, 8, 13), 0, TZ_BR
        )

    @pytest.mark.parametrize("invalido", [-1, 1441, 5000])
    def test_rejeita_fora_do_dia(self, invalido: int) -> None:
        with pytest.raises(ValueError, match="minutos_do_dia"):
            combinar_local(date(2026, 8, 12), invalido, TZ_BR)

    def test_ida_e_volta(self) -> None:
        instante = combinar_local(date(2026, 8, 12), 855, TZ_BR)  # 14:15
        assert minutos_do_dia(instante, TZ_BR) == 855
        assert dia_semana(instante, TZ_BR) == 2  # quarta-feira


class TestFormatacao:
    @pytest.mark.parametrize(
        ("minutos", "esperado"),
        [(0, "00:00"), (570, "09:30"), (840, "14:00"), (1439, "23:59"), (1440, "24:00")],
    )
    def test_formatar_hhmm(self, minutos: int, esperado: str) -> None:
        assert formatar_hhmm(minutos) == esperado

    @pytest.mark.parametrize(("texto", "esperado"), [("09:30", 570), ("00:00", 0), ("23:59", 1439)])
    def test_parse_hhmm(self, texto: str, esperado: int) -> None:
        assert parse_hhmm(texto) == esperado

    def test_parse_hhmm_rejeita_hora_invalida(self) -> None:
        with pytest.raises(ValueError, match="fora do dia"):
            parse_hhmm("25:00")
