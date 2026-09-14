# ADR 0006 — Disponibilidade (regra) ≠ Agendamento (fato)

- **Status:** aceita
- **Data:** 2026-08-12

## Contexto

O spike anterior (`efatafy/app/models.py`) tinha **uma** tabela `agendas` que era
simultaneamente "quando o profissional atende" e "consulta marcada com um
paciente". Foi o erro de modelagem mais grave do projeto: com esse modelo, mudar
a agenda semanal do profissional mexeria em consultas já marcadas, e não havia
como representar férias, feriado ou "essa quinta não".

## Decisão

Três conceitos separados:

| Conceito | Natureza | Persistência | Tempo |
|---|---|---|---|
| `DisponibilidadeRecorrente` | **Regra** — "toda terça, 14h–18h" | Tabela, poucas linhas | Hora **local de parede**, sem data |
| `Slot` | **Derivado** — expansão da regra − bloqueios − ocupados | **Nenhuma** — calculado sob demanda | Instante absoluto |
| `Agendamento` | **Fato** — consulta marcada | Tabela, muitas linhas | `TIMESTAMPTZ` em UTC |

Mais `BloqueioAgenda` (intervalo absoluto que subtrai da expansão) para férias e
exceções.

## Detalhes que decorrem da decisão

- **Slots não são pré-gerados no banco.** Pré-geração exigiria cron, teria race
  conditions e faria a tabela explodir. `DisponibilidadeService.slots_disponiveis()`
  expande a regra na hora, com cache curto (60 s) por profissional.
- **`DisponibilidadeRecorrente` guarda hora local, não UTC.** O Brasil aboliu o
  horário de verão em 2019, mas isso é reversível por decreto; se a regra
  estivesse em UTC, o retorno do DST deslocaria a agenda de todo mundo em 1 hora.
  Guardando "terça 14:00 local", 14h continua sendo 14h.
- **Janelas em minutos desde a meia-noite (`0..1440`), não `TIME`.** Só assim é
  possível usar `int4range` na constraint `EXCLUDE`; o tipo `TIME` não é
  suportado por operador de range.
- `Agendamento.disponibilidade_origem_id` existe só para auditoria e para a
  sugestão de recorrência, com `ON DELETE SET NULL` — nunca como dependência
  funcional. Um fato não pode deixar de existir porque a regra que o originou
  mudou.

## Garantias no banco, não só no código

```sql
-- disponibilidades não se sobrepõem entre si
EXCLUDE USING gist (profissional_id WITH =, dia_semana WITH =,
                    int4range(inicio_min, fim_min, '[)') WITH &&) WHERE (ativo)

-- nem o profissional nem o paciente ficam com dois compromissos no mesmo horário
EXCLUDE USING gist (profissional_id WITH =, tstzrange(inicio_utc, fim_utc, '[)') WITH &&)
  WHERE (status IN ('PENDENTE_PAGAMENTO','CONFIRMADO','EM_ANDAMENTO'))
```

`PENDENTE_PAGAMENTO` participa do EXCLUDE **de propósito**: como o fluxo é
sintomas → horário → pagamento, o slot fica travado enquanto o paciente paga. O
worker expira reservas vencidas (`EXPIRADO`) e libera o horário.

Isso exige a extensão `btree_gist` (criada na migration 0001), porque a
constraint combina igualdade com sobreposição de range.

## Concorrência: EXCLUDE não basta

`EXCLUDE` resolve sobreposição, mas os limites de 10h/dia e 3 sessões/semana são
**restrições agregadas**: duas transações simultâneas podem ler "9h30 agendadas"
e ambas inserirem. Por isso `AgendamentoService.reservar()` toma
`pg_advisory_xact_lock` antes de ler, sempre na mesma ordem (profissional, depois
paciente) para não deadlockar.
