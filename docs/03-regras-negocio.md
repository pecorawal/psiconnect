# Regras de negócio

Cada regra tem uma camada primária (onde é decidida) e, quando possível, um
**reforço no banco** — porque validação só na aplicação é uma sugestão, não uma
garantia.

| # | Regra | Camada primária | Reforço |
|---|---|---|---|
| R1 | Máx. **10 horas/dia** por profissional | `regras/limites.py` (pura) | advisory lock transacional |
| R2 | Máx. **3 sessões/semana** por paciente | `regras/limites.py` | advisory lock transacional |
| R3 | Sugerir o mesmo horário **até 2×/semana** | `regras/slots.py` | R2 |
| R4 | Máx. **5 especialidades** | `PerfilProfissionalService` | `UNIQUE(prof, ordem)` + `CHECK 1..5` |
| R5 | Descrição ≤ **500 caracteres** | schema Pydantic | `CHECK char_length` |
| R6 | Sem sobreposição de horário | `AgendamentoService` | **`EXCLUDE USING gist`** |
| R7 | Tolerância de **15 min** de atraso | `regras/pontualidade.py` | via `EventoSessao` |
| R8 | Link **20 min antes** | `workers/agenda.py` | `Notificacao.chave_idempotencia` |
| R9 | **Comissão** (default 5%) | `regras/precificacao.py` | congelada em `CompraPlano` |
| R10 | Pontualidade do profissional | `regras/pontualidade.py` | `EventoPontuacao` UNIQUE |
| R11 | Avaliação obrigatória | `AvaliacaoService` | — |

## R1 — 10 horas por dia, não 10 consultas

O spike anterior contava **linhas** de agendamento e comparava com 10. Com
sessão de 50 min, 10 consultas são 8h20 (o profissional podia atender mais do que
o limite); com sessão de 90 min, seriam 15h (o limite bloqueava cedo demais).

```python
def valida_limite_horas_dia(minutos_ja_agendados, duracao_nova_min, limite_horas):
    if minutos_ja_agendados + duracao_nova_min > limite_horas * 60:
        raise LimiteHorasDiaExcedido(...)
```

O service soma a duração real dos agendamentos ativos **do dia local do
profissional**. O dia local importa: às 21h em São Paulo já é o dia seguinte em
UTC, e contar pelo dia UTC atribuiria as consultas noturnas ao dia errado.
Coberto em `tests/unit/test_tempo.py::TestDiaLocal`.

## R2 — semana ISO

A semana começa na segunda e é calculada por `isocalendar()`, não por
`dt - timedelta(days=dt.weekday())`. A diferença aparece na virada do ano: 1º de
janeiro de 2027 pertence à semana 53 de **2026**. Com o cálculo ingênuo, o
paciente conseguiria furar o limite de 3 sessões naquela semana.

## R4 — o limite de 5 vive no banco

```sql
ordem SMALLINT NOT NULL CHECK (ordem BETWEEN 1 AND 5),
UNIQUE (profissional_id, ordem)
```

Só existem 5 valores possíveis de `ordem` e cada um é único por profissional —
logo, no máximo 5 linhas. A sexta especialidade é impossível mesmo por acesso
direto ao banco ou por um bug futuro na API.

## R6 — sobreposição é garantida pelo Postgres

```sql
EXCLUDE USING gist (profissional_id WITH =, tstzrange(inicio_utc, fim_utc, '[)') WITH &&)
  WHERE (status IN ('PENDENTE_PAGAMENTO','CONFIRMADO','EM_ANDAMENTO'))
```

`PENDENTE_PAGAMENTO` entra na constraint **de propósito**: como o paciente
escolhe o horário antes de pagar, o slot precisa ficar travado durante o
checkout. O worker expira as reservas vencidas e devolve o horário ao mercado.

## Concorrência: por que a constraint não basta

`EXCLUDE` cobre sobreposição, mas **R1 e R2 são restrições agregadas**: duas
transações simultâneas podem ambas ler "9h30 já agendadas" e ambas inserir 50
min, resultando em 10h20. Por isso `AgendamentoService.reservar()` toma advisory
locks antes de ler:

```sql
SELECT pg_advisory_xact_lock(hashtextextended('prof_dia:'||:prof_id||':'||:dia_local, 0));
SELECT pg_advisory_xact_lock(hashtextextended('pac_sem:' ||:pac_id ||':'||:ano_semana, 0));
```

Sempre nessa ordem (profissional, depois paciente) para não deadlockar. Os locks
são liberados no fim da transação e custam praticamente nada.
Teste: `tests/db/test_concorrencia_agendamento.py` — duas transações competindo
pelo mesmo slot, exatamente uma vence.

## R9 — repartição do valor

```python
@dataclass(frozen=True)
class ReparticaoValores:
    bruto_centavos: int
    taxa_provedor_centavos: int         # Pix ~0,99% | crédito ~4,98%
    comissao_plataforma_centavos: int
    imposto_retido_centavos: int
    liquido_profissional_centavos: int
```

Invariante testada: `taxa + comissão + imposto + líquido == bruto`. Todo valor é
`int` em centavos e todo arredondamento é `Decimal` com `ROUND_HALF_UP` — nenhum
centavo criado ou perdido.

## R11 — o que "obrigatória" significa

Após uma sessão `REALIZADA` sem `Avaliacao`, o paciente é redirecionado para
`/avaliacao/{id}` e **fica bloqueado de agendar uma nova sessão**. Nunca é
bloqueado de sair, de pedir suporte ou de acessar os próprios dados — bloquear
isso violaria o art. 18 da LGPD.
