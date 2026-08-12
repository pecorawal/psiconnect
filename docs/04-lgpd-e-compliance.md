# LGPD e compliance

> **Este documento não é parecer jurídico.** Ele registra o que foi apurado e as
> decisões técnicas tomadas em consequência. Os itens marcados **[JURÍDICO]**
> precisam de validação por advogado antes do go-live.

## O que é obrigatório no Brasil

A pergunta registrada no brief original era se o Brasil é regido por auditorias
HIPAA + BAA. **Não é.** A HIPAA é lei federal dos Estados Unidos (1996) e não
tem efeito legal aqui.

- **LGPD** (Lei 13.709/2018, em vigor desde 2020) é a lei brasileira aplicável.
  É baseada no GDPR europeu, não na HIPAA.
- **HIPAA + BAA** funcionam, no Brasil, como padrão de excelência de adoção
  **voluntária** por healthtechs que buscam alinhamento internacional.
- A Anvisa aceita resultados de auditorias externas para apoiar decisões
  regulatórias, o que incentiva padrões elevados.

## Dado sensível de saúde (art. 11)

Sintomas, transcrições e o próprio fato de haver uma consulta marcada são dado
pessoal **sensível**. O art. 11 admite dois caminhos:

- inciso I — **consentimento específico e destacado** do titular;
- inciso II, "a" — tutela da saúde, **por profissionais de saúde**.

**[JURÍDICO]** A plataforma não é profissional de saúde; o profissional é. O
caminho provável é consentimento específico e destacado — que é **revogável a
qualquer momento**. A consequência de produto é concreta: revogar precisa apagar
sintomas e transcrições **sem** quebrar a obrigação do profissional de manter o
prontuário dele.

### Como o consentimento é registrado

`TermoVersionado` guarda o texto e seu `hash_sha256`; `AceiteTermo` liga usuário
+ termo + `aceito_em` + IP + user-agent (+ `sessao_id` no caso do disclaimer por
sessão) e tem `revogado_em`. A prova é do **texto exato** que a pessoa leu, não
de "aceitou os termos".

## Conselho Federal de Psicologia

- **Res. CFP nº 9/2024** (assinada em 18/07/2024, publicada no DOU em 30/07/2024)
  regulamenta o exercício da Psicologia mediado por TDICs e **revogou a Res.
  11/2018 e a 04/2020**, descontinuando o cadastro no **e-Psi**.
  → A verificação obrigatória passa a ser **registro ativo no CRP**. O campo
  `epsi_*` permanece no modelo apenas como histórico opcional.
- **Res. CFP nº 001/2009** continua em vigor (não foi revogada pela 006/2019):
  registro documental obrigatório, **guarda mínima de 5 anos**; a Lei
  13.787/2018 permite eliminação após 20 anos.
  → **A transcrição não é prontuário.** O prontuário é responsabilidade do
  profissional. Ver [ADR 0003](adr/0003-sem-gravacao-apenas-transcricao.md).

**[JURÍDICO]** Se o produto passar a oferecer prontuário eletrônico, retenção,
exportação e responsabilidade mudam — exige nova ADR e revisão jurídica.

## Decisões técnicas já tomadas

| Decisão | Onde |
|---|---|
| Não persistir áudio nem vídeo; só transcrição cifrada (AES-256-GCM) | [ADR 0003](adr/0003-sem-gravacao-apenas-transcricao.md) |
| Sessão revogável, com IP/UA auditáveis | [ADR 0002](adr/0002-sessao-cookie-vs-jwt.md) |
| Redação de PII nos logs por nome de chave | `app/core/logging.py` |
| Admin **não** vê transcrição, só metadado | `AutorizacaoService` |
| Paciente A pedindo sessão de B recebe **404**, não 403 | não vazar existência |
| `LogAuditoria` append-only | revoke DELETE/UPDATE no role da aplicação |

## Pendências antes de produção

1. **[JURÍDICO]** Base legal definitiva para dado de saúde e política de
   revogação.
2. **[JURÍDICO] Transferência internacional (art. 33).** daily.co (EUA) e
   OpenAI (EUA). Áudio de psicoterapia saindo do Brasil é o ponto mais sensível
   do projeto → recomendação técnica: **transcrever localmente**
   (`faster-whisper`), com API de terceiro apenas por opt-in explícito.
3. **Consentimento do profissional** para a transcrição. O disclaimer atual
   cobre só o paciente.
4. **[JURÍDICO] Menores de 18** — art. 14 exige consentimento de ao menos um
   dos pais ou responsável, e a taxonomia inclui "infantil" e "adolescente".
   **Recomendação: bloquear menores de 18 na Fase 1**, explicitamente, e
   desenhar o fluxo do responsável depois.
5. **Protocolo de risco.** `Sintoma.bandeira_risco` já está modelado. Mínimo
   viável, já implementado no rodapé: banner permanente com **CVV 188**. Falta
   alertar o profissional quando o paciente marca sintoma de risco.
6. **Encarregado (DPO)**, canal do titular, RIPD e plano de resposta a incidente
   — o e-mail do encarregado precisa estar no ar antes do primeiro usuário real.
7. **Direitos do titular (art. 18)** — `SolicitacaoTitular` está modelado;
   implementação na Fase 7.
