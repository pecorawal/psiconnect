#!/usr/bin/env bash
# Baixa o binário standalone do Tailwind CSS para tools/ (gitignored).
#
# Só é necessário em desenvolvimento: o build (app/static/css/app.css) é
# commitado de propósito para que produção não precise de Node.
set -euo pipefail

VERSAO="${TAILWIND_VERSAO:-v4.1.16}"
DESTINO="tools/tailwindcss"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64)   ALVO="linux-x64" ;;
  Linux-aarch64)  ALVO="linux-arm64" ;;
  Darwin-x86_64)  ALVO="macos-x64" ;;
  Darwin-arm64)   ALVO="macos-arm64" ;;
  *) echo "Plataforma não suportada: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

if [[ -x "${DESTINO}" ]]; then
  echo "Tailwind já presente em ${DESTINO} ($(${DESTINO} --help 2>&1 | head -1))"
  exit 0
fi

URL="https://github.com/tailwindlabs/tailwindcss/releases/download/${VERSAO}/tailwindcss-${ALVO}"

mkdir -p tools
echo "Baixando Tailwind ${VERSAO} (${ALVO})..."
curl -fsSL --retry 3 -o "${DESTINO}" "${URL}"
chmod +x "${DESTINO}"
echo "OK: ${DESTINO}"
