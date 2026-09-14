"""Base declarativa única.

O spike anterior tinha **dois** ``declarative_base()`` -- um em ``database.py``,
outro em ``models.py`` -- o que significa dois ``MetaData`` distintos e, na
prática, relacionamentos que nunca resolveriam. Aqui existe uma só.

A ``naming_convention`` é o que torna as migrations do Alembic determinísticas:
sem ela, o Postgres inventa nomes para constraints e o ``--autogenerate`` passa
a produzir diffs espúrios a cada execução.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

CONVENCAO_NOMES = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=CONVENCAO_NOMES)
