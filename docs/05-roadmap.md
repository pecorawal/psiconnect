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

## Fase 1 — Walking skeleton (~2 semanas)

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

**Pronto quando:** em dois navegadores, do zero até os dois na sala, em ~6 min.
**Fora de escopo:** pacotes 5/10, Mercado Pago, WhatsApp/e-mail reais,
transcrição, PWA, nota fiscal, admin.

---

## Fase 2 — Pagamentos reais e planos (~2 semanas)

`MercadoPagoPaymentProvider` (Pix com QR e copia-e-cola, crédito, débito), OAuth
do profissional, `application_fee` = comissão, webhook idempotente com validação
de assinatura, planos de 5 e 10 sessões com expiração de créditos, estorno,
`RegraComissao` com override, `/profissional/simulador`, painel financeiro.

---

## Fase 3 — Notificações, agenda operacional e avaliação (~1,5 semana)

Worker de outbox com retry exponencial, SMTP real, WhatsApp Cloud API com
templates aprovados, link em T-20min de verdade, lembretes T-24h e T-1h,
expiração de reserva, no-show automático, tolerância de 15 min, **avaliação
obrigatória de 3 perguntas**, `EventoPontuacao` e telas de pontos.

---

## Fase 4 — daily.co real, sala e PWA (~1,5 semana)

Salas privadas com meeting tokens (o profissional é *owner* e controla a
admissão), SDK vendorizado, grade de vídeo responsiva, reconexão, SSE no lugar do
polling do lobby, `manifest.webmanifest` + service worker (app shell; **jamais**
cachear rota com dado clínico), instalação como app.

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

`NotaFiscalProvider` (Focus NFe/eNotas), emissão na aprovação do pagamento,
conciliação de repasses, painel admin: aprovação de cadastro, verificação de CRP,
edição de `ParametroSistema` (inclusive a comissão), relatórios, taxonomias.

---

## Fase 7 — Hardening e produção (~2 semanas)

Fluxo de direitos do titular, retenção e anonimização, RIPD, canal do DPO, CSP
restrita, rate limiting geral, backups cifrados **com teste de restore**,
observabilidade, pgBouncer, deploy, runbook, teste de carga.

---

## Decisões de negócio ainda em aberto

Nenhuma bloqueia a Fase 1, mas todas precisam de resposta até a fase indicada:

| # | Questão | Até a fase |
|---|---|---|
| 1 | Quem paga a taxa do gateway? No crédito (~4,98%) ela supera a comissão de 5% | 2 |
| 2 | Créditos são atrelados ao profissional — se ele sair, o que acontece com os restantes? | 2 |
| 3 | Política de no-show e cancelamento: consome crédito? quantas horas antes é gratuito? | 3 |
| 4 | A plataforma emite NFS-e da consulta (do profissional) ou só da comissão? | 6 |
| 5 | A plataforma vira prontuário eletrônico? | antes da 5 |
