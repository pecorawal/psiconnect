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
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings
from app.db.sessao import get_db
from app.providers.registry import montar_providers


@pytest.fixture(scope="session")
def settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def conexao(settings: Settings) -> AsyncIterator[AsyncConnection]:
    """Uma conexão com transação externa, descartada no teardown.

    Todos os objetos do teste (a sessão de asserção e as sessões das
    requisições) vivem nesta conexão, então enxergam o trabalho uns dos outros
    — mas nada escapa do rollback final.
    """
    engine = create_async_engine(str(settings.database_url))
    conn = await engine.connect()
    transacao = await conn.begin()
    yield conn
    await transacao.rollback()
    await conn.close()
    await engine.dispose()


@pytest.fixture
def fabrica_sessao(conexao: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=conexao,
        expire_on_commit=False,
        # Cada sessão abre um SAVEPOINT próprio: um commit não escapa do
        # rollback do teste, e um rollback não desfaz o que outras sessões já
        # commitaram.
        join_transaction_mode="create_savepoint",
    )


@pytest.fixture
async def sessao(
    fabrica_sessao: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Sessão para montar cenário e fazer asserções."""
    async with fabrica_sessao() as s:
        yield s


@pytest.fixture
async def app(
    settings: Settings,
    sessao: AsyncSession,
    fabrica_sessao: async_sessionmaker[AsyncSession],
) -> AsyncIterator[FastAPI]:
    """Aplicação com providers fake e banco transacional.

    **Uma sessão por requisição**, como em produção. Compartilhar uma única
    sessão entre requisições faz um rollback (por exemplo, de uma validação que
    falhou) desfazer o que requisições anteriores gravaram — inclusive o login.
    """
    from app.main import create_app

    aplicacao = create_app(settings)
    # O lifespan não roda com ASGITransport, então montamos os providers aqui.
    aplicacao.state.providers = montar_providers(settings)

    async def _sessao_por_requisicao() -> AsyncIterator[AsyncSession]:
        async with fabrica_sessao() as s:
            try:
                yield s
            except Exception:
                await s.rollback()
                raise

    aplicacao.dependency_overrides[get_db] = _sessao_por_requisicao
    try:
        # A sessão de asserção precisa enxergar o que as requisições commitaram.
        await sessao.commit()
        yield aplicacao
    finally:
        aplicacao.dependency_overrides.clear()
