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


#: Marca, na própria sessão, que já existe um UnitOfWork ativo.
_ATRIBUTO_ANINHAMENTO = "_psiconnect_uow_profundidade"


class UnitOfWork:
    """Escopo transacional explícito para um caso de uso.

    Uso::

        async with UnitOfWork(sessao):
            await repo_agendamento.adicionar(ag)
            await repo_credito.consumir(credito_id)
        # commit aqui; qualquer exceção dentro do bloco faz rollback

    O aninhamento é rastreado por um contador na sessão -- **não** por
    ``sessao.in_transaction()``. A diferença importa: o SQLAlchemy abre a
    transação sozinho na primeira consulta, e numa requisição web isso já
    aconteceu antes de o caso de uso começar (a dependência que carrega o
    usuário logado faz um SELECT). Decidir "sou o dono?" olhando
    ``in_transaction()`` responderia *não* sempre, e o commit nunca aconteceria:
    a sessão fecharia no fim da requisição com rollback, descartando tudo em
    silêncio.
    """

    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao
        self._externo = False

    async def __aenter__(self) -> UnitOfWork:
        profundidade = getattr(self.sessao, _ATRIBUTO_ANINHAMENTO, 0)
        self._externo = profundidade == 0
        setattr(self.sessao, _ATRIBUTO_ANINHAMENTO, profundidade + 1)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        profundidade = getattr(self.sessao, _ATRIBUTO_ANINHAMENTO, 1)
        setattr(self.sessao, _ATRIBUTO_ANINHAMENTO, max(0, profundidade - 1))

        if exc_type is not None:
            # Rollback vale para a transação inteira, não só para o bloco
            # interno: um caso de uso parcialmente aplicado é pior que nenhum.
            await self.sessao.rollback()
            return

        if self._externo:
            await self.sessao.commit()
        else:
            # Aninhado: deixa o bloco externo decidir, mas garante que o SQL já
            # foi emitido (para que constraints falhem aqui, e não no commit).
            await self.sessao.flush()
