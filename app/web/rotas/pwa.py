"""PWA: manifesto, service worker e a página de falha sem conexão.

Por que não são arquivos estáticos comuns:

* o **service worker** só controla o que está abaixo do caminho de onde foi
  servido. Em ``/static/js/sw.js`` ele controlaria ``/static/js/`` e mais nada —
  precisa sair de ``/sw.js`` para valer para o app inteiro;
* o **manifesto** precisa do ``Content-Type: application/manifest+json``, que o
  ``mimetypes`` do Python não conhece, e monta nome, cores e ícones a partir das
  settings em vez de repetir tudo num JSON solto.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.core.config import RAIZ_PROJETO, Settings
from app.core.deps import Config
from app.core.templating import responder

router = APIRouter(tags=["pwa"], include_in_schema=False)

ARQUIVO_SW = RAIZ_PROJETO / "app" / "static" / "js" / "sw.js"

#: Verde da marca — o mesmo do favicon e da meta `theme-color`.
COR_TEMA = "#0f766e"
COR_FUNDO = "#f8fafc"


def _manifesto(settings: Settings) -> dict[str, Any]:
    return {
        "id": "/",
        "name": settings.app_nome,
        "short_name": settings.app_nome,
        "description": (
            "Atendimento psicológico online: encontre profissionais, agende e "
            "seja atendido com sigilo."
        ),
        "lang": "pt-BR",
        "dir": "ltr",
        # A home, e não o painel: aberto por quem ainda não entrou, o painel
        # responderia 401 — o app abriria numa página de erro.
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": COR_FUNDO,
        "theme_color": COR_TEMA,
        "categories": ["health", "medical", "lifestyle"],
        "icons": [
            {"src": "/static/img/icone-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/img/icone-512.png", "sizes": "512x512", "type": "image/png"},
            {
                "src": "/static/img/icone-maskable-512.png",
                "sizes": "512x512",
                "type": "image/png",
                # Sem um ícone `maskable` o Android desenha o nosso dentro de um
                # quadrado branco, com moldura.
                "purpose": "maskable",
            },
            {"src": "/static/img/favicon.svg", "sizes": "any", "type": "image/svg+xml"},
        ],
    }


def montar(templates: Jinja2Templates) -> APIRouter:
    @router.get("/manifest.webmanifest", name="manifesto")
    async def manifesto(settings: Config) -> Response:
        return JSONResponse(
            _manifesto(settings),
            media_type="application/manifest+json",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @router.get("/sw.js", name="service_worker")
    async def service_worker() -> Response:
        return FileResponse(
            ARQUIVO_SW,
            media_type="text/javascript",
            headers={
                # Um service worker em cache é um bug que não some sozinho: o
                # navegador continuaria servindo a versão velha do app.
                "Cache-Control": "no-cache",
                "Service-Worker-Allowed": "/",
            },
        )

    @router.get("/offline", name="offline")
    async def offline(request: Request) -> Response:
        """Mostrada quando uma navegação falha por falta de rede.

        Página solta, sem herdar o layout: ela é servida pelo cache, e depender
        do cabeçalho seria mostrar um estado de login possivelmente errado.
        """
        return responder(
            request,
            templates,
            template_completo="pwa/offline.html",
            contexto={"titulo": "Sem conexão"},
        )

    return router
