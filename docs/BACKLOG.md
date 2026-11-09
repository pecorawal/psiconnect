# Backlog

Levantado em 2026-11-09 a partir do histórico de commits, do
[roadmap](05-roadmap.md), das [pendências de compliance](04-lgpd-e-compliance.md)
e do [brief original](ideias.md). O roadmap diz **o que cada fase entrega**; este
arquivo diz **qual é o próximo item a pegar**. Ao concluir um item, risque-o
aqui e atualize a fase correspondente no roadmap no mesmo commit.

Legenda de prioridade: **P0** bloqueia produção ou é risco · **P1** próximo a
fazer · **P2** depois · **⛔** bloqueado por algo externo.

## Estado em 2026-11-09

- `dev` e `main` sincronizados com o `origin`. O PR #1 (dev → main) foi
  mergeado; o commit `852fe8c` (segredos fora do repositório, alerta do
  GitGuardian) foi para o `dev` e abre o próximo PR automático.
- Suíte completa verde (340 testes) com Postgres local; `ruff` limpo.
- Fases 0 e 1 concluídas; Fase 2 parcial (resto ⛔ no Mercado Pago); Fase 4 em
  andamento.

## Fluxo de entrega (pendente de ação manual)

| # | Item | Prio | Observação |
|---|---|---|---|
| E1 | Revisar e mergear o PR automático dev → main com o `852fe8c` | P0 | Confirmar que o GitGuardian ficou verde |
| E2 | Rotacionar qualquer valor que tenha ido para o histórico do git antes do `852fe8c` | P1 | Eram só valores de dev/CI, mas continuam no histórico. O `make env` nunca sobrescreve: para gerar novos, apague a linha da chave no `.env` e rode de novo (a senha do Postgres exige recriar o volume) |
| E3 | Autenticar o `gh` neste PC (`gh auth login`) | P2 | Sem isso não dá para listar PRs/issues pela CLI |
| E4 | Atualizar o bloco `readme-tree` (falta `scripts/gerar_env.py`) | P2 | O workflow regenera sozinho no próximo push em `main` |

## Segurança e compliance

| # | Item | Prio | Origem |
|---|---|---|---|
| S1 | Alertar o profissional quando o paciente marca sintoma com `bandeira_risco` | P0 | Pendência 5 de compliance — o dado já é modelado e o `MatchingService` já filtra |
| S2 | Consentimento do **profissional** para a transcrição (disclaimer hoje cobre só o paciente) | P0 antes da Fase 5 | Pendência 3 |
| S3 | E-mail do encarregado (DPO), canal do titular, RIPD, plano de incidente | P0 antes do 1º usuário | Pendência 6 |
| S4 | Direitos do titular (art. 18) sobre `SolicitacaoTitular` | P1 (Fase 7) | Pendência 7 |
| S5 | CSP restrita e rate limiting geral | P1 (Fase 7) | Roadmap |
| S6 | ⛔ Parecer jurídico: base legal, transferência internacional (daily.co/OpenAI) | — | Pendências 1 e 2 |

## Fase 4 — vídeo, sala e PWA (em andamento)

| # | Item | Prio | Observação |
|---|---|---|---|
| V1 | ⛔ Criar conta no daily.co, preencher `DAILY_API_KEY`/`DAILY_DOMAIN` e homologar em sandbox | P1 | Adaptador e testes de contrato prontos |
| V2 | Instrumentar minutos consumidos por sessão | P1 | Nota de custo em [06-integracoes.md](06-integracoes.md) |
| V3 | Decidir: UI *prebuilt* do daily.co ou SDK vendorizado com grade própria | P2 | Depende de V1 |

## Fase 3 — notificações reais (sem dependência de pagamento)

| # | Item | Prio | Observação |
|---|---|---|---|
| N1 | `SmtpEmailProvider` (Mailpit em dev) | P1 | Pode começar já |
| N2 | Lembretes T-24h e T-1h no worker | P1 | Outbox com retry já existe |
| N3 | ⛔ WhatsApp Cloud API — templates HSM aprovados pela Meta | P2 | Tem prazo de aprovação: pedir cedo |
| N4 | Telas de pontos (avaliação/pontualidade) | P2 | |
| N5 | Decisão de negócio nº 3: no-show/cancelamento consome crédito? | P1 | Bloqueia regra do worker e o estorno (B2) |

## Fase 5 — transcrição e busca vetorial

| # | Item | Prio | Observação |
|---|---|---|---|
| T1 | **Decidir antes de começar:** embedding local (1024) ou OpenAI (1536) | P1 | A dimensão está na migration; trocar depois = re-embedding |
| T2 | Decisão de negócio nº 5: a plataforma vira prontuário eletrônico? | P1 | Antes da Fase 5 |
| T3 | Transcrição local (`faster-whisper`) como padrão; API de terceiro só por opt-in | P1 | Recomendação técnica da pendência 2 |
| T4 | Pipeline áudio → trechos → AES-GCM → embeddings → pgvector (HNSW) | P2 | O áudio nunca toca o disco |
| T5 | "Baixar" e "excluir minha transcrição" | P2 | Art. 18 |

## Fase 2 — pagamentos (⛔ parado, ver roadmap)

B1–B6 do [roadmap](05-roadmap.md#backlog--mercado-pago--mercado-livre-parado):
OAuth do profissional, estorno ponta a ponta, `RegraComissao` com override,
homologação em sandbox, conciliação de repasses, Checkout Pro. Mais a decisão de
negócio nº 2 (créditos quando o profissional sai).

## Fases 6 e 7

- Nota fiscal (`NotaFiscalProvider`, Focus NFe/eNotas) — ⛔ depende do split real
  e da decisão de negócio nº 4.
- Admin: relatórios, gestão de taxonomias pela interface, consulta à trilha de
  auditoria.
- Produção: backups cifrados com teste de restore, observabilidade, pgBouncer,
  deploy, runbook, teste de carga.

## Divergências do brief original a decidir

O [brief](ideias.md) pede coisas que o produto implementou de outro jeito. Não
são bugs, mas precisam de decisão explícita do dono do produto:

| # | Brief | Implementado | Decisão pendente |
|---|---|---|---|
| D1 | Pacotes de 10, 20 e 30 sessões, com **+2 sessões grátis** | Avulso, pacote de 5 e de 10 (`app/seeds/planos.py`) | Manter ou trocar os planos e criar o bônus |
| D2 | Paciente entra só com nome e celular e descreve o problema num **chat** | Cadastro com e-mail/senha e seleção de sintomas | Login por celular (OTP) e triagem por texto livre |
| D3 | Gravação da sessão | Proibida — só transcrição ([ADR 0003](adr/0003-sem-gravacao-apenas-transcricao.md)) | Manter (recomendado, LGPD) |
| D4 | App mobile | PWA ([ADR 0012](adr/0012-pwa-sem-cache-de-dado-clinico.md)) | Avaliar se lojas de app são exigência |
| D5 | Análise de sentimento por IA | Não iniciado | Fica depois da Fase 5 |
| D6 | Trocar de profissional no meio do tratamento | Créditos atrelados ao profissional | Liga com a decisão de negócio nº 2 |

## Fora deste repositório

A segunda metade de [`ideias.md`](ideias.md) descreve outro sistema (gestão da
Pectec: estoque descentralizado, fornecedores, cotação de controle de acesso,
custos de infraestrutura 24x7, campanhas de marketing). Não é escopo do
PsiConnect; se for adiante, merece repositório próprio.
