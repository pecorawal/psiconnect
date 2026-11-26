# Imagem da aplicação para rodar o PsiConnect inteiro no podman em dev.
#
#   make app-up     # constrói e sobe app (porta 8000) + worker
#
# Não é a imagem de produção (Fase 7): instala o requirements.txt completo,
# inclusive as ferramentas de dev, e lê a configuração do .env da máquina em
# tempo de execução. Nenhum segredo entra na imagem (.containerignore).

FROM docker.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/psiconnect

# Dependências primeiro: a camada só é refeita quando o requirements.txt muda.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN useradd --system --uid 10001 --home-dir /srv/psiconnect psiconnect \
    && mkdir -p storage_local \
    && chown psiconnect storage_local
USER psiconnect

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
