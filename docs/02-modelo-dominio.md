# Modelo de domínio

Convenções em todos os modelos:

- SQLAlchemy 2.0 tipado (`Mapped[...]` / `mapped_column`), uma única `Base`;
- PK `UUID` gerada pelo banco — os IDs aparecem em URLs, e um serial permitiria
  enumerar consultas alheias e inferir o volume da plataforma;
- timestamps `TIMESTAMPTZ`, **sempre UTC** no banco;
- **dinheiro é `int` em centavos**, nunca `float`;
- enums nativos do Postgres.

## O ponto central: três conceitos de tempo

Detalhado em [ADR 0006](adr/0006-disponibilidade-vs-agendamento.md).

| Conceito | Natureza | Persistência |
|---|---|---|
| `DisponibilidadeRecorrente` | regra ("toda terça, 14h–18h") | tabela, hora **local** |
| `Slot` | derivado (regra − bloqueios − ocupados) | **nenhuma** |
| `Agendamento` | fato (consulta marcada) | tabela, UTC |

## Agregados

### Identidade e acesso

- **`Usuario`** — `email` (citext único), `senha_hash`, `papel`
  (`PACIENTE`/`PROFISSIONAL`/`ADMIN`), `nome_completo`, `telefone_e164`,
  `timezone`, `ativo`, verificações, `ultimo_login_em`.
- **`SessaoLogin`** — sessão de autenticação. Nome distinto de `Sessao`
  (atendimento) de propósito: são coisas diferentes e confundi-las gera bug.
  Guarda `token_hash`, IP, user-agent, `expira_em`, `revogada_em`.
- **`TokenVerificacao`** — verificação de e-mail e reset de senha.

### Perfis

- **`PerfilProfissional`** — `foto_url`, `conselho` (CRP/CREFITO),
  `registro_numero`/`registro_uf`, `registro_verificado_em`, `descricao`
  (`CHECK ≤ 500`), `duracao_sessao_min`, `limite_horas_dia`, `status_cadastro`,
  `mp_user_id` / `mp_refresh_token_cifrado`, `pontos_total`.
- **`PerfilPaciente`** — `data_nascimento`, `cpf_cifrado`, responsável legal
  (menores — ver [compliance](04-lgpd-e-compliance.md)), contato de emergência,
  `pontos_total`.

### Taxonomias e matching

- **`Especialidade`** — ~30 no seed (ansiedade, depressão, luto, TCC,
  psicanálise, casal, familiar, infantil, TDAH, TEA, TOC, pânico, burnout,
  TEPT…).
- **`Sintoma`** — com `descricao_leiga` (linguagem de paciente, não jargão) e
  **`bandeira_risco`** para o protocolo de crise.
- **`SintomaEspecialidade`** — `peso 1..5`. É a ponte que faz o matching.
- **`PacienteSintoma`** — `intensidade 1..5`. **Dado sensível (LGPD art. 11)**:
  acesso restrito e auditado.
- **`ProfissionalEspecialidade`** — `ordem CHECK 1..5` + `UNIQUE(prof, ordem)`
  (é isto que garante o máximo de 5), `preco_min/padrao/max_centavos`.

### Agenda

- **`DisponibilidadeRecorrente`** — `dia_semana 0..6`, `inicio_min`/`fim_min`
  (minutos desde a meia-noite local — permitem `int4range` no `EXCLUDE`, o que
  `TIME` não permite), vigência, `EXCLUDE` contra sobreposição.
- **`BloqueioAgenda`** — férias, feriado, exceções. Subtrai da expansão.
- **`Agendamento`** — o fato. `status`, `reserva_expira_em`,
  `serie_recorrencia_id` (agrupa a sugestão de 2×/semana), `valor_centavos`,
  duas constraints `EXCLUDE` (uma por profissional, outra por paciente).

### Planos, créditos e pagamento

- **`Plano`** — `AVULSO`, `PACOTE_5`, `PACOTE_10`.
- **`CompraPlano`** — `percentual_comissao_aplicado` **congelado na compra**.
- **`CreditoSessao`** — uma **linha por crédito**, não um contador: consumo vira
  um `UPDATE ... WHERE status='DISPONIVEL'` atômico e auditável.
- **`Pagamento`** — bruto, taxa do provedor, comissão, líquido; dados do Pix;
  `chave_idempotencia`.
- **`EventoWebhook`** — idempotência de webhook (o Mercado Pago reenvia).
- **`RegraComissao`**, **`Repasse`**, **`NotaFiscal`**.

### Sessão

- **`Sessao`** — `sala_url`, `status`, marcos de entrada de cada parte,
  `chave_acesso_*_hash`. As "chaves de acesso aleatórias" do requisito são
  geradas com `secrets.token_urlsafe(32)`, exibidas uma vez e guardadas apenas
  como hash. Tokens do daily.co **não são persistidos** — emitidos sob demanda
  com TTL curto.
- **`EventoSessao`** — append-only, é a **fonte de verdade** para pontualidade,
  tolerância de 15 min e no-show. Depender de campo mutável para isso seria
  frágil e não auditável.

### Avaliação e gamificação

- **`Avaliacao`** — exatamente as 3 notas do requisito (`nota_plataforma`,
  `nota_profissional`, `nota_proprio_cuidado`), `CHECK 1..5`, + comentários.
- **`EventoPontuacao`** — `UNIQUE (usuario_id, tipo, referencia_id)`, saldo por
  `SUM(pontos)`. Idempotente por construção.

### LGPD

- **`TermoVersionado`** (com `hash_sha256`), **`AceiteTermo`** (IP, UA, hash do
  texto lido, `revogado_em`), **`LogAuditoria`** (append-only),
  **`SolicitacaoTitular`** (art. 18).

### Transcrição

- **`Transcricao`** e **`TrechoTranscricao`** — `texto_cifrado BYTEA`
  (AES-256-GCM, AAD = `transcricao_id|ordem`), `embedding Vector(1536)` com
  índice HNSW.

### Operacional

- **`ParametroSistema`** — regras mutáveis em runtime (comissão, limites).
- **`Notificacao`** — **outbox** com `agendada_para`: é assim que "o link chega
  20 minutos antes" vira uma linha testável e reentrante, em vez de um
  `time.sleep` ou de uma promessa no texto da resposta (o spike anterior
  retornava "Notificações enviadas para WhatsApp e E-mail" sem enviar nada).
