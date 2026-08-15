"""Loop do worker.

    python -m app.workers            # roda continuamente
    python -m app.workers --uma-vez  # um ciclo e sai (útil em teste e cron)

A fila é o próprio Postgres, com ``FOR UPDATE SKIP LOCKED``. Não há Redis nem
broker: numa plataforma que faz dezenas de sessões por dia, acrescentar uma peça
de infraestrutura para isso seria custo sem retorno. Quando o volume justificar,
troca-se por ``arq`` sem mexer nos services.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
from types import FrameType

from app.core.config import get_settings
from app.core.logging import configurar_logging, get_logger
from app.db.sessao import fechar_engine, get_sessionmaker, init_engine
from app.providers.registry import montar_providers
from app.services.parametros_service import ParametrosService
from app.workers import agenda, outbox

log = get_logger(__name__)

INTERVALO_SEGUNDOS = 30

_parar = asyncio.Event()


def _sinal(_num: int, _quadro: FrameType | None) -> None:
    """Encerra ao fim do ciclo atual, sem cortar trabalho pela metade."""
    log.info("worker.encerrando")
    _parar.set()


async def ciclo() -> dict[str, int]:
    """Um ciclo completo. Cada tarefa em sua própria transação.

    Se a preparação de salas falhar, a outbox ainda roda -- uma tarefa não
    derruba as outras.
    """
    settings = get_settings()
    providers = montar_providers(settings)
    fabrica = get_sessionmaker()
    resultado = {
        "salas": 0,
        "expiradas": 0,
        "no_show": 0,
        "creditos": 0,
        "enviadas": 0,
        "falhas": 0,
    }

    async with fabrica() as sessao:
        parametros = ParametrosService(sessao, settings)

        async def _rodar(nome: str) -> None:
            """Cada tarefa em sua própria transação: uma falha não derruba as
            outras."""
            try:
                async with sessao.begin():
                    if nome == "salas":
                        resultado[nome] = await agenda.preparar_salas(
                            sessao, parametros, providers.video
                        )
                    elif nome == "expiradas":
                        resultado[nome] = await agenda.expirar_reservas(sessao, parametros)
                    elif nome == "no_show":
                        resultado[nome] = await agenda.marcar_no_show(sessao, parametros)
                    elif nome == "creditos":
                        resultado[nome] = await agenda.expirar_creditos(sessao)
            except Exception:
                await sessao.rollback()
                log.exception("worker.tarefa_falhou", tarefa=nome)

        for nome in ("salas", "expiradas", "no_show", "creditos"):
            await _rodar(nome)

        try:
            async with sessao.begin():
                enviadas, falhas = await outbox.enviar_pendentes(sessao, providers.notificacao)
                resultado["enviadas"] = enviadas
                resultado["falhas"] = falhas
        except Exception:
            await sessao.rollback()
            log.exception("worker.tarefa_falhou", tarefa="outbox")

    return resultado


async def executar(uma_vez: bool) -> None:
    settings = get_settings()
    configurar_logging(settings)
    init_engine(settings)
    log.info("worker.iniciado", intervalo_s=INTERVALO_SEGUNDOS, uma_vez=uma_vez)

    try:
        while True:
            resultado = await ciclo()
            if any(resultado.values()):
                log.info("worker.ciclo", **resultado)
            if uma_vez or _parar.is_set():
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(_parar.wait(), timeout=INTERVALO_SEGUNDOS)
            if _parar.is_set():
                break
    finally:
        await fechar_engine()
        log.info("worker.encerrado")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.workers")
    parser.add_argument("--uma-vez", action="store_true", help="executa um ciclo e encerra")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _sinal)
    signal.signal(signal.SIGINT, _sinal)

    asyncio.run(executar(uma_vez=args.uma_vez))
    return 0


if __name__ == "__main__":
    sys.exit(main())
