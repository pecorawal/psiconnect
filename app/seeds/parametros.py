"""Seed dos parâmetros de sistema.

Idempotente: só insere o que ainda não existe. Rodar de novo **não** sobrescreve
valores que o admin já ajustou pelo painel.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.parametro import ChaveParametro, ParametroSistema


class Definicao(NamedTuple):
    chave: str
    valor: Any
    tipo: str
    descricao: str


def definicoes(s: Settings) -> list[Definicao]:
    return [
        Definicao(
            ChaveParametro.COMISSAO_PERCENTUAL,
            s.comissao_percentual_padrao,
            "decimal",
            "Percentual retido pela plataforma sobre cada atendimento concluído. "
            "As fontes originais divergiam (5% no funcionalidades.md, 3% no mapa "
            "mental); ver docs/adr/0005-comissao-parametrizavel.md.",
        ),
        Definicao(
            ChaveParametro.DURACAO_SESSAO_MIN,
            s.duracao_sessao_padrao_min,
            "int",
            "Duração padrão da sessão em minutos. O profissional pode sobrescrever.",
        ),
        Definicao(
            ChaveParametro.INTERVALO_ENTRE_SESSOES_MIN,
            s.intervalo_entre_sessoes_min,
            "int",
            "Intervalo mínimo entre duas sessões consecutivas do mesmo profissional.",
        ),
        Definicao(
            ChaveParametro.LIMITE_HORAS_DIA,
            s.limite_horas_dia_profissional,
            "int",
            "Máximo de HORAS de atendimento por dia por profissional (regra R1). "
            "Contado em minutos reais, não em número de consultas.",
        ),
        Definicao(
            ChaveParametro.LIMITE_SESSOES_SEMANA,
            s.limite_sessoes_semana_paciente,
            "int",
            "Máximo de sessões por semana por paciente (regra R2).",
        ),
        Definicao(
            ChaveParametro.SUGESTAO_MAX_SEMANA,
            s.sugestao_max_semana,
            "int",
            "Quantas vezes por semana a agenda sugere repetir o mesmo horário (R3).",
        ),
        Definicao(
            ChaveParametro.TOLERANCIA_ATRASO_MIN,
            s.tolerancia_atraso_min,
            "int",
            "Minutos de atraso do paciente sem penalização (R7).",
        ),
        Definicao(
            ChaveParametro.ANTECEDENCIA_LINK_MIN,
            s.antecedencia_link_min,
            "int",
            "Antecedência com que a sala é criada e o link enviado (R8).",
        ),
        Definicao(
            ChaveParametro.JANELA_PONTUALIDADE_MIN,
            s.janela_pontualidade_min,
            "int",
            "Janela para o profissional ser considerado pontual e pontuar (R10).",
        ),
        Definicao(
            ChaveParametro.TTL_RESERVA_PAGAMENTO_MIN,
            s.ttl_reserva_pagamento_min,
            "int",
            "Tempo que o horário fica reservado enquanto o paciente paga. "
            "Expirado, o worker libera o slot.",
        ),
        Definicao(
            ChaveParametro.MAX_ESPECIALIDADES,
            s.max_especialidades_profissional,
            "int",
            "Máximo de especialidades por profissional (R4). O banco também "
            "garante isso via CHECK(ordem BETWEEN 1 AND 5) + UNIQUE.",
        ),
        Definicao(
            ChaveParametro.TAXA_PIX_PERCENTUAL,
            0.99,
            "decimal",
            "Taxa estimada do gateway para Pix, exibida no simulador de recebimento.",
        ),
        Definicao(
            ChaveParametro.TAXA_CREDITO_PERCENTUAL,
            4.98,
            "decimal",
            "Taxa estimada para cartão de crédito. ATENÇÃO: é maior que a comissão "
            "de 5%; ver questão aberta 12 no plano sobre quem absorve essa taxa.",
        ),
        Definicao(
            ChaveParametro.TAXA_DEBITO_PERCENTUAL,
            1.99,
            "decimal",
            "Taxa estimada para cartão de débito.",
        ),
    ]


async def semear_parametros(sessao: AsyncSession, settings: Settings) -> int:
    existentes = set((await sessao.execute(select(ParametroSistema.chave))).scalars().all())
    novos = 0
    for d in definicoes(settings):
        if d.chave in existentes:
            continue
        sessao.add(
            ParametroSistema(chave=d.chave, valor=d.valor, tipo=d.tipo, descricao=d.descricao)
        )
        novos += 1
    return novos
