# PsiConnect

Plataforma de conexão entre psicólogos e pacientes: um ecossistema digital que
facilita o encontro entre profissionais de psicologia e pessoas que buscam
acompanhamento terapêutico — com sigilo, do computador, do tablet ou do celular.

> **Status:** Fases 0 e 1 concluídas — o fluxo completo funciona ponta a ponta.
> A Fase 2 saiu pela metade: adaptador do Mercado Pago, webhook, pacotes de
> crédito e painel financeiro estão prontos, mas tudo que depende de credencial
> real do Mercado Pago/Mercado Livre está **no backlog**. A Fase 4 está em
> andamento: sala de espera em tempo real, PWA instalável e o adaptador do
> daily.co já escrito, à espera de uma conta.
> O roadmap está em [`docs/05-roadmap.md`](docs/05-roadmap.md).

## Como funciona

1. **O paciente conta como se sente.** Seleciona sintomas, e a plataforma sugere
   profissionais com experiência naqueles temas.
2. **Escolhe o horário.** Vê a agenda real dos profissionais e reserva.
3. **É atendido.** A sessão acontece dentro da plataforma, em chamada de vídeo
   criptografada; o link chega antes do horário.

Do lado do profissional: cadastro com registro no conselho (CRP/CREFITO), até 5
especialidades com faixa de preço, e uma agenda semanal de disponibilidade.

## Stack

| Camada | Escolha | Por quê |
|---|---|---|
| Runtime | Python 3.12 | — |
| Web | FastAPI + Jinja2 + **HTMX** + Tailwind | Um codebase, sem Node em produção ([ADR 0001](docs/adr/0001-stack-fastapi-htmx.md)) |
| Banco | PostgreSQL 17 + **pgvector** | Relacional para cadastro/agenda, vetorial para transcrições |
| Migrations | Alembic | `create_all()` é proibido fora de testes |
| Auth | Cookie de sessão httpOnly | Revogação imediata, imune a XSS ([ADR 0002](docs/adr/0002-sessao-cookie-vs-jwt.md)) |
| Senhas | `pwdlib` / Argon2id | `passlib` está quebrado com bcrypt ≥ 4.1 ([ADR 0007](docs/adr/0007-pwdlib-argon2.md)) |
| Pagamento | Mercado Pago (split) | O dinheiro não transita pela plataforma ([ADR 0004](docs/adr/0004-mercado-pago-split.md)) |
| Vídeo | daily.co | Salas privadas e meeting tokens, atrás de uma porta trocável |
| App móvel | PWA | Instala pela web, sem segunda base de código; **nada de clínico vai para o cache** ([ADR 0012](docs/adr/0012-pwa-sem-cache-de-dado-clinico.md)) |
| Arquivos | MinIO (S3) | Bucket privado; link temporário ou stream cifrado conforme a sensibilidade ([ADR 0008](docs/adr/0008-minio-links-temporarios.md)) |
| Autorização | `Role` + `RolePermission` | Papéis criados pelo admin, CRUD por módulo ([ADR 0009](docs/adr/0009-papeis-e-permissoes.md)) |

Integrações externas ficam atrás de **portas** (`app/providers/`), com
implementações *fake* completas — dá para rodar o fluxo inteiro sem nenhuma
conta em serviço de terceiro.

## Começando

Pré-requisitos: Python 3.12, e `podman` ou `docker` com compose.

```bash
make bootstrap     # venv + deps + Postgres/pgvector/MinIO + migrations + seed
make seed-demo     # psi@ / pac@ / admin@demo.br, senha: psiconnect123
make dev           # http://localhost:8000
make worker        # 2º terminal: salas T-20min, outbox, expiração de reservas
```

Se a porta 8000 estiver ocupada: `make PORTA=8010 dev`.

Outros alvos úteis (`make ajuda` lista todos):

```bash
make test          # suíte com cobertura
make ci            # lint + tipos + testes (o que o CI roda)
make psql          # abre um psql no banco de dev
make css-watch     # recompila o Tailwind ao salvar
```

