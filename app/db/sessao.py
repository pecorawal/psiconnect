"""Engine, sessão e unidade de trabalho.

Contrato de transação do projeto:

* **Repository não commita.** Ele lê e adiciona objetos à sessão.
* **Service commita**, via ``async with UnitOfWork(...) as uow``.

Isso mantém casos de uso com vários passos (reservar agendamento + consumir
crédito + criar sessão + enfileirar notificações) atômicos de verdade.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import TracebackType

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def criar_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        str(settings.database_url),
        echo=settings.database_echo,
        pool_pre_ping=True,  # evita "server closed the connection" após ocioso
        pool_size=10,
        max_overflow=20,
        # SQLAlchemy 2.0 já usa expire_on_commit=False nos sessionmakers abaixo.
    )


def init_engine(settings: Settings | None = None) -> AsyncEngine:
    """Cria (uma vez) o engine e o sessionmaker do processo."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = criar_engine(settings or get_settings())
        _sessionmaker = async_sessionmaker(
            _engine,
            expire_on_commit=False,  # objetos seguem utilizáveis após o commit
            autoflush=False,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def fechar_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependência FastAPI: uma sessão por requisição.

    Não commita: quem decide é o service. Em caso de exceção, faz rollback.
    """
    async with get_sessionmaker()() as sessao:
        try:
            yield sessao
        except Exception:
            await sessao.rollback()
            raise


class UnitOfWork:
    """Escopo transacional explícito para um caso de uso.

    Uso::

        async with UnitOfWork(sessao):
            await repo_agendamento.adicionar(ag)
            await repo_credito.consumir(credito_id)
        # commit aqui; qualquer exceção dentro do bloco faz rollback

    Reentrante: se a sessão já estiver numa transação (o caso normal dentro de
    uma requisição), participa dela em vez de abrir outra.
    """

    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao
        self._proprietario = False

    async def __aenter__(self) -> UnitOfWork:
        if not self.sessao.in_transaction():
            await self.sessao.begin()
            self._proprietario = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.sessao.rollback()
            return
        if self._proprietario:
            await self.sessao.commit()
        else:
            await self.sessao.flush()
