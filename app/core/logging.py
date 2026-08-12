"""Logging estruturado.

Numa plataforma de saúde, log é superfície de vazamento: um ``logger.info(user)``
distraído põe CPF, e-mail e sintomas em texto puro num arquivo que vai para
qualquer agregador. O processador ``redigir_pii`` abaixo censura chaves
sensíveis por nome, antes de qualquer renderização.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from app.core.config import Ambiente, Settings

CHAVES_SENSIVEIS = frozenset(
    {
        "senha",
        "password",
        "senha_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "secret",
        "api_key",
        "cpf",
        "cnpj",
        "cpf_cnpj",
        "chave_acesso",
        "transcricao",
        "transcricao_texto",
        "texto",
        "sintomas",
        "comentario",
    }
)

MASCARA = "***"


def redigir_pii(
    _logger: Any, _nome: str, evento: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Censura valores cujas chaves são reconhecidamente sensíveis.

    É uma rede de segurança, não uma licença para logar dado clínico: a regra
    continua sendo logar identificadores, não conteúdo.
    """
    for chave in list(evento):
        if chave.lower() in CHAVES_SENSIVEIS and evento[chave] is not None:
            evento[chave] = MASCARA
    return evento


def configurar_logging(settings: Settings) -> None:
    nivel = logging.DEBUG if settings.app_debug else logging.INFO

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=nivel)
    # O uvicorn instala handlers próprios; deixamos que o structlog formate.
    for nome in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(nome).handlers.clear()
        logging.getLogger(nome).propagate = True
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database_echo else logging.WARNING
    )

    processadores: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redigir_pii,
    ]
    # Em dev, saída colorida legível; fora dele, JSON para o agregador.
    if settings.app_env is Ambiente.DEV:
        processadores.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processadores.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processadores,
        wrapper_class=structlog.make_filtering_bound_logger(nivel),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(nome: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(nome)  # type: ignore[no-any-return]
