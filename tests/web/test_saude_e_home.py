"""Testes das rotas de saúde e da home.

Portão de regressão da Fase 0: garante que a aplicação sobe, renderiza e que a
distinção liveness/readiness está correta.
"""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.web


async def _cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://teste")


class TestHealthz:
    async def test_healthz_nao_toca_no_banco(self, app: FastAPI) -> None:
        """Liveness precisa responder mesmo com o banco fora.

        Se `/healthz` dependesse do banco, uma oscilação derrubaria réplicas
        saudáveis -- reiniciar não resolveria nada.
        """
        async with await _cliente(app) as c:
            r = await c.get("/healthz")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestReadyz:
    @pytest.mark.db
    async def test_readyz_confere_banco_extensoes_e_migration(self, app: FastAPI) -> None:
        async with await _cliente(app) as c:
            r = await c.get("/readyz")
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["status"] == "pronto"
        assert corpo["checagens"]["banco"] == "ok"
        # btree_gist e vector são pré-requisito das constraints EXCLUDE e do
        # pgvector; sem elas o schema da Fase 1 não sobe.
        assert corpo["checagens"]["extensoes"] == "ok"
        assert corpo["checagens"]["migration"]


class TestHome:
    async def test_home_renderiza(self, app: FastAPI) -> None:
        async with await _cliente(app) as c:
            r = await c.get("/")
        assert r.status_code == 200
        sopa = BeautifulSoup(r.text, "html.parser")
        assert sopa.find("h1") is not None
        assert sopa.html is not None and sopa.html.get("lang") == "pt-BR"

    async def test_home_traz_canal_de_crise(self, app: FastAPI) -> None:
        """O CVV (188) é requisito de segurança do produto, não decoração."""
        async with await _cliente(app) as c:
            r = await c.get("/")
        assert "188" in r.text
        assert "CVV" in r.text

    async def test_cabecalhos_de_seguranca(self, app: FastAPI) -> None:
        async with await _cliente(app) as c:
            r = await c.get("/")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "same-origin"
        assert r.headers["x-request-id"]

    async def test_request_id_do_cliente_e_propagado(self, app: FastAPI) -> None:
        async with await _cliente(app) as c:
            r = await c.get("/", headers={"X-Request-ID": "abc123"})
        assert r.headers["x-request-id"] == "abc123"
