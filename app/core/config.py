"""Configuração da aplicação.

Separação deliberada entre dois tipos de configuração:

* ``Settings`` (este módulo) -- infraestrutura e segredos. Vem do ambiente,
  é imutável em runtime, e mudá-la exige redeploy.
* ``ParametroSistema`` (tabela no banco) -- regras de negócio mutáveis pelo
  admin sem deploy: comissão, duração da sessão, limites.

Os valores de negócio que aparecem aqui servem apenas de *bootstrap* para o
seed inicial de ``ParametroSistema``. Depois do seed, quem manda é o banco.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ_PROJETO = Path(__file__).resolve().parent.parent.parent


class Ambiente(StrEnum):
    DEV = "dev"
    TESTE = "teste"
    STAGING = "staging"
    PRODUCAO = "producao"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # --- Aplicação ---------------------------------------------------------
    app_env: Ambiente = Ambiente.DEV
    app_nome: str = "PsiConnect"
    app_secret_key: str = Field(min_length=32)
    app_base_url: str = "http://localhost:8000"
    app_debug: bool = False

    # --- Banco -------------------------------------------------------------
    database_url: PostgresDsn
    database_echo: bool = False

    tz_padrao: str = "America/Sao_Paulo"

    # --- Bootstrap dos parâmetros de negócio -------------------------------
    comissao_percentual_padrao: float = 12.0
    duracao_sessao_padrao_min: int = 50
    intervalo_entre_sessoes_min: int = 10
    limite_horas_dia_profissional: int = 10
    limite_sessoes_semana_paciente: int = 3
    sugestao_max_semana: int = 2
    tolerancia_atraso_min: int = 15
    antecedencia_link_min: int = 20
    janela_pontualidade_min: int = 5
    ttl_reserva_pagamento_min: int = 15
    max_especialidades_profissional: int = 5

    # --- Seleção de providers ---------------------------------------------
    pagamento_provider: Literal["fake", "mercadopago"] = "fake"
    video_provider: Literal["fake", "daily"] = "fake"
    notificacao_provider: Literal["console", "smtp"] = "console"
    transcricao_provider: Literal["fake", "whisper_api", "faster_whisper"] = "fake"
    embedding_provider: Literal["fake", "openai", "local"] = "fake"
    notafiscal_provider: Literal["fake", "focus_nfe"] = "fake"
    armazenamento_provider: Literal["local", "s3"] = "local"

    # --- Mercado Pago ------------------------------------------------------
    mercadopago_access_token: str = ""
    mercadopago_public_key: str = ""
    mercadopago_client_id: str = ""
    mercadopago_client_secret: str = ""
    mercadopago_webhook_secret: str = ""

    # --- daily.co ----------------------------------------------------------
    daily_api_key: str = ""
    daily_domain: str = ""

    # --- E-mail ------------------------------------------------------------
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_usuario: str = ""
    smtp_senha: str = ""
    smtp_tls: bool = False
    email_remetente: str = "nao-responda@psiconnect.com.br"

    # --- WhatsApp ----------------------------------------------------------
    whatsapp_phone_number_id: str = ""
    whatsapp_token: str = ""

    # --- IA ----------------------------------------------------------------
    embedding_dim: int = 1536
    openai_api_key: str = ""
    chave_cripto_transcricao: str = ""

    # --- Armazenamento -----------------------------------------------------
    armazenamento_local_dir: Path = RAIZ_PROJETO / "storage_local"
    #: MinIO em dev; qualquer storage S3-compatível em produção.
    s3_endpoint_url: str = "http://localhost:9010"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "psiconnect"
    s3_region: str = "us-east-1"
    #: MinIO exige path-style (bucket no caminho, não no subdomínio).
    s3_path_style: bool = True
    #: TTL do link assinado de foto de perfil.
    s3_url_ttl_segundos: int = 60

    # --- Validações --------------------------------------------------------
    @field_validator("app_secret_key")
    @classmethod
    def _secret_nao_pode_ser_o_exemplo(cls, v: str) -> str:
        if v.startswith("troque-em-producao"):
            # Em dev é aceitável; o guarda de produção está em `validar_para_producao`.
            return v
        return v

    @property
    def eh_dev(self) -> bool:
        return self.app_env is Ambiente.DEV

    @property
    def eh_producao(self) -> bool:
        return self.app_env is Ambiente.PRODUCAO

    @property
    def permite_rotas_dev(self) -> bool:
        """Se as rotas ``/dev`` devem existir.

        Dev e teste sim; **staging e produção nunca** -- são atalhos que pulam
        pagamento e aprovação de cadastro, e não podem ser alcançáveis onde há
        gente real.
        """
        return self.app_env in (Ambiente.DEV, Ambiente.TESTE)

    @property
    def cookies_seguros(self) -> bool:
        """Se os cookies devem levar a flag ``Secure``.

        Amarrado ao esquema real da URL, não ao nome do ambiente: um cookie
        ``Secure`` simplesmente não é enviado sobre ``http://``, então marcar
        por ambiente quebraria qualquer staging ou teste servido sem TLS -- a
        sessão sumiria sem erro visível. ``validar_para_producao`` já garante
        que produção seja https.
        """
        return self.app_base_url.startswith("https://")

    @property
    def database_url_sync(self) -> str:
        """URL para o Alembic, que roda em modo síncrono.

        O dialeto ``postgresql+psycopg`` do SQLAlchemy atende os dois modos: o
        que decide é ``create_engine`` (sync) versus ``create_async_engine``
        (async). Por isso a URL é a mesma da aplicação.

        Cuidado: reescrever para ``postgresql://`` faria o SQLAlchemy cair no
        dialeto padrão, que é psycopg2 -- dependência que este projeto não tem.
        """
        return str(self.database_url)

    def validar_armazenamento(self) -> None:
        """Recusa subir com storage S3 sem credencial.

        Sem isto o erro só aparece no primeiro upload, como
        ``InvalidAccessKeyId`` vindo do boto3 — mensagem que não diz o que
        fazer. Falhar no lifespan aponta direto para a variável faltando.
        """
        if self.armazenamento_provider != "s3":
            return
        faltando = [
            nome
            for nome, valor in (
                ("S3_ACCESS_KEY", self.s3_access_key),
                ("S3_SECRET_KEY", self.s3_secret_key),
                ("S3_BUCKET", self.s3_bucket),
            )
            if not valor
        ]
        if faltando:
            raise RuntimeError("ARMAZENAMENTO_PROVIDER=s3 exige: " + ", ".join(faltando))

    def validar_para_producao(self) -> None:
        """Falha cedo se a configuração for insegura para produção.

        Chamado no lifespan. É melhor a aplicação não subir do que subir com
        secret de exemplo num sistema que trata dado sensível de saúde.
        """
        if not self.eh_producao:
            return
        problemas: list[str] = []
        if self.app_secret_key.startswith("troque-em-producao"):
            problemas.append("APP_SECRET_KEY ainda é o valor de exemplo")
        if self.app_debug:
            problemas.append("APP_DEBUG está ligado")
        if not self.app_base_url.startswith("https://"):
            problemas.append("APP_BASE_URL precisa ser https em produção")
        if self.pagamento_provider == "fake":
            problemas.append("PAGAMENTO_PROVIDER está como fake")
        if self.video_provider == "fake":
            problemas.append("VIDEO_PROVIDER está como fake")
        if problemas:
            raise RuntimeError("Configuração inválida para produção: " + "; ".join(problemas))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
