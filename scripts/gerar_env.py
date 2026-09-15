#!/usr/bin/env python3
"""Cria o .env a partir do .env.example e preenche os segredos que faltam.

Nenhuma senha fica versionada, nem as de desenvolvimento: o ``.env.example``
traz as chaves vazias e este script gera valores aleatórios na máquina de quem
roda. Pode rodar quantas vezes quiser -- valor já preenchido nunca é
sobrescrito, então o Postgres e o MinIO que já subiram continuam aceitando as
credenciais de antes.

Uso: ``make env`` (ou ``python3 scripts/gerar_env.py``). Só biblioteca padrão,
porque roda antes de a venv existir.
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import shutil
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

RAIZ = Path(__file__).resolve().parent.parent
EXEMPLO = RAIZ / ".env.example"
ENV = RAIZ / ".env"

# Batem com o docker-compose.yml. Usuário e endereço não são segredo.
DB_USUARIO = "psiconnect"
DB_ENDERECO = "localhost:5433"
DB_NOME = "psiconnect"
MINIO_USUARIO = "psiconnect"

LINHA = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def ler(caminho: Path) -> dict[str, str]:
    valores: dict[str, str] = {}
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if casamento := LINHA.match(linha):
            valores[casamento[1]] = casamento[2].strip()
    return valores


def senha_da_url(url: str) -> str:
    """Senha embutida numa DATABASE_URL, ou vazio."""
    try:
        return unquote(urlsplit(url).password or "")
    except ValueError:
        return ""


def gerar(atuais: dict[str, str]) -> dict[str, str]:
    """Valores novos só para as chaves ausentes ou vazias."""
    novos: dict[str, str] = {}

    def falta(chave: str) -> bool:
        return not atuais.get(chave)

    if falta("APP_SECRET_KEY"):
        novos["APP_SECRET_KEY"] = secrets.token_urlsafe(48)

    if falta("POSTGRES_PASSWORD"):
        # .env anterior a este script: a senha já está na DATABASE_URL e o
        # volume do Postgres foi inicializado com ela. Gerar outra quebraria o
        # login no banco que já existe.
        herdada = senha_da_url(atuais.get("DATABASE_URL", ""))
        novos["POSTGRES_PASSWORD"] = herdada or secrets.token_urlsafe(24)
    senha_db = atuais.get("POSTGRES_PASSWORD") or novos["POSTGRES_PASSWORD"]

    if falta("DATABASE_URL"):
        novos["DATABASE_URL"] = (
            f"postgresql+psycopg://{DB_USUARIO}:{quote(senha_db, safe='')}@{DB_ENDERECO}/{DB_NOME}"
        )

    if falta("S3_ACCESS_KEY"):
        novos["S3_ACCESS_KEY"] = MINIO_USUARIO

    if falta("S3_SECRET_KEY"):
        # O MinIO lê as credenciais de root do ambiente a cada start, então uma
        # senha nova aqui não tranca dados que já estão no volume.
        novos["S3_SECRET_KEY"] = secrets.token_urlsafe(24)

    if falta("CHAVE_CRIPTO_TRANSCRICAO"):
        novos["CHAVE_CRIPTO_TRANSCRICAO"] = base64.b64encode(os.urandom(32)).decode()

    return novos


def aplicar(caminho: Path, novos: dict[str, str]) -> None:
    """Preenche a linha ``CHAVE=`` que já existe ou acrescenta a chave no fim."""
    linhas = caminho.read_text(encoding="utf-8").splitlines()
    pendentes = dict(novos)
    for i, linha in enumerate(linhas):
        casamento = LINHA.match(linha)
        if casamento and casamento[1] in pendentes and not casamento[2].strip():
            linhas[i] = f"{casamento[1]}={pendentes.pop(casamento[1])}"
    if pendentes:
        linhas += ["", "# --- Preenchido por scripts/gerar_env.py ---"]
        linhas += [f"{chave}={valor}" for chave, valor in pendentes.items()]
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def main() -> int:
    criado = not ENV.exists()
    if criado:
        shutil.copyfile(EXEMPLO, ENV)
        ENV.chmod(0o600)

    novos = gerar(ler(ENV))
    if novos:
        aplicar(ENV, novos)

    # Só os nomes: valor de segredo não vai para o terminal nem para log de CI.
    nomes = ", ".join(novos) or "nada"
    if criado:
        print(f".env criado a partir do .env.example; preenchidos: {nomes}")
    else:
        print(f".env já existia; preenchidos: {nomes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
