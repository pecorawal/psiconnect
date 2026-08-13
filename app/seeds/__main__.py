"""Ponto de entrada do seed.

    python -m app.seeds           # taxonomias, planos, parâmetros, termos
    python -m app.seeds --demo    # + usuários e dados de demonstração

Idempotente: pode rodar quantas vezes for preciso.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.logging import configurar_logging, get_logger
from app.db.sessao import fechar_engine, get_sessionmaker, init_engine
from app.seeds.demo import semear_demo
from app.seeds.especialidades import semear_especialidades
from app.seeds.parametros import atualizar_parametros, semear_parametros
from app.seeds.planos import semear_planos
from app.seeds.sintomas import semear_sintomas
from app.seeds.termos import semear_termos

log = get_logger(__name__)


async def executar(com_demo: bool, atualizar: bool = False) -> None:
    settings = get_settings()
    configurar_logging(settings)
    init_engine(settings)

    async with get_sessionmaker()() as sessao, sessao.begin():
        if atualizar:
            # Sobrescreve valores já gravados: é uma decisão de negócio, por
            # isso exige a flag explícita.
            for chave, antes, depois in await atualizar_parametros(sessao, settings):
                log.warning("seed.parametro_alterado", chave=chave, antes=antes, depois=depois)
        log.info("seed.parametros", novos=await semear_parametros(sessao, settings))
        # Ordem importa: sintomas apontam para especialidades.
        log.info("seed.especialidades", novos=await semear_especialidades(sessao))
        sintomas, ligacoes = await semear_sintomas(sessao)
        log.info("seed.sintomas", novos=sintomas, ligacoes=ligacoes)
        log.info("seed.planos", novos=await semear_planos(sessao))
        log.info("seed.termos", novos=await semear_termos(sessao))

        if com_demo:
            if settings.eh_producao:
                raise SystemExit("Recusando semear dados de demonstração em produção.")
            resumo = await semear_demo(sessao)
            log.info("seed.demo", **resumo)

    await fechar_engine()
    log.info("seed.concluido")


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seeds")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="cria usuários e dados de demonstração (bloqueado em produção)",
    )
    parser.add_argument(
        "--atualizar-parametros",
        action="store_true",
        help=(
            "sobrescreve os parâmetros de sistema já gravados com os valores do "
            "ambiente (comissão, limites). Não reprecifica compras passadas."
        ),
    )
    args = parser.parse_args()
    asyncio.run(executar(com_demo=args.demo, atualizar=args.atualizar_parametros))
    return 0


if __name__ == "__main__":
    sys.exit(main())
