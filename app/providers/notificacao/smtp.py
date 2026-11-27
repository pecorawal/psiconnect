"""E-mail por SMTP (N1). Em dev aponta para o Mailpit (UI em localhost:8025).

Só atende o canal EMAIL. WhatsApp e push continuam sem provedor real até a
Fase 3 terminar; o worker cancela essas linhas com "canal indisponível" em vez
de fingir que enviou.

O texto de cada mensagem é um template Jinja em ``app/templates/email/``, um
arquivo ``<template>.txt`` por nome de template da outbox. O assunto vem de um
``{% set assunto %}`` no próprio arquivo. Texto puro de propósito: sem HTML não
há pixel de rastreio nem imagem remota para vazar quem abriu o e-mail.

``StrictUndefined``: contexto faltando é erro, a linha vai para retry e depois
para FALHA com o motivo, em vez de sair um e-mail com buraco no meio.

``smtplib`` é síncrono; o envio vai para ``asyncio.to_thread`` para não travar o
loop do worker.
"""

from __future__ import annotations

import asyncio
import hashlib
import smtplib
import ssl
from collections.abc import Mapping
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from app.core.config import Settings
from app.core.logging import get_logger
from app.models.enums import CanalNotificacao
from app.providers.base import ResultadoEnvio

log = get_logger(__name__)

DIR_TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "email"

#: Porta do SMTP com TLS implícito (SMTPS). Nas outras, TLS é via STARTTLS.
PORTA_SMTPS = 465


class TemplateDeEmailAusente(LookupError):
    """Template da outbox sem texto de e-mail correspondente."""


class RenderizadorEmail:
    def __init__(self, base_url: str, diretorio: Path = DIR_TEMPLATES) -> None:
        self.base_url = base_url.rstrip("/")
        self.env = Environment(
            loader=FileSystemLoader(diretorio),
            undefined=StrictUndefined,
            # Texto puro, não HTML: escapar viraria "&amp;" no nome do paciente.
            autoescape=False,  # noqa: S701
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def templates(self) -> set[str]:
        return {nome.removesuffix(".txt") for nome in self.env.list_templates(extensions=["txt"])}

    def renderizar(self, template: str, contexto: Mapping[str, Any]) -> tuple[str, str]:
        """Devolve ``(assunto, corpo)``."""
        try:
            modelo = self.env.get_template(f"{template}.txt")
        except TemplateNotFound as exc:
            raise TemplateDeEmailAusente(template) from exc
        modulo = modelo.make_module({**contexto, "base_url": self.base_url})
        assunto = " ".join(str(getattr(modulo, "assunto", "")).split())
        if not assunto:
            raise TemplateDeEmailAusente(f"{template}: falta o assunto")
        return assunto, str(modulo).strip() + "\n"


class SmtpEmailProvider:
    nome = "smtp"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.renderizador = RenderizadorEmail(settings.app_base_url)

    def suporta(self, canal: CanalNotificacao) -> bool:
        return canal is CanalNotificacao.EMAIL

    async def enviar(
        self,
        canal: CanalNotificacao,
        destino: str,
        template: str,
        contexto: Mapping[str, Any],
        chave_idempotencia: str,
    ) -> ResultadoEnvio:
        if not self.suporta(canal):
            return ResultadoEnvio(sucesso=False, erro=f"canal {canal.value} não suportado")

        assunto, corpo = self.renderizador.renderizar(template, contexto)
        mensagem = self._montar(destino, assunto, corpo, chave_idempotencia)
        try:
            await asyncio.to_thread(self._entregar, mensagem)
        except (smtplib.SMTPException, OSError) as exc:
            # Só o tipo e o código: a mensagem do servidor costuma repetir o
            # endereço do destinatário, e isto vai para o banco e para o log.
            codigo = getattr(exc, "smtp_code", None)
            erro = type(exc).__name__ + (f" {codigo}" if codigo else "")
            log.warning("email.falha_envio", template=template, erro=erro)
            return ResultadoEnvio(sucesso=False, erro=erro)

        return ResultadoEnvio(sucesso=True, provedor_msg_id=str(mensagem["Message-ID"]))

    # --- Internos -----------------------------------------------------------

    def _montar(
        self, destino: str, assunto: str, corpo: str, chave_idempotencia: str
    ) -> EmailMessage:
        mensagem = EmailMessage()
        mensagem["From"] = formataddr((self.settings.app_nome, self.settings.email_remetente))
        mensagem["To"] = destino
        mensagem["Subject"] = assunto
        # Message-ID estável por chave: um retry depois de um envio que deu certo
        # mas não foi confirmado chega com o mesmo ID, e o cliente de e-mail
        # agrupa em vez de mostrar duas mensagens.
        dominio = self.settings.email_remetente.partition("@")[2] or "psiconnect.local"
        digest = hashlib.sha256(chave_idempotencia.encode()).hexdigest()[:32]
        mensagem["Message-ID"] = f"<{digest}@{dominio}>"
        mensagem.set_content(corpo)
        return mensagem

    def _entregar(self, mensagem: EmailMessage) -> None:
        s = self.settings
        contexto_tls = ssl.create_default_context()
        if s.smtp_port == PORTA_SMTPS:
            conexao: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=10, context=contexto_tls
            )
        else:
            conexao = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10)
        with conexao:
            if s.smtp_tls and s.smtp_port != PORTA_SMTPS:
                conexao.starttls(context=contexto_tls)
            if s.smtp_usuario:
                conexao.login(s.smtp_usuario, s.smtp_senha)
            conexao.send_message(mensagem)
