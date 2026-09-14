"""Armazenamento em disco local — apenas para desenvolvimento sem MinIO.

Cumpre o mesmo contrato do provider S3, inclusive os "links temporários": aqui
eles são URLs assinadas pela própria aplicação, com validade curta. Não é
equivalente a um presigned de verdade (o arquivo continua no disco do processo),
mas mantém o código das rotas idêntico nos dois ambientes.

Em produção use ``ARMAZENAMENTO_PROVIDER=s3``.
"""

from __future__ import annotations

import re
from pathlib import Path

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import Settings
from app.core.erros import NaoEncontrado

#: Só o que é seguro num nome de arquivo.
SEGURO = re.compile(r"[^a-zA-Z0-9._/-]")
TTL_PADRAO_SEGUNDOS = 60


class LocalStorageProvider:
    nome = "local"

    def __init__(self, settings: Settings) -> None:
        self._raiz = Path(settings.armazenamento_local_dir).resolve()
        self._base_url = settings.app_base_url.rstrip("/")
        self._assinador = URLSafeTimedSerializer(settings.app_secret_key, salt="psiconnect.midia")
        self._raiz.mkdir(parents=True, exist_ok=True)

    def _resolver(self, caminho: str) -> Path:
        limpo = SEGURO.sub("_", caminho).lstrip("/")
        destino = (self._raiz / limpo).resolve()
        # Path traversal: "../../etc/passwd" não pode escapar da raiz.
        if not destino.is_relative_to(self._raiz):
            raise ValueError(f"caminho fora do diretório de armazenamento: {caminho!r}")
        return destino

    async def salvar(self, caminho: str, conteudo: bytes, content_type: str) -> str:
        destino = self._resolver(caminho)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(conteudo)
        # Devolve a CHAVE, não uma URL: quem autoriza o acesso é o app.
        return caminho

    async def ler(self, caminho: str) -> bytes:
        destino = self._resolver(caminho)
        if not destino.is_file():
            raise NaoEncontrado("Arquivo não encontrado.")
        return destino.read_bytes()

    async def remover(self, caminho: str) -> None:
        self._resolver(caminho).unlink(missing_ok=True)

    async def url_temporaria(self, caminho: str, ttl_segundos: int = TTL_PADRAO_SEGUNDOS) -> str:
        token = self._assinador.dumps(caminho)
        return f"{self._base_url}/midia/{token}"

    def validar_url_temporaria(self, token: str, ttl_segundos: int) -> str:
        """Devolve a chave se o token for válido. Usado pela rota ``/midia``."""
        try:
            caminho = self._assinador.loads(token, max_age=ttl_segundos)
        except (BadSignature, SignatureExpired) as exc:
            raise NaoEncontrado("Link expirado.") from exc
        return str(caminho)