O Mailpit sobe junto e captura os e-mails em <http://localhost:8025>.

## Documentação

Comece por [`docs/README.md`](docs/README.md). Destaques:

- [`docs/00-visao-original.md`](docs/00-visao-original.md) — o brief original, preservado sem edição
- [`docs/adr/`](docs/adr/) — as decisões de arquitetura e o porquê de cada uma
- [`docs/04-lgpd-e-compliance.md`](docs/04-lgpd-e-compliance.md) — LGPD, CFP, e o que ainda precisa de parecer jurídico

## Privacidade

O produto trata **dado pessoal sensível de saúde** (LGPD art. 11). Duas decisões
estruturais decorrem disso:

- **Não guardamos áudio nem vídeo das sessões.** Só a transcrição, cifrada em
  repouso ([ADR 0003](docs/adr/0003-sem-gravacao-apenas-transcricao.md)).
- **Consentimento é registrado com o hash do texto exato** que a pessoa leu, com
  IP e user-agent.
- **Adolescentes só são atendidos com autorização do responsável legal**
  (art. 14), verificada por documento — que é apagado depois da conferência
  ([ADR 0010](docs/adr/0010-cadastro-de-menores.md)).
- **Documento de identificação** só é visto pelo dono ou por admin com permissão,
  e todo acesso fica registrado.

O PsiConnect não presta atendimento de emergência. Em crise, o **CVV** atende 24h
pelo **188**; em emergência, **192** (SAMU).

