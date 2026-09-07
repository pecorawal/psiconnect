"""Formato do protocolo SSE."""

from __future__ import annotations

import pytest

from app.core.sse import KEEPALIVE, formatar_evento

pytestmark = pytest.mark.unit


class TestFormatarEvento:
    def test_evento_simples_termina_em_linha_em_branco(self) -> None:
        # Sem a linha em branco o navegador segura o evento no buffer.
        assert formatar_evento("/sessao/1/sala", evento="entrar") == (
            "event: entrar\ndata: /sessao/1/sala\n\n"
        )

    def test_html_de_varias_linhas_vira_uma_linha_data_por_linha(self) -> None:
        html = "<div>\n  <p>oi</p>\n</div>"
        assert formatar_evento(html, evento="estado") == (
            "event: estado\ndata: <div>\ndata:   <p>oi</p>\ndata: </div>\n\n"
        )

    def test_payload_vazio_ainda_produz_uma_linha_data(self) -> None:
        # `"".splitlines()` é `[]`; um evento sem nenhuma linha `data:` não
        # dispara o ouvinte do outro lado.
        assert formatar_evento("", evento="estado") == "event: estado\ndata: \n\n"

    def test_retry_e_id_precedem_os_dados(self) -> None:
        assert formatar_evento("x", evento="e", identificador="7", retry_ms=3000) == (
            "event: e\nid: 7\nretry: 3000\ndata: x\n\n"
        )

    def test_keepalive_e_um_comentario(self) -> None:
        # Comentário: o navegador descarta, o proxy conta como tráfego.
        assert KEEPALIVE.startswith(":")
        assert KEEPALIVE.endswith("\n\n")
