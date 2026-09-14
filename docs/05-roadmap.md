# Roadmap

Estratégia: **walking skeleton primeiro**. A Fase 1 entrega a fatia end-to-end
mais fina que funciona de verdade; pagamento, notificação e IA entram como
implementações *fake* atrás de portas e são aprofundados depois. O objetivo é ter
algo demonstrável cedo, não módulos perfeitos e desconectados.

---

## Fase 0 — Fundação ✅ concluída

Scaffolding, `pyproject`, `.gitignore`, compose (Postgres+pgvector, Mailpit),
Alembic com a migration `0001` (extensões + `ParametroSistema`), `Settings`,
logging estruturado com redação de PII, `create_app()`, `/healthz` e `/readyz`,
layout base Jinja+Tailwind+HTMX, `Makefile`, CI, ADRs 0001–0007, migração das
specs do `efatafy` para `docs/`.

**Pronto quando:** `make bootstrap && make dev` sobe a home estilizada,
`/readyz` confirma banco + extensões + migration, e `make ci` passa.

---

## Fase 1 — Walking skeleton ✅ concluída

A fatia fina, ponta a ponta: **cadastro → agenda → paciente agenda → ambos na
sala de vídeo**.

1. `Usuario` + auth por cookie + papéis + CSRF; cadastro/login de paciente e
   profissional; aceite de termos versionados.
2. Onboarding do profissional: foto (upload + resize Pillow), CRP/CREFITO,
   descrição de 500 caracteres, **até 5 especialidades com faixa de preço**.
3. `DisponibilidadeRecorrente`: CRUD semanal em HTMX (grade dias × horas), com a
   constraint `EXCLUDE`.
4. Paciente: seleção de sintomas → `MatchingService` → lista rankeada.
5. `DisponibilidadeService.slots_disponiveis()` (expansão − bloqueios − ocupados).
6. `AgendamentoService.reservar()` com **R1, R2, R6** completos, advisory locks e
   TTL de reserva.
7. Checkout mínimo: plano `AVULSO` + `FakePaymentProvider` → `CompraPlano` +
   `CreditoSessao` + `Pagamento`, com a comissão exibida.
8. `ConsoleNotificationProvider` + `/dev/notificacoes`; card com **nome e foto**
   do profissional no painel do paciente.
9. Sessão: sala criada em T-20min, disclaimer com checkbox → `AceiteTermo`,
   **lobby** com polling HTMX, profissional **admite**, ambos entram.
10. Encerrar → `REALIZADA`.

11. Worker: sala em T-20min, expiração de reservas, no-show, outbox com retry.
12. Avaliação obrigatória de 3 perguntas (R11) com pontuação.

**Verificado:** o teste `tests/e2e/test_fluxo_completo.py` percorre os 21 passos
por HTTP, com dois clientes distintos, e confere a trilha de eventos ao final.

**Fora de escopo (segue nas fases seguintes):** pacotes 5/10 no fluxo real,
Mercado Pago, WhatsApp/e-mail reais, transcrição, PWA, nota fiscal, painel admin.

**Decisão que ficou:** profissional recém-cadastrado fica `EM_ANALISE` e não
aparece para pacientes até a verificação do registro no conselho.

### Entrou depois, fora do escopo original da fase

Três blocos pedidos durante a construção, que anteciparam parte das fases 6 e 7:

- **Papéis e permissões** ([ADR 0009](adr/0009-papeis-e-permissoes.md)) com CRUD
  administrativo — antecipa boa parte do painel admin da Fase 6.
- **MinIO** ([ADR 0008](adr/0008-minio-links-temporarios.md)) para foto e
  documento, com acesso autorizado e trilha.
- **Cadastro de adolescentes** ([ADR 0010](adr/0010-cadastro-de-menores.md)),
  que resolve a pendência nº 6 de compliance.

A comissão padrão passou de 5% para **12%**
([ADR 0005](adr/0005-comissao-parametrizavel.md)).

---

## Fase 2 — Pagamentos reais e planos ⏸ parcial, resto no backlog

**Entregue** (commits `09339e5`, `408d731`, `56dfb07`):

- `MercadoPagoPaymentProvider` — Pix com QR e copia-e-cola, crédito e débito,
  `application_fee` = comissão, idempotência na criação da cobrança.
- Webhook `/webhooks/mercadopago` idempotente, com validação de assinatura e a
  tabela `EventoWebhook`.
- Pacotes de 5 e 10 sessões utilizáveis ponta a ponta: `CreditoService` (saldo,
  consumo, devolução, expiração pelo worker), checkout e painel do paciente.
- Painel financeiro do profissional: resumo, extrato por mês e próximas sessões
  pagas. O `/profissional/simulador` já tinha saído na Fase 1.

