"""Object storage compatível com S3 — MinIO em dev, S3/MinIO em produção.

Duas formas de servir um arquivo, e a escolha depende do que ele é:

1. **Link temporário (presigned).** O storage assina uma URL válida por alguns
   segundos. Bom para a **foto de perfil**: precisa renderizar num ``<img>``, e
   o dano de um link vazado é pequeno.

2. **Stream pela aplicação.** O app lê o objeto, decifra e devolve. É o caminho
   do **documento de identidade**: um link, por mais curto que seja, pode ser
   encaminhado, cair num log de proxy ou ficar no histórico do navegador. Para
   uma cópia de RG isso não compensa — e como o arquivo sobe cifrado com chave
   da aplicação, um presigned nem seria legível.

O bucket é **privado** em qualquer caso. A autorização (dono ou admin) é do
aplicativo; o storage nunca é a fronteira de acesso.

``boto3`` é síncrono; as chamadas vão para ``asyncio.to_thread`` para não
bloquear o event loop. Upload de foto e de documento são raros — não justificam
mais uma dependência async.
"""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.core.config import Settings
from app.core.erros import ErroDominio, NaoEncontrado
from app.core.logging import get_logger

log = get_logger(__name__)

#: TTL curto de propósito: o link serve para a página que está abrindo agora.
TTL_PADRAO_SEGUNDOS = 60


class FalhaNoArmazenamento(ErroDominio):
    codigo = "falha_armazenamento"
    status_http = 503
    mensagem_padrao = "Não foi possível guardar o arquivo agora. Tente novamente."


class S3StorageProvider:
    """Funciona com MinIO e com qualquer storage S3-compatível."""

    nome = "s3"

    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._cliente = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url or None,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            # MinIO exige path-style; a AWS aceita os dois.
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path" if settings.s3_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
            use_ssl=settings.s3_endpoint_url.startswith("https://")
            if settings.s3_endpoint_url
            else True,
        )

    # --- Ciclo de vida ------------------------------------------------------

    async def garantir_bucket(self) -> None:
        """Cria o bucket se não existir, **privado**.

        Chamado no lifespan em dev. Em produção o bucket normalmente já existe
        com política e criptografia definidas pela infraestrutura.
        """

        def _criar() -> None:
            try:
                self._cliente.head_bucket(Bucket=self._bucket)
                return
            except ClientError as exc:
                codigo = exc.response.get("Error", {}).get("Code", "")
                if codigo not in ("404", "NoSuchBucket", "NotFound"):
                    raise
            self._cliente.create_bucket(Bucket=self._bucket)
            # Sem isto, um bucket recém-criado poderia herdar política pública
            # de uma configuração de servidor permissiva.
            self._cliente.put_public_access_block(
                Bucket=self._bucket,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )

        try:
            await asyncio.to_thread(_criar)
        except ClientError as exc:
            # PutPublicAccessBlock é uma API da AWS que o MinIO não implementa.
            # Lá o bucket já nasce privado (verificado: acesso sem assinatura
            # devolve 403), então isto é informativo, não falha.
            codigo = exc.response.get("Error", {}).get("Code", "")
            if codigo in ("MalformedXML", "NotImplemented", "InvalidRequest"):
                log.info("storage.public_access_block_indisponivel", provedor="minio")
            else:
                log.warning("storage.bucket_config_parcial", erro=str(exc))

    # --- Operações ----------------------------------------------------------

    async def salvar(self, caminho: str, conteudo: bytes, content_type: str) -> str:
        """Grava e devolve a **chave** — não uma URL.

        Devolver chave e não URL é intencional: quem decide se alguém pode ver
        o arquivo é o app, na hora do acesso, não o momento do upload.
        """
        extras: dict[str, Any] = {"ContentType": content_type}

        def _upload() -> None:
            self._cliente.put_object(Bucket=self._bucket, Key=caminho, Body=conteudo, **extras)

        try:
            await asyncio.to_thread(_upload)
        except ClientError as exc:
            log.error("storage.falha_upload", caminho=caminho, erro=str(exc))
            raise FalhaNoArmazenamento() from exc
        return caminho

    async def ler(self, caminho: str) -> bytes:
        def _download() -> bytes:
            resposta = self._cliente.get_object(Bucket=self._bucket, Key=caminho)
            corpo: bytes = resposta["Body"].read()
            return corpo

        try:
            return await asyncio.to_thread(_download)
        except ClientError as exc:
            codigo = exc.response.get("Error", {}).get("Code", "")
            if codigo in ("NoSuchKey", "404"):
                raise NaoEncontrado("Arquivo não encontrado.") from exc
            raise FalhaNoArmazenamento() from exc

    async def remover(self, caminho: str) -> None:
        def _delete() -> None:
            self._cliente.delete_object(Bucket=self._bucket, Key=caminho)

        try:
            await asyncio.to_thread(_delete)
        except ClientError as exc:
            log.warning("storage.falha_remocao", caminho=caminho, erro=str(exc))

    async def url_temporaria(self, caminho: str, ttl_segundos: int = TTL_PADRAO_SEGUNDOS) -> str:
        """URL assinada, válida por ``ttl_segundos``.

        **Só para arquivo não cifrado pelo app** (foto de perfil). Para
        documento cifrado, use ``ler()`` e sirva pela rota autorizada — o
        presigned entregaria bytes ilegíveis, e um link de RG não deve circular.
        """

        def _assinar() -> str:
            url: str = self._cliente.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": caminho},
                ExpiresIn=ttl_segundos,
            )
            return url

        try:
            return await asyncio.to_thread(_assinar)
        except ClientError as exc:
            raise FalhaNoArmazenamento() from exc
