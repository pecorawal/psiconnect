"""Montagem dos providers a partir das settings.

Chamado uma vez no ``lifespan`` e guardado em ``app.state.providers``. Os testes
substituem o objeto inteiro, ou trocam um provider específico.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import Settings
from app.core.erros import ProviderIndisponivel
from app.providers.armazenamento.local import LocalStorageProvider
from app.providers.base import (
    NotificationProvider,
    PaymentProvider,
    StorageProvider,
    VideoProvider,
)
from app.providers.notificacao.console import ConsoleNotificationProvider
from app.providers.pagamento.fake import FakePaymentProvider
from app.providers.video.fake import FakeVideoProvider


@dataclass(frozen=True, slots=True)
class Providers:
    pagamento: PaymentProvider
    video: VideoProvider
    notificacao: NotificationProvider
    armazenamento: StorageProvider


def montar_providers(settings: Settings) -> Providers:
    return Providers(
        pagamento=_pagamento(settings),
        video=_video(settings),
        notificacao=_notificacao(settings),
        armazenamento=_armazenamento(settings),
    )


def _pagamento(settings: Settings) -> PaymentProvider:
    if settings.pagamento_provider == "fake":
        return FakePaymentProvider()
    if settings.pagamento_provider == "mercadopago":
        from app.providers.pagamento.mercadopago import MercadoPagoPaymentProvider

        # Falhar aqui, no boot, é melhor do que falhar no primeiro checkout: o
        # erro aparece para quem está subindo a aplicação, não para o paciente.
        if not settings.mercadopago_access_token:
            raise ProviderIndisponivel("MERCADOPAGO_ACCESS_TOKEN não configurado.")
        if not settings.mercadopago_webhook_secret:
            raise ProviderIndisponivel(
                "MERCADOPAGO_WEBHOOK_SECRET não configurado — sem ele o webhook "
                "aceitaria confirmação de pagamento forjada."
            )
        return MercadoPagoPaymentProvider(settings)
    raise ProviderIndisponivel(
        f"Provedor de pagamento '{settings.pagamento_provider}' ainda não implementado."
    )


def _video(settings: Settings) -> VideoProvider:
    if settings.video_provider == "fake":
        return FakeVideoProvider(settings)
    if settings.video_provider == "daily":
        from app.providers.video.daily import DailyVideoProvider

        # Mesmo critério do pagamento: falhar no boot é melhor do que falhar na
        # primeira sessão -- o erro aparece para quem sobe a aplicação, não para
        # o paciente que já está esperando na tela.
        if not settings.daily_api_key:
            raise ProviderIndisponivel("DAILY_API_KEY não configurado.")
        if not settings.daily_domain:
            raise ProviderIndisponivel(
                "DAILY_DOMAIN não configurado — sem ele a URL da sala fica inválida."
            )
        return DailyVideoProvider(settings)
    raise ProviderIndisponivel(
        f"Provedor de vídeo '{settings.video_provider}' ainda não implementado."
    )


def _notificacao(settings: Settings) -> NotificationProvider:
    if settings.notificacao_provider == "console":
        return ConsoleNotificationProvider()
    if settings.notificacao_provider == "smtp":
        from app.providers.notificacao.smtp import SmtpEmailProvider

        # Em produção, e-mail sem TLS ou sem autenticação expõe o conteúdo e
        # o endereço do paciente na rede. Falha no boot, como os outros.
        if settings.eh_producao:
            if not (settings.smtp_tls or settings.smtp_port == 465):
                raise ProviderIndisponivel("SMTP_TLS precisa estar ligado em produção.")
            if not (settings.smtp_usuario and settings.smtp_senha):
                raise ProviderIndisponivel("SMTP_USUARIO e SMTP_SENHA não configurados.")
        return SmtpEmailProvider(settings)
    # WhatsApp chega com os templates HSM aprovados (N3).
    raise ProviderIndisponivel(
        f"Provedor de notificação '{settings.notificacao_provider}' ainda não implementado."
    )


def _armazenamento(settings: Settings) -> StorageProvider:
    if settings.armazenamento_provider == "local":
        return LocalStorageProvider(settings)
    if settings.armazenamento_provider == "s3":
        from app.providers.armazenamento.s3 import S3StorageProvider

        return S3StorageProvider(settings)
    raise ProviderIndisponivel(
        f"Provedor de armazenamento '{settings.armazenamento_provider}' ainda não implementado."
    )
