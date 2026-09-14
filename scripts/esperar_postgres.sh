#!/usr/bin/env bash
# Aguarda o Postgres do compose aceitar conexões.
set -euo pipefail

HOST="${PGHOST:-localhost}"
PORTA="${PGPORT:-5433}"
TENTATIVAS="${TENTATIVAS:-40}"

echo -n "Aguardando Postgres em ${HOST}:${PORTA}"
for _ in $(seq 1 "${TENTATIVAS}"); do
  # Testa a porta pelo host: funciona igual com docker, podman ou um Postgres
  # instalado localmente, sem depender do runtime de container.
  if (exec 3<>"/dev/tcp/${HOST}/${PORTA}") 2>/dev/null; then
    exec 3>&- 3<&-
    echo " OK"
    exit 0
  fi
  echo -n "."
  sleep 1
done

echo ""
echo "ERRO: Postgres não respondeu após ${TENTATIVAS}s." >&2
echo "Verifique com: podman compose logs db  (ou docker compose logs db)" >&2
exit 1