**Parado:** o que falta depende de conta e credenciais reais do Mercado
Pago/Mercado Livre e foi movido para o [backlog](#backlog--mercado-pago--mercado-livre-parado).

---

## Fase 3 — Notificações reais (~1 semana)

O worker, a outbox com retry, a expiração de reserva, o no-show e a avaliação
obrigatória já saíram na Fase 1. Falta trocar os provedores fake pelos reais:
`SmtpEmailProvider` e WhatsApp Cloud API — este exige **templates HSM aprovados
pela Meta**, o que tem prazo e precisa entrar no planejamento. Mais os lembretes
T-24h e T-1h, e as telas de pontos.

---

## Fase 4 — daily.co real, sala e PWA ⏳ em andamento

**Entregue:**

- **SSE no lobby** ([ADR 0011](adr/0011-sse-no-lobby.md)): `GET /sessao/{id}/eventos`
  no lugar do polling de 2 s, que fica como plano B para navegador sem
  `EventSource` e para stream que cai. A conexão de banco volta ao pool entre as
  leituras, e evento só sai quando o HTML muda.
- **PWA** ([ADR 0012](adr/0012-pwa-sem-cache-de-dado-clinico.md)): manifesto,
  service worker em `/sw.js`, ícones (incl. *maskable*), página offline e botão
  de instalar. O cache guarda **só o app shell** — a regra é verificada por teste
  que lê o próprio `sw.js`.
- **`DailyVideoProvider`**: salas privadas, `exp` + `eject_at_room_exp`, meeting
  tokens com `is_owner` só para o profissional, chat desligado e gravação nunca
  pedida ([ADR 0003](adr/0003-sem-gravacao-apenas-transcricao.md)). Coberto pela
  suíte de contrato `tests/contratos/test_video.py`, que roda contra o fake e
  contra o real com `respx` — sem tocar a rede.
- **Admissão virou regra de servidor**: paciente sem `paciente_admitido_em` não
  recebe meeting token. Antes, digitar `/sessao/{id}/sala` entrava na chamada
  sem passar pelo lobby — inofensivo com o provider fake, grave com sala real.
- Correção: o iframe da sala simulada apontava para uma rota inexistente.

**Falta:** ligar uma conta no daily.co (`DAILY_API_KEY` + `DAILY_DOMAIN`) e
homologar em sandbox; instrumentar minutos consumidos (ver a nota de custo em
[06-integracoes.md](06-integracoes.md)); avaliar se a UI *prebuilt* basta ou se
vale vendorizar o SDK para uma grade de vídeo própria.

---

## Fase 5 — Transcrição e busca vetorial (~2 semanas)

Consentimento específico por sessão, áudio → `TranscriptionProvider` → trechos →
AES-GCM → embeddings → pgvector com índice HNSW; **o áudio nunca toca o disco**;
busca semântica restrita ao dono; retenção e expurgo; "baixar" e "excluir minha
transcrição" (art. 18).

> Decidir **antes** desta fase: embedding local (1024 dimensões) ou OpenAI
> (1536). A dimensão está fixada na migration; mudar depois custa re-embedding
> de tudo. Ver [ADR 0003](adr/0003-sem-gravacao-apenas-transcricao.md).

---

## Fase 6 — Nota fiscal, repasses e admin (~2 semanas)

`NotaFiscalProvider` (Focus NFe/eNotas), emissão na aprovação do pagamento e
conciliação de repasses.

O painel admin já saiu junto com a Fase 1: contas, papéis, verificação de
registro e edição de `ParametroSistema`. Restam relatórios, gestão de taxonomias
pela interface e a consulta à trilha de auditoria.

---

## Fase 7 — Hardening e produção (~2 semanas)

Fluxo de direitos do titular, retenção e anonimização, RIPD, canal do DPO, CSP
restrita, rate limiting geral, backups cifrados **com teste de restore**,
observabilidade, pgBouncer, deploy, runbook, teste de carga.

---

## Backlog — Mercado Pago / Mercado Livre (parado)

Tudo que exige conta de marketplace, credencial de produção ou homologação no
Mercado Pago/Mercado Livre fica aqui até haver decisão comercial. O código já
escrito permanece no repositório, coberto pelos testes de contrato — nada
precisa ser revertido, e com o provedor `fake` o fluxo inteiro roda sem
nenhuma conta de terceiro.

| # | Item | Por que está parado |
|---|---|---|
| B1 | OAuth do profissional (autorizar a plataforma na conta dele) | Exige aplicação aprovada no painel do MP |
| B2 | Estorno ponta a ponta: cancelamento → `estornar()` → devolução de crédito | Depende de B1 e da decisão de negócio nº 3 |
| B3 | `RegraComissao` com override por profissional ou plano | Só faz sentido com split real |
| B4 | Homologação em sandbox e teste da assinatura de webhook contra o MP | Precisa de credenciais |
| B5 | Conciliação de repasses e relatório de saldo liberado | Depende de B1 |
| B6 | Checkout Pro / Mercado Livre como canal alternativo de venda | Nunca avaliado |

Com a Fase 2 parada, as fases 3, 4 e 5 seguem sem nenhuma dependência de
pagamento; a Fase 6 (nota fiscal e repasses) fica bloqueada junto.

---

## Decisões de negócio ainda em aberto

Nenhuma bloqueia a Fase 1, mas todas precisam de resposta até a fase indicada:

| # | Questão | Até a fase |
|---|---|---|
| ~~1~~ | ~~Quem paga a taxa do gateway?~~ **Resolvido:** sai do profissional; comissão revista para 12% (ADR 0005) | — |
| 2 | Créditos são atrelados ao profissional — se ele sair, o que acontece com os restantes? | 2 |
| 3 | Política de no-show e cancelamento: consome crédito? quantas horas antes é gratuito? | 3 |
| 4 | A plataforma emite NFS-e da consulta (do profissional) ou só da comissão? | 6 |
| 5 | A plataforma vira prontuário eletrônico? | antes da 5 |