## Sugestões de melhorias
<!-- readme-tree start -->
```
.
├── .env.example
├── .github
│   └── workflows
│       ├── auto-pr-dev.yaml
│       ├── ci.yaml
│       └── readme-tree.yaml
├── .gitignore
├── .pre-commit-config.yaml
├── Makefile
├── README.md
├── alembic
│   ├── env.py
│   ├── script.py.mako
│   └── versions
│       ├── 0001_extensoes_e_parametros.py
│       ├── 0002_entidades_da_fase_1.py
│       ├── 0003_constraints_exclude_e_citext.py
│       ├── 0004_papeis_permissoes_e_cadastro_de_menores.py
│       ├── 0005_foto_como_chave_de_objeto.py
│       └── 0006_eventos_webhook.py
├── alembic.ini
├── app
│   ├── __init__.py
│   ├── api
│   │   ├── __init__.py
│   │   ├── saude.py
│   │   ├── v1
│   │   │   └── __init__.py
│   │   └── webhooks
│   │       ├── __init__.py
│   │       └── mercadopago.py
│   ├── core
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── cripto.py
│   │   ├── deps.py
│   │   ├── dinheiro.py
│   │   ├── erros.py
│   │   ├── logging.py
│   │   ├── permissoes.py
│   │   ├── seguranca.py
│   │   ├── sessao_web.py
│   │   ├── sse.py
│   │   ├── templating.py
│   │   └── tempo.py
│   ├── db
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── mixins.py
│   │   └── sessao.py
│   ├── main.py
│   ├── models
│   │   ├── __init__.py
│   │   ├── agenda.py
│   │   ├── autorizacao.py
│   │   ├── enums.py
│   │   ├── lgpd.py
│   │   ├── operacional.py
│   │   ├── pagamento.py
│   │   ├── parametro.py
│   │   ├── perfil.py
│   │   ├── responsavel.py
│   │   ├── sessao.py
│   │   ├── taxonomia.py
│   │   └── usuario.py
│   ├── providers
│   │   ├── __init__.py
│   │   ├── armazenamento
│   │   │   ├── __init__.py
│   │   │   ├── local.py
│   │   │   └── s3.py
│   │   ├── base.py
│   │   ├── embeddings
│   │   │   └── __init__.py
│   │   ├── notafiscal
│   │   │   └── __init__.py
│   │   ├── notificacao
│   │   │   ├── __init__.py
│   │   │   └── console.py
│   │   ├── pagamento
│   │   │   ├── __init__.py
│   │   │   ├── assinatura.py
│   │   │   ├── fake.py
│   │   │   └── mercadopago.py
│   │   ├── registry.py
│   │   ├── transcricao
│   │   │   └── __init__.py
│   │   └── video
│   │       ├── __init__.py
│   │       ├── daily.py
│   │       └── fake.py
│   ├── repositories
│   │   └── __init__.py
│   ├── schemas
│   │   └── __init__.py
│   ├── seeds
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── demo.py
│   │   ├── especialidades.py
│   │   ├── papeis.py
│   │   ├── parametros.py
│   │   ├── planos.py
│   │   ├── sintomas.py
│   │   └── termos.py
│   ├── services
│   │   ├── __init__.py
│   │   ├── agendamento_service.py
│   │   ├── auth_service.py
│   │   ├── avaliacao_service.py
│   │   ├── checkout_service.py
│   │   ├── credito_service.py
│   │   ├── disponibilidade_service.py
│   │   ├── financeiro_service.py
│   │   ├── matching_service.py
│   │   ├── notificacao_service.py
│   │   ├── parametros_service.py
│   │   ├── perfil_service.py
│   │   ├── regras
│   │   │   ├── __init__.py
│   │   │   ├── limites.py
│   │   │   ├── precificacao.py
│   │   │   └── slots.py
│   │   ├── responsavel_service.py
│   │   ├── sessao_service.py
│   │   ├── termos_service.py
│   │   ├── usuarios_service.py
│   │   └── webhook_service.py
│   ├── static
│   │   ├── css
│   │   │   └── app.css
│   │   ├── img
│   │   │   ├── apple-touch-icon.png
│   │   │   ├── favicon.svg
│   │   │   ├── icone-192.png
│   │   │   ├── icone-512.png
│   │   │   └── icone-maskable-512.png
│   │   ├── js
│   │   │   ├── htmx-config.js
│   │   │   ├── lobby-sse.js
│   │   │   ├── pwa.js
│   │   │   └── sw.js
│   │   ├── src
│   │   │   └── input.css
│   │   └── vendor
│   │       └── htmx.min.js
│   ├── templates
│   │   ├── admin
│   │   │   ├── base_admin.html
│   │   │   ├── papeis.html
│   │   │   ├── parametros.html
│   │   │   ├── profissionais.html
│   │   │   └── usuarios.html
│   │   ├── auth
│   │   │   ├── cadastro_paciente.html
│   │   │   ├── cadastro_profissional.html
│   │   │   └── entrar.html
│   │   ├── avaliacao
│   │   │   └── formulario.html
│   │   ├── base.html
│   │   ├── componentes
│   │   │   ├── cabecalho.html
│   │   │   ├── campo.html
│   │   │   ├── passos.html
│   │   │   └── rodape.html
│   │   ├── dev
│   │   │   └── notificacoes.html
│   │   ├── erros
│   │   │   └── erro.html
│   │   ├── paciente
│   │   │   ├── checkout.html
│   │   │   ├── horarios.html
│   │   │   ├── pagamento_pix.html
│   │   │   ├── profissionais.html
│   │   │   └── sintomas.html
│   │   ├── painel
│   │   │   └── index.html
│   │   ├── partials
│   │   │   ├── alerta.html
│   │   │   ├── estado_lobby.html
│   │   │   ├── financeiro_extrato.html
│   │   │   ├── grade_agenda.html
│   │   │   ├── simulador_resultado.html
│   │   │   └── tabela_usuarios.html
│   │   ├── profissional
│   │   │   ├── agenda.html
│   │   │   ├── especialidades.html
│   │   │   ├── financeiro.html
│   │   │   ├── perfil.html
│   │   │   └── simulador.html
│   │   ├── publico
│   │   │   └── home.html
│   │   ├── pwa
│   │   │   └── offline.html
│   │   ├── responsavel
│   │   │   ├── confirmado.html
│   │   │   ├── confirmar.html
│   │   │   ├── dependentes.html
│   │   │   └── informar.html
│   │   └── sessao
│   │       ├── disclaimer.html
│   │       ├── lobby.html
│   │       ├── sala.html
│   │       └── sala_simulada.html
│   ├── web
│   │   ├── __init__.py
│   │   └── rotas
│   │       ├── __init__.py
│   │       ├── admin.py
│   │       ├── auth.py
│   │       ├── avaliacao.py
│   │       ├── dev.py
│   │       ├── midia.py
│   │       ├── paciente.py
│   │       ├── painel.py
│   │       ├── profissional.py
│   │       ├── publico.py
│   │       ├── pwa.py
│   │       ├── responsavel.py
│   │       └── sessao.py
│   └── workers
│       ├── __init__.py
│       ├── __main__.py
│       ├── agenda.py
│       └── outbox.py
├── docker-compose.yml
├── docs
│   ├── 00-visao-original.md
│   ├── 01-visao-produto.md
│   ├── 02-modelo-dominio.md
│   ├── 03-regras-negocio.md
│   ├── 04-lgpd-e-compliance.md
│   ├── 05-roadmap.md
│   ├── 06-integracoes.md
│   ├── 07-glossario.md
│   ├── README.md
│   ├── adr
│   │   ├── 0001-stack-fastapi-htmx.md
│   │   ├── 0002-sessao-cookie-vs-jwt.md
│   │   ├── 0003-sem-gravacao-apenas-transcricao.md
│   │   ├── 0004-mercado-pago-split.md
│   │   ├── 0005-comissao-parametrizavel.md
│   │   ├── 0006-disponibilidade-vs-agendamento.md
│   │   ├── 0007-pwdlib-argon2.md
│   │   ├── 0008-minio-links-temporarios.md
│   │   ├── 0009-papeis-e-permissoes.md
│   │   ├── 0010-cadastro-de-menores.md
│   │   ├── 0011-sse-no-lobby.md
│   │   └── 0012-pwa-sem-cache-de-dado-clinico.md
│   ├── assets
│   │   ├── mindmap.txt
│   │   └── sistema-atendimento-agendamento.png
│   └── ideias.md
├── pyproject.toml
├── scripts
│   ├── baixar_tailwind.sh
│   ├── esperar_postgres.sh
│   └── gerar_icones.py
├── tests
│   ├── __init__.py
│   ├── api
│   │   ├── __init__.py
│   │   └── test_webhook_mercadopago.py
│   ├── conftest.py
│   ├── contratos
│   │   ├── __init__.py
│   │   ├── test_pagamento.py
│   │   └── test_video.py
│   ├── db
│   │   ├── __init__.py
│   │   ├── test_concorrencia_agendamento.py
│   │   └── test_constraints.py
│   ├── e2e
│   │   ├── __init__.py
│   │   ├── test_fluxo_completo.py
│   │   └── test_fluxo_menor.py
│   ├── fabricas.py
│   ├── servicos
│   │   ├── __init__.py
│   │   ├── test_agendamento.py
│   │   ├── test_avaliacao.py
│   │   ├── test_credito_service.py
│   │   ├── test_financeiro_service.py
│   │   ├── test_responsavel.py
│   │   └── test_worker.py
│   ├── unit
│   │   ├── __init__.py
│   │   ├── test_dinheiro.py
│   │   ├── test_permissoes.py
│   │   ├── test_regras.py
│   │   ├── test_sse.py
│   │   └── test_tempo.py
│   └── web
│       ├── __init__.py
│       ├── test_admin.py
│       ├── test_auth.py
│       ├── test_profissional.py
│       ├── test_pwa.py
│       ├── test_saude_e_home.py
│       ├── test_sessao_lobby.py
│       └── test_templates.py
└── tree.bak

61 directories, 242 files
```
<!-- readme-tree end -->
