"""Notificação para desenvolvimento.

Registra o envio no log estruturado e devolve sucesso. A ``Notificacao`` já está
gravada no banco pelo outbox, então tudo fica visível em ``/dev/notificacoes``
sem depender de SMTP nem da Cloud API do WhatsApp.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.logging import get_logger
from app.models.enums import CanalNotificacao
from app.providers.base import ResultadoEnvio

log = get_logger(__name__)


class ConsoleNotificationProvider:
    nome = "console"

    def suporta(self, canal: CanalNotificacao) -> bool:
        return True

    async def enviar(
        self,
        canal: CanalNotificacao,
        destino: str,
        template: str,
        contexto: Mapping[str, Any],
        chave_idempotencia: str,
    ) -> ResultadoEnvio:
        # `destino` é e-mail ou telefone -- PII. O processador redigir_pii não
        # cobre a chave "destino", então mascaramos aqui.
        log.info(
            "notificacao.simulada",
            canal=canal.value,
            template=template,
            destino=_mascarar(destino),
            chave_idempotencia=chave_idempotencia,
        )
        return ResultadoEnvio(sucesso=True, provedor_msg_id=f"console-{chave_idempotencia[:12]}")


def _mascarar(destino: str) -> str:
    if "@" in destino:
        usuario, _, dominio = destino.partition("@")
        visivel = usuario[:2] if len(usuario) > 2 else usuario[:1]
        return f"{visivel}***@{dominio}"
    return f"***{destino[-4:]}" if len(destino) > 4 else "***"
