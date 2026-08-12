"""Termos versionados e registro de consentimento.

O aceite registrado aponta para uma **versão específica** do texto, que carrega
o próprio hash SHA-256. É isso que permite responder, dois anos depois, à
pergunta "qual texto exatamente esta pessoa aceitou?" -- pergunta que um booleano
``aceitou_termos`` não responde.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import ErroDominio
from app.core.tempo import agora_utc
from app.models import AceiteTermo, TermoVersionado, TipoTermo, Usuario


class TermoNaoConfigurado(ErroDominio):
    codigo = "termo_nao_configurado"
    status_http = 500
    mensagem_padrao = "Documento legal não configurado. Avise o suporte."


class TermosService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    async def vigentes(self, *tipos: TipoTermo) -> dict[TipoTermo, TermoVersionado]:
        """Os termos em vigor para os tipos pedidos.

        Há um índice parcial garantindo no máximo um vigente por tipo, então não
        existe ambiguidade sobre qual texto é o atual.
        """
        linhas = (
            await self.sessao.execute(
                select(TermoVersionado).where(
                    TermoVersionado.tipo.in_(tipos),
                    TermoVersionado.vigente_ate.is_(None),
                )
            )
        ).scalars()
        return {t.tipo: t for t in linhas}

    async def registrar_aceite(
        self,
        usuario: Usuario,
        tipos: Iterable[TipoTermo],
        *,
        ip: str | None = None,
        user_agent: str | None = None,
        sessao_id: uuid.UUID | None = None,
    ) -> Sequence[AceiteTermo]:
        tipos = tuple(tipos)
        vigentes = await self.vigentes(*tipos)

        faltando = [t.value for t in tipos if t not in vigentes]
        if faltando:
            # Melhor falhar do que registrar um aceite sem texto associado --
            # isso não seria prova de nada.
            raise TermoNaoConfigurado(
                f"Sem versão vigente para: {', '.join(faltando)}. Rode o seed."
            )

        agora = agora_utc()
        aceites = [
            AceiteTermo(
                usuario_id=usuario.id,
                termo_id=vigentes[tipo].id,
                aceito_em=agora,
                ip=ip,
                user_agent=(user_agent or "")[:500] or None,
                sessao_id=sessao_id,
            )
            for tipo in tipos
        ]
        self.sessao.add_all(aceites)
        await self.sessao.flush()
        return aceites

    async def ja_aceitou(self, usuario_id: uuid.UUID, tipo: TipoTermo) -> bool:
        """Se aceitou a versão **vigente** -- não uma versão antiga."""
        resultado = await self.sessao.scalar(
            select(AceiteTermo.id)
            .join(TermoVersionado, AceiteTermo.termo_id == TermoVersionado.id)
            .where(
                AceiteTermo.usuario_id == usuario_id,
                AceiteTermo.revogado_em.is_(None),
                TermoVersionado.tipo == tipo,
                TermoVersionado.vigente_ate.is_(None),
            )
            .limit(1)
        )
        return resultado is not None
