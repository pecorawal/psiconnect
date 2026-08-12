"""Fixtures compartilhadas."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest

# Settings previsíveis para toda a suíte, independentes do .env do dev.
os.environ.setdefault("APP_ENV", "teste")
os.environ.setdefault("APP_SECRET_KEY", "chave-de-teste-com-mais-de-32-bytes-ok!")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://psiconnect:psiconnect@localhost:5433/psiconnect"
)

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.db.sessao import get_db
from app.providers.registry import montar_providers


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def sessao(settings: Settings) -> AsyncIterator[AsyncSession]:
    """Sessão de banco que **sempre** faz rollback ao final.

    A conexão é aberta fora da sessão e a transação externa é descartada no
    teardown: os testes compartilham o schema (migrado uma vez), mas nenhum
    enxerga a sujeira do outro. Bem mais rápido que recriar o schema por teste.
    """
    engine = create_async_engine(str(settings.database_url), poolclass=None)
    conexao = await engine.connect()
    transacao = await conexao.begin()
    fabrica = async_sessionmaker(
        bind=conexao,
        expire_on_commit=False,
        # A sessão abre um SAVEPOINT em vez de participar da transação externa:
        # assim um commit do service não escapa do rollback do teste.
        join_transaction_mode="create_savepoint",
    )
    async with fabrica() as s:
        try:
            yield s
        finally:
            await s.close()
    await transacao.rollback()
    await conexao.close()
    await engine.dispose()


@pytest.fixture
async def app(settings: Settings, sessao: AsyncSession) -> AsyncIterator[FastAPI]:
    """Aplicação com providers fake e banco transacional.

    O override de ``get_db`` é o que impede os testes de rota de deixarem
    usuários e agendamentos no banco de desenvolvimento: sem ele, rodar a suíte
    duas vezes falharia por e-mail duplicado.
    """
    from app.main import create_app

    aplicacao = create_app(settings)
    # O lifespan não roda com ASGITransport, então montamos os providers aqui.
    aplicacao.state.providers = montar_providers(settings)

    async def _sessao_de_teste() -> AsyncIterator[AsyncSession]:
        yield sessao

    aplicacao.dependency_overrides[get_db] = _sessao_de_teste
    try:
        yield aplicacao
    finally:
        aplicacao.dependency_overrides.clear()
