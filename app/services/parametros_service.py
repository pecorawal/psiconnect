"""Leitura dos parâmetros de sistema, com cache curto.

Os parâmetros são lidos em praticamente todo agendamento (duração da sessão,
limites, comissão). Ler o banco toda vez seria desperdício; cachear para sempre
tiraria o sentido de serem editáveis pelo admin. O meio-termo é um TTL curto:
uma mudança no painel vale em no máximo ``TTL_SEGUNDOS``.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.parametro import ChaveParametro, ParametroSistema

TTL_SEGUNDOS = 30.0


class ParametrosService:
    """Cache de processo com TTL. Não é compartilhado entre réplicas -- e não
    precisa ser: o pior caso é uma réplica usar o valor antigo por 30s."""

    def __init__(self, sessao: AsyncSession, settings: Settings) -> None:
        self.sessao = sessao
        self.settings = settings

    # Compartilhado por todas as instâncias do processo: é um cache, não estado
    # de instância.
    _cache: ClassVar[dict[str, Any]] = {}
    _carregado_em: ClassVar[float] = 0.0

    @classmethod
    def invalidar_cache(cls) -> None:
        """Chamado pelo admin ao salvar um parâmetro, e pelos testes."""
        cls._cache = {}
        cls._carregado_em = 0.0

    async def _carregar(self) -> dict[str, Any]:
        agora = time.monotonic()
        if ParametrosService._cache and (agora - ParametrosService._carregado_em) < TTL_SEGUNDOS:
            return ParametrosService._cache
        linhas = (await self.sessao.execute(select(ParametroSistema))).scalars().all()
        ParametrosService._cache = {p.chave: p.valor for p in linhas}
        ParametrosService._carregado_em = agora
        return ParametrosService._cache

    async def obter(self, chave: str, padrao: Any = None) -> Any:
        return (await self._carregar()).get(chave, padrao)

    async def inteiro(self, chave: str, padrao: int) -> int:
        valor = await self.obter(chave)
        return padrao if valor is None else int(valor)

    async def decimal(self, chave: str, padrao: Decimal) -> Decimal:
        valor = await self.obter(chave)
        return padrao if valor is None else Decimal(str(valor))

    # --- Acessores nomeados: evitam string solta espalhada pelos services ---

    async def comissao_percentual(self) -> Decimal:
        return await self.decimal(
            ChaveParametro.COMISSAO_PERCENTUAL,
            Decimal(str(self.settings.comissao_percentual_padrao)),
        )

    async def duracao_sessao_min(self) -> int:
        return await self.inteiro(
            ChaveParametro.DURACAO_SESSAO_MIN, self.settings.duracao_sessao_padrao_min
        )

    async def limite_horas_dia(self) -> int:
        return await self.inteiro(
            ChaveParametro.LIMITE_HORAS_DIA, self.settings.limite_horas_dia_profissional
        )

    async def limite_sessoes_semana(self) -> int:
        return await self.inteiro(
            ChaveParametro.LIMITE_SESSOES_SEMANA, self.settings.limite_sessoes_semana_paciente
        )

    async def max_especialidades(self) -> int:
        return await self.inteiro(
            ChaveParametro.MAX_ESPECIALIDADES, self.settings.max_especialidades_profissional
        )

    async def tolerancia_atraso_min(self) -> int:
        return await self.inteiro(
            ChaveParametro.TOLERANCIA_ATRASO_MIN, self.settings.tolerancia_atraso_min
        )

    async def antecedencia_link_min(self) -> int:
        return await self.inteiro(
            ChaveParametro.ANTECEDENCIA_LINK_MIN, self.settings.antecedencia_link_min
        )

    async def ttl_reserva_pagamento_min(self) -> int:
        return await self.inteiro(
            ChaveParametro.TTL_RESERVA_PAGAMENTO_MIN, self.settings.ttl_reserva_pagamento_min
        )
