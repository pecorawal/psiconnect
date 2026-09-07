# Integrações

Toda integração externa fica atrás de uma **porta** (`Protocol` em
`app/providers/base.py`), com pelo menos duas implementações: uma *fake*
determinística e uma real. A escolha vem de `Settings`, e o `registry.py` monta
os providers no `lifespan`.

Isso não é abstração por esporte: é o que permite a Fase 1 fechar o fluxo inteiro
sem nenhuma conta em serviço de terceiro, e o que mantém a suíte de testes rápida
e offline.

## Os fakes não são no-ops

| Fake | Comportamento |
|---|---|
| `FakePaymentProvider` | aprova na hora; valores terminados em `.13` recusam (caminho de erro testável); dispara o **mesmo webhook interno** do real |
| `FakeVideoProvider` | serve `/sala-simulada/{id}` com retângulo, cronômetro e botões; **o fluxo de lobby e admissão é real**, só o WebRTC é falso |
| `ConsoleNotificationProvider` | grava a `Notificacao`, visível em `/dev/notificacoes` |
| `FakeTranscriptionProvider` | texto determinístico |
| `FakeEmbeddingProvider` | vetor derivado de hash — estável, permite testar similaridade |

**Contract tests** (`tests/contratos/`) rodam a **mesma** suíte de asserções
contra o fake e contra o adaptador real (com `respx` interceptando HTTP a partir
de fixtures gravadas). É o que garante que o fake não está mentindo.

## Vídeo — daily.co

- Salas privadas; *meeting tokens* emitidos sob demanda com TTL curto, nunca
  persistidos. O profissional entra como *owner* e controla a admissão.
- Sala criada em T-20min pelo worker; `sala_expira_em = fim + 30min`.

**Implementado** em `app/providers/video/daily.py` (Fase 4), à espera de conta:
basta `VIDEO_PROVIDER=daily`, `DAILY_API_KEY` e `DAILY_DOMAIN`. Sem as duas
últimas a aplicação **não sobe** — falhar no boot é melhor do que falhar com o
paciente já na tela. A suíte `tests/contratos/test_video.py` roda contra o fake e
contra o real (com `respx`) sem tocar a rede.

Detalhes que o adaptador fixa, e por quê:

| Propriedade | Valor | Motivo |
|---|---|---|
| `privacy` | `private` | sala aberta é sala que qualquer um acha |
| `exp` + `eject_at_room_exp` | fim + 30 min | sem o segundo, a sala fica de pé depois de expirar |
| `enable_knocking` | ligado | segunda barreira; a primeira é nossa, no lobby |
| `enable_chat` | desligado | o que se escreve nele escapa do consentimento de transcrição |
| gravação | nunca pedida | [ADR 0003](adr/0003-sem-gravacao-apenas-transcricao.md) — a API só grava se pedirmos |
| `is_owner` | só o profissional | é ele que controla a chamada |

A entrada usa a **UI *prebuilt*** por iframe (`?t=<token>`), que já traz grade de
vídeo, reconexão e seleção de dispositivo. Vendorizar o SDK em
`app/static/vendor/` só se passarmos a precisar de uma sala com layout próprio.

### Economia — atenção

Free tier: **10.000 participant-minutes/mês**. Uma sessão de 50 min com 2
participantes consome **100** → **~100 sessões/mês** no plano gratuito. Depois,
US$ 0,004/participante-minuto = **US$ 0,40/sessão** (~R$ 2,20).

Com a comissão de 12% sobre uma sessão de R$ 150 (R$ 18,00), **o vídeo consome
~12% da receita** — foi justamente esse custo, somado à transcrição, que
motivou a revisão da comissão de 5% para 12% ([ADR 0005](adr/0005-comissao-parametrizavel.md)). Instrumentar minutos consumidos desde a Fase 4; se o volume
crescer, **LiveKit self-hosted** transforma custo variável em infra fixa — e a
porta `VideoProvider` existe exatamente para essa troca.

## Pagamento — Mercado Pago

Ver [ADR 0004](adr/0004-mercado-pago-split.md). Split de marketplace: o dinheiro
não transita pela plataforma, evitando caracterizar arranjo de pagamento sujeito
ao BACEN.

Webhooks são idempotentes via `EventoWebhook.evento_id_externo` (unique) e a
assinatura é validada por HMAC. Rota fora do CSRF.

## Notificação — e-mail e WhatsApp

Padrão **outbox**: o service grava uma `Notificacao` com `agendada_para` e
`chave_idempotencia`; o worker envia com retry exponencial (`tenacity`). Nunca se
envia dentro da transação do caso de uso — se o envio falhasse, o agendamento
faria rollback junto.

- **E-mail:** Mailpit em dev (UI em <http://localhost:8025>), SMTP real depois.
- **WhatsApp:** Cloud API da Meta. Mensagem iniciada pela empresa exige
  **template HSM aprovado** — o texto precisa ser submetido e aprovado antes,
  o que tem prazo e deve entrar no planejamento da Fase 3.

## Transcrição e embeddings

Duas opções, com uma diferença que não é só de custo:

| | Custo | LGPD |
|---|---|---|
| Whisper API (OpenAI) | ~US$ 0,006/min → **US$ 0,30/sessão** | áudio de psicoterapia sai do Brasil (art. 33) |
| `faster-whisper` local | infra (GPU) | **áudio não sai** |
| Transcrição do próprio Daily | US$ 0,0059/min | pior caso: áudio no provedor + subprocessador |

**Recomendação: local por padrão**, API só com opt-in explícito. Resolve custo e
transferência internacional de uma vez.

> `EMBEDDING_DIM=1536` está fixado na migration. Modelos locais multilíngues bons
> (e5-large, BGE-M3) são 1024 e **não cabem**. Decidir antes da Fase 5.

## Nota fiscal — são dois documentos

1. O **profissional** emite NFS-e da consulta para o paciente.
2. A **plataforma** emite NFS-e da comissão (intermediação) para o profissional.

**Só (2) é obrigação da plataforma.** Oferecer (1) como serviço é decisão de
produto e exige dados fiscais, regime tributário, município e inscrição de cada
profissional — bastante fricção no onboarding.

NFS-e é municipal e o padrão nacional está em transição: usar um intermediador
(Focus NFe, eNotas, NFE.io) em vez de integrar prefeitura por prefeitura.
