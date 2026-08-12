"""Armazenamento em disco local, para desenvolvimento."""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import Settings

#: Só o que é seguro num nome de arquivo servido pela web.
SEGURO = re.compile(r"[^a-zA-Z0-9._/-]")


class LocalStorageProvider:
    nome = "local"

    def __init__(self, settings: Settings) -> None:
        self._raiz = Path(settings.armazenamento_local_dir).resolve()
        self._base_url = settings.app_base_url.rstrip("/")
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
        relativo = destino.relative_to(self._raiz)
        return f"{self._base_url}/midia/{relativo}"

    async def remover(self, caminho: str) -> None:
        destino = self._resolver(caminho)
        destino.unlink(missing_ok=True)
