"""Rotas de saúde.

Distinção que importa em produção:

* ``/healthz`` -- *liveness*. Responde sem tocar em dependência nenhuma. Se
  falhar, o processo está travado e deve ser reiniciado.
* ``/readyz``  -- *readiness*. Verifica banco e extensões. Se falhar, o processo
  está vivo mas não deve receber tráfego; reiniciar não ajudaria.

Trocar os dois é um erro clássico: um ``/healthz`` que consulta o banco faz o
orquestrador matar réplicas saudáveis toda vez que o banco oscila.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.sessao import get_db

router = APIRouter(tags=["saude"], include_in_schema=False)

EXTENSOES_OBRIGATORIAS = ("vector", "btree_gist", "pgcrypto")


@router.get("/healthz")
async def healthz(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    return {"status": "ok", "app": settings.app_nome, "ambiente": settings.app_env.value}


@router.get("/readyz")
async def readyz(
    resposta: Response,
    sessao: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    checagens: dict[str, Any] = {}
    pronto = True

    try:
        await sessao.execute(text("SELECT 1"))
        checagens["banco"] = "ok"
    # readiness reporta o problema, não o trata: qualquer falha vira 503.
    except Exception as exc:
        checagens["banco"] = f"erro: {type(exc).__name__}"
        pronto = False

    if pronto:
        try:
            linhas = await sessao.execute(text("SELECT extname FROM pg_extension"))
            instaladas = {linha[0] for linha in linhas}
            faltando = [e for e in EXTENSOES_OBRIGATORIAS if e not in instaladas]
            checagens["extensoes"] = "ok" if not faltando else f"faltando: {', '.join(faltando)}"
            pronto = not faltando
        except Exception as exc:
            checagens["extensoes"] = f"erro: {type(exc).__name__}"
            pronto = False

        try:
            versao = await sessao.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            atual = versao.scalar_one_or_none()
            checagens["migration"] = atual or "nenhuma aplicada"
            pronto = pronto and atual is not None
        except Exception as exc:
            checagens["migration"] = f"erro: {type(exc).__name__}"
            pronto = False

    if not pronto:
        resposta.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "pronto" if pronto else "nao_pronto", "checagens": checagens}
