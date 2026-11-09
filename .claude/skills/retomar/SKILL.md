---
name: retomar
description: Prepara o PsiConnect num PC novo (ou depois de muito tempo parado) e reorienta o trabalho — confere git, sobe a infra, roda os testes e resume o próximo passo do backlog. Use quando o usuário disser "retomar", "continuar de onde parou", "configurar em outro PC", "clonei o repo", ou abrir a sessão sem contexto.
---

# Retomar o trabalho no PsiConnect

Objetivo: em poucos minutos, deixar o ambiente funcionando e dizer ao usuário
onde o trabalho parou e qual é o próximo item. Faça os passos em ordem e relate
o resultado de cada um em uma linha.

## 1. Git

```bash
git fetch --all --prune
git status -sb
git branch -vv
git log --oneline --graph --all -15
```

- Se não estiver em `dev`, mude para `dev` (`git switch dev`; se não existir
  local, `git switch -c dev --track origin/dev`).
- Atrás do remoto e sem mudança local: `git pull --ff-only`.
- Commits locais não enviados ou divergência: **não** force nada; mostre ao
  usuário e siga a skill `/sincronizar`.
- `main` local atrasada: `git fetch origin main:main` (só fast-forward).

## 2. Ferramentas

Confira `python3.12`, `podman` (ou `docker`) e `gh`. Se o `gh` não estiver
autenticado, sugira ao usuário rodar `! gh auth login` — não é bloqueante.

## 3. Ambiente

```bash
systemctl --user start podman.socket     # só Fedora/podman; ignore se usar docker
make bootstrap                           # idempotente: .env, venv, deps, containers, migrate, seed
```

Se a venv já existir e só faltar a infra: `make infra-up`,
`./scripts/esperar_postgres.sh`, `make migrate`, `make seed`.

O `.env` é gerado na máquina (não vem do repo). Credenciais reais (Mercado
Pago, daily.co) **não** existem por padrão e os providers ficam `fake` — isso é
o esperado.

## 4. Verificação

```bash
make lint
.venv/bin/pytest -q          # precisa do Postgres no ar
```

Se algo falhar, diga o que falhou com a saída; não avance para codar em cima
de uma suíte vermelha sem avisar.

## 5. Reorientar

Leia `docs/BACKLOG.md` (seção "Estado" e os itens P0/P1) e o fim de
`git log --oneline -10`. Responda ao usuário com:

- estado do git (sincronizado? PR aberto?);
- estado da suíte;
- os 3 itens mais prioritários **não bloqueados (⛔)** do backlog;
- uma recomendação de qual pegar.

Se o "Estado" do backlog estiver desatualizado em relação ao git, atualize-o.
