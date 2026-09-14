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
<!-- readme-tree end -->
