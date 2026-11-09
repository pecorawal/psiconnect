---
name: proxima-tarefa
description: Escolhe e entrega um item do backlog do PsiConnect seguindo as convenções do projeto (porta + fake, testes de contrato, migration, ADR, atualização de backlog e roadmap). Use quando o usuário pedir "próxima tarefa", "o que falta", "pegar item do backlog", "implementar <item>" ou citar um código como S1, N1, V2, T1.
---

# Pegar e entregar um item do backlog

## 1. Escolher

- Leia `docs/BACKLOG.md`. Se o usuário citou um item (ex.: `N1`), use esse.
- Senão, proponha o item P0/P1 de maior prioridade que **não** esteja ⛔ e não
  dependa de decisão de negócio em aberto; espere o "ok" antes de codar.
- Item que depende de decisão (D1–D6, decisões nº 2–5 do roadmap): apresente as
  opções com uma recomendação e pergunte — não decida pelo dono do produto.

## 2. Entender antes de mexer

Leia o ADR e a seção do `docs/` relacionados, e o código vizinho. Padrões do
projeto:

- Serviço externo → interface em `app/providers/base.py`, fake **completo** (não
  no-op), real atrás de import tardio em `providers/registry.py`, falha no boot
  sem credencial, e suíte em `tests/contratos/` que roda os dois (real via
  `respx`, sem rede).
- Regra de negócio pura → `app/services/regras/` com teste em `tests/unit/`.
- Schema → só por migration Alembic (`make nova-migration m="..."`, revisar,
  `DROP TYPE` no downgrade de ENUM novo).
- Parâmetro de negócio ajustável → `ParametroSistema` + seed, não constante.
- Tela → template Jinja + HTMX em `app/web/rotas/`; teste em `tests/web/`.
- Autorização → permissão por módulo (ADR 0009); recurso alheio responde 404.
- Nada de PII em log; nada clínico no cache do service worker.

## 3. Entregar

1. Implementação + testes no mesmo commit.
2. `make lint && .venv/bin/mypy app && .venv/bin/pytest -q` verde (com Postgres).
3. Decisão de arquitetura nova → `docs/adr/NNNN-*.md` e linha em `docs/README.md`.
4. Riscar o item em `docs/BACKLOG.md` (`~~texto~~` + hash) e atualizar a fase em
   `docs/05-roadmap.md`.
5. Commit `tipo(escopo): ...` em português. Push só se o usuário pedir — então
   siga `/sincronizar`.

## 4. Relatar

O que mudou, como foi verificado (comandos e resultado), o que ficou de fora e o
próximo item sugerido.
