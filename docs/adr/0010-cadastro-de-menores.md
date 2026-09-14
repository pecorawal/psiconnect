# ADR 0010 — Cadastro de adolescentes com consentimento do responsável

- **Status:** aceita (itens jurídicos pendentes de validação)
- **Data:** 2026-08-13

## Contexto

A Fase 1 **bloqueava** menores de 18: sem o fluxo de consentimento do art. 14 da
LGPD, recusar era mais defensável do que tratar dado de menor sem base legal.

A decisão de produto mudou: menores podem se cadastrar, ficando pendentes até o
responsável autorizar.

O art. 14 exige consentimento **específico e em destaque** de ao menos um dos
pais ou do responsável. O §5 pede que o controlador faça "todos os esforços
razoáveis para verificar" que o consentimento partiu de quem tem legitimidade.

## Decisão

Dois caminhos, ambos terminando no mesmo registro de consentimento:

### A — o adolescente se cadastra

1. Informa a data de nascimento; a conta nasce **`PENDENTE_RESPONSAVEL`**.
2. Informa nome e contato do responsável.
3. Um convite vai **ao contato do responsável**, com link válido por 7 dias.
4. O responsável abre o link (**página pública** — ele pode não ter conta), envia
   cópia de documento e marca a autorização.
5. A conta vira `ATIVO`.

### B — o responsável cadastra o adolescente

O responsável já autenticado declara o vínculo e cria a conta. O consentimento é
dado no ato, por quem tem legitimidade, e a conta nasce ativa.

## Enquanto pendente, nada acontece

A conta pendente **não vê horários, não agenda, não paga**. O bloqueio está na
tela e no POST — mostrar horários a quem não pode marcar é convidar para uma
frustração no último clique.

## Não confirmado no prazo, o cadastro é APAGADO

Não é limpeza cosmética. Sem o consentimento do art. 14 **não há base legal para
manter o dado**, então o certo é remover, não arquivar. O worker faz isso após 7
dias.

Esta é a ressalva honesta do caminho A: entre o cadastro e a confirmação, a
plataforma guarda dados de um menor **antes** de ter o consentimento. Para
reduzir isso ao mínimo defensável, coleta-se o essencial, nada é processado, e o
que não for confirmado é apagado.

## O bug que quase inutilizou a verificação

Na primeira implementação, o convite era enfileirado com o destino derivado do
usuário dono da notificação — e o dono é **o menor**. O adolescente receberia o
próprio link e se autoautorizaria; o "consentimento do responsável" seria teatro
e a verificação não verificaria nada.

`NotificacaoService.enfileirar` passou a aceitar **destino explícito**, para
mensagens dirigidas a terceiros. Há teste travando esse comportamento.

## O documento

Ver [ADR 0008](0008-minio-links-temporarios.md) para armazenamento e acesso. Em
resumo: cifrado pela aplicação antes de subir, servido apenas por rota
autorizada com trilha de acesso, e **apagado depois da verificação** — permanece
só o registro de que houve, com tipo e últimos dígitos.

## Atendimento a partir de 12 anos

Abaixo disso a plataforma recusa. Psicoterapia infantil tem exigências de
setting, técnica e formação que este produto não contempla — aceitar o cadastro
e depois não ter como atender seria pior do que dizer não na entrada.

**Esta é uma decisão de produto, não jurídica.** Se o escopo passar a incluir
crianças, muda mais do que código.

## O que a tela deixa claro ao responsável

Três coisas, porque autorizar sem entender não é consentimento informado:

1. o documento é guardado criptografado e apagado depois da conferência;
2. só ele e um administrador autorizado podem vê-lo, e todo acesso é registrado;
3. **autorizar não dá acesso ao conteúdo das sessões** — ele é protegido por
   sigilo profissional. O profissional compartilha o que for necessário ao
   acompanhamento, conforme a ética da profissão.

O terceiro ponto é o mais fácil de esquecer e o mais importante: um responsável
que espera ler as sessões do filho e descobre depois que não pode teria sido
enganado pela tela.

## Pendências jurídicas

- **[JURÍDICO]** Validar se documento + confirmação por canal do responsável
  satisfaz "esforços razoáveis" (art. 14 §5) para este contexto.
- **[JURÍDICO]** Política de retenção do registro de verificação depois de o
  adolescente completar 18 anos.
- **[JURÍDICO]** O que fazer se o responsável revogar a autorização durante um
  tratamento em curso.
