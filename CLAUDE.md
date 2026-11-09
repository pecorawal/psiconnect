# PsiConnect — guia para o Claude

Plataforma que conecta psicólogos e pacientes (agenda, pagamento, sessão por
vídeo). Dado pessoal sensível de saúde (LGPD art. 11): privacidade pesa em toda
decisão. Toda a documentação, código e commits são em **português**.

## Por onde começar

1. `docs/BACKLOG.md` — próximo item a pegar e o estado atual.
2. `docs/05-roadmap.md` — fases e o que cada uma entrega.
3. `docs/adr/` — por que as coisas são como são. Não contrarie um ADR sem
   escrever outro.

Skills do projeto em `.claude/skills/`: `/retomar` (preparar um PC novo e
reorientar), `/sincronizar` (fluxo git dev → main) e `/proxima-tarefa`
(pegar e entregar um item do backlog).

## Stack e layout

Python 3.12 · FastAPI + Jinja2 + HTMX + Tailwind (sem Node em produção) ·
PostgreSQL 17 + pgvector · Alembic · MinIO (S3) · cookie de sessão httpOnly ·
Argon2id via `pwdlib`.

- `app/web/rotas/` páginas HTMX · `app/api/` saúde e webhooks
- `app/services/` regras (as puras em `services/regras/`)
- `app/providers/` **portas** para serviços externos, cada uma com um fake
  completo; escolhidas por env (`PAGAMENTO_PROVIDER=fake|mercadopago`,
  `VIDEO_PROVIDER=fake|daily`, ...) em `providers/registry.py`, que falha no
  boot se faltar credencial do provider real.
- `app/workers/` outbox com retry, salas em T-20min, expiração de reservas,
  no-show. Roda com `make worker`.
- Parâmetros de negócio mutáveis (comissão 12%, limites) ficam na tabela
  `ParametroSistema`; o `.env` só faz o bootstrap pelo seed.

## Ambiente local

```bash
systemctl --user start podman.socket   # Fedora: sem isto o "podman compose" falha
make bootstrap    # gera .env (scripts/gerar_env.py), venv, deps, containers, migrate, seed
make seed-demo    # psi@ / pac@ / admin@demo.br, senha psiconnect123
make dev          # http://localhost:8000  (outra porta: make PORTA=8010 dev)
```

Portas: Postgres **5433**, Mailpit 8025 (SMTP 1025), MinIO 9010 (console 9011).
O `.env` nunca é versionado e `make env` nunca sobrescreve valor existente.
Nenhum segredo entra no repositório, nem de dev ou CI (o GitGuardian vigia).

## Qualidade

- `make ci` = `ruff check` + `ruff format --check` + `mypy --strict` + `pytest`
  com cobertura. **Quase todo teste precisa do Postgres no ar** — inclusive
  `make test-rapido`. Antes de testar: containers de pé, `alembic upgrade head`
  e `python -m app.seeds`.
- Integração nova = suíte de contrato em `tests/contratos/` rodando contra o
  fake e contra o real com `respx`, sem tocar a rede.
- `create_all()` é proibido fora de testes; schema só por migration
  (`make nova-migration m="..."`, e ENUM novo precisa de `DROP TYPE` no
  downgrade).

## Git

- Trabalho em `dev`. Cada push em `dev` roda o CI e abre/atualiza o PR
  "Integração dev -> main" (`auto-pr-dev.yaml`).
- Em `main`, o Actions comita sozinho `docs: generate README tree`; depois de
  um merge, traga a `main` para a `dev` antes de seguir.
- Commits no estilo `tipo(escopo): descrição em português`
  (feat, fix, docs, test, ci, style).
- Ao concluir algo: riscar no `docs/BACKLOG.md` e atualizar a fase no
  `docs/05-roadmap.md` no mesmo commit; decisão de arquitetura nova vira ADR.

## Regras que não se negociam

- Não gravar áudio nem vídeo, só transcrição cifrada (ADR 0003).
- O service worker do PWA não cacheia dado clínico (ADR 0012; há teste que lê
  o `sw.js`).
- Paciente pedindo recurso de outro recebe 404, não 403.
- Paciente sem `paciente_admitido_em` não recebe meeting token.
- Nada de PII em log (`app/core/logging.py` faz a redação por nome de chave).
