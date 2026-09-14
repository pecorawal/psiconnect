"""Rotas públicas: home e páginas institucionais."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates

from app.core.templating import responder

router = APIRouter(tags=["publico"])


def montar(templates: Jinja2Templates) -> APIRouter:
    @router.get("/", name="home")
    async def home(request: Request) -> Response:
        return responder(
            request,
            templates,
            template_completo="publico/home.html",
            contexto={"titulo": "Cuidar de você começa aqui"},
        )

    return router
