"""PWA: manifesto, service worker, página offline — e o que o cache não pode ter."""

from __future__ import annotations

import json
import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import RAIZ_PROJETO

pytestmark = pytest.mark.web

SW = RAIZ_PROJETO / "app" / "static" / "js" / "sw.js"


def cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://teste", follow_redirects=False
    )


def lista_do_shell() -> list[str]:
    """Extrai o array ``SHELL`` do service worker.

    Ler o JS em vez de manter a lista em Python é de propósito: o teste precisa
    falhar quando **o arquivo que roda no navegador** mudar.
    """
    fonte = SW.read_text(encoding="utf-8")
    bruto = re.search(r"var SHELL = \[(.*?)\];", fonte, re.S)
    assert bruto is not None, "o array SHELL sumiu do service worker"
    return re.findall(r'"([^"]+)"', bruto.group(1))


class TestPoliticaDeCache:
    def test_o_cache_so_guarda_o_shell(self) -> None:
        """O guarda-corpo da Fase 4: **nenhuma rota de aplicação** no cache.

        Qualquer página do PsiConnect pode conter nome de paciente, horário de
        consulta ou sintoma. Guardar isso no disco do aparelho cria uma cópia de
        dado sensível de saúde que sobrevive ao logout.
        """
        for caminho in lista_do_shell():
            assert caminho.startswith("/static/") or caminho == "/offline", (
                f"{caminho} não é app shell — dado clínico não pode ir para o cache"
            )

    def test_o_shell_aponta_para_arquivos_que_existem(self) -> None:
        """Um caminho errado no `addAll` derruba a instalação inteira do SW."""
        for caminho in lista_do_shell():
            if caminho == "/offline":
                continue
            assert (RAIZ_PROJETO / "app" / caminho.lstrip("/")).is_file(), caminho

    def test_so_navegacao_cai_para_a_pagina_offline(self) -> None:
        fonte = SW.read_text(encoding="utf-8")
        assert 'requisicao.mode === "navigate"' in fonte
        # Um fallback genérico devolveria conteúdo velho para um fragmento HTMX.
        assert fonte.count('caches.match("/offline")') == 1

    def test_escrita_nunca_passa_pelo_cache(self) -> None:
        assert 'requisicao.method !== "GET"' in SW.read_text(encoding="utf-8")


class TestManifesto:
    async def test_content_type_proprio(self, app: FastAPI) -> None:
        """Servido como `application/json` o navegador ignora o manifesto."""
        async with cliente(app) as c:
            r = await c.get("/manifest.webmanifest")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/manifest+json")

    async def test_tem_o_que_o_navegador_exige_para_instalar(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            r = await c.get("/manifest.webmanifest")
        manifesto = json.loads(r.text)

        assert manifesto["display"] == "standalone"
        assert manifesto["start_url"] == "/"
        tamanhos = {icone["sizes"] for icone in manifesto["icons"]}
        assert {"192x192", "512x512"} <= tamanhos
        # Sem `maskable` o Android emoldura o ícone num quadrado branco.
        assert any(icone.get("purpose") == "maskable" for icone in manifesto["icons"])

    async def test_os_icones_declarados_existem(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            manifesto = json.loads((await c.get("/manifest.webmanifest")).text)
            for icone in manifesto["icons"]:
                assert (await c.get(icone["src"])).status_code == 200, icone["src"]


class TestServiceWorker:
    async def test_servido_na_raiz(self, app: FastAPI) -> None:
        """Fora da raiz ele controlaria só o próprio diretório."""
        async with cliente(app) as c:
            r = await c.get("/sw.js")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/javascript")
        assert r.headers["service-worker-allowed"] == "/"

    async def test_nao_e_cacheado_pelo_navegador(self, app: FastAPI) -> None:
        """SW em cache é bug que não some sozinho: o app velho fica para sempre."""
        async with cliente(app) as c:
            r = await c.get("/sw.js")
        assert r.headers["cache-control"] == "no-cache"


class TestPaginaOffline:
    async def test_renderiza_sem_layout(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            r = await c.get("/offline")
        assert r.status_code == 200
        assert "sem conexão" in r.text.lower()
        # Não herda o cabeçalho: servida do cache, mostraria um login errado.
        assert "Pular para o conteúdo" not in r.text

    async def test_traz_o_canal_de_crise(self, app: FastAPI) -> None:
        """O 188 funciona sem internet — é o que ainda serve nesta tela."""
        async with cliente(app) as c:
            r = await c.get("/offline")
        assert "188" in r.text

    async def test_nao_depende_de_css_externo(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            r = await c.get("/offline")
        assert "<style>" in r.text
        assert 'rel="stylesheet"' not in r.text


class TestPaginasComuns:
    async def test_home_anuncia_o_manifesto_e_o_registro(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            r = await c.get("/")
        assert '<link rel="manifest" href="/manifest.webmanifest">' in r.text
        assert "js/pwa.js" in r.text
        assert "apple-touch-icon" in r.text
