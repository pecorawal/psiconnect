---
name: sincronizar
description: Sincroniza o repositório PsiConnect com o GitHub seguindo o fluxo dev → PR automático → main — valida, comita, envia a dev e traz de volta os commits que o Actions faz na main. Use quando o usuário pedir para sincronizar, subir, enviar, dar push, commitar ou atualizar as branches.
---

# Sincronizar com o remoto

Fluxo do projeto:

- Trabalho acontece em `dev`. Push em `dev` → CI + PR rolante
  "Integração dev -> main" (`.github/workflows/auto-pr-dev.yaml`).
- O merge na `main` é feito pelo usuário no GitHub. Depois dele, o Actions
  comita `docs: generate README tree` na `main` (`readme-tree.yaml`).
- Nunca `push --force`, nunca comitar direto na `main`, nunca reescrever
  histórico publicado.

## Passos

1. **Estado**
   ```bash
   git fetch --all --prune
   git status -sb && git branch -vv
   ```
2. **Validar antes de enviar** (Postgres precisa estar no ar —
   `systemctl --user start podman.socket && make infra-up` se preciso):
   ```bash
   make lint && .venv/bin/mypy app && .venv/bin/pytest -q
   ```
   Falhou: pare e mostre ao usuário. Não envie código vermelho.
3. **Segredos**: antes de comitar, confira `git diff --cached` — nenhum token,
   senha ou chave, nem de dev (o GitGuardian bloqueia o PR). `.env` é ignorado;
   valores novos vão vazios no `.env.example` e gerados em `scripts/gerar_env.py`.
4. **Commit** (só se o usuário pediu): mensagem `tipo(escopo): descrição` em
   português, corpo explicando o porquê.
5. **Trazer o remoto**:
   - `dev` atrás de `origin/dev`: `git pull --ff-only`.
   - `main` local: `git fetch origin main:main`.
   - Se a `main` tem commits que a `dev` não tem (merge do PR, README tree):
     `git merge origin/main` na `dev`. Conflito no `README.md` costuma ser o
     bloco `readme-tree` — mantenha o texto da `dev` e a árvore da `main`.
6. **Enviar**: `git push origin dev`.
7. **Relatar**: hashes enviados, se o PR automático foi aberto/atualizado
   (`gh pr list --head dev` se o `gh` estiver autenticado) e o resultado do CI
   quando disponível (`gh run list --branch dev -L 3`).

Se a branch divergir (commits dos dois lados que não são da `main`), não
resolva sozinho: mostre `git log --oneline --graph dev origin/dev` e pergunte.
