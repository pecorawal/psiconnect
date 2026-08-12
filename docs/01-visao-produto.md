# Visão de produto

## O problema

Acesso a psicoterapia no Brasil esbarra em custo, em disponibilidade de horário
e na dificuldade de encontrar um profissional com experiência no que a pessoa
está sentindo. O brief original resume a intenção: democratizar custo e acesso,
com atendimento humanizado, acessível 24h de qualquer dispositivo e com sigilo.

## Personas

### Paciente

Alguém em sofrimento emocional que muitas vezes **não sabe nomear** o que tem —
sabe apenas como se sente. Daí a decisão de o onboarding começar por **sintomas**
em linguagem leiga ("não consigo dormir", "sinto o coração acelerar"), não por
especialidade em jargão clínico ("TOC", "TCC").

### Profissional

Psicólogo ou terapeuta com registro ativo no conselho (CRP/CREFITO) que quer
preencher a agenda sem gastar com captação. Escolhe **até 5 especialidades** —
o limite é deliberado: força foco e melhora a qualidade do matching.

### Admin

Opera a plataforma: aprova cadastros, verifica registro no conselho, ajusta
parâmetros (inclusive a comissão). **Não tem acesso a conteúdo clínico.**

## Fluxos

### Paciente — sintomas → horário → pagamento

> Esta ordem resolve uma contradição entre as fontes: o mapa mental dizia
> "após o pagamento, o paciente pode agendar"; o `funcionalidades.md` descrevia
> escolher sintomas e horário primeiro. Decisão: **sintomas → horário → pagamento**,
> porque pedir cartão antes de mostrar valor derruba conversão.

1. Cadastro com dados pessoais.
2. Seleciona os sintomas que mais incomodam (com intensidade).
3. Recebe uma lista de profissionais rankeada por afinidade sintoma↔especialidade.
4. Vê a agenda real e escolhe o horário. **O slot fica reservado** por 15 min.
5. Escolhe o plano (avulso, 5 ou 10 sessões) e paga por Pix, débito ou crédito.
6. Confirmado: recebe notificação por WhatsApp e e-mail, e passa a ver **nome e
   foto** do profissional que vai atender.
7. Não pode marcar mais de **3 sessões por semana**; a agenda sugere repetir o
   mesmo horário até 2× por semana.

### Profissional

1. Cadastro com e-mail, profissão e CRP/CREFITO; lê e aceita o contrato.
2. Perfil: nome, foto, descrição de até 500 caracteres.
3. Escolhe até 5 especialidades, com faixa de preço em cada uma.
4. Monta a agenda semanal de disponibilidade. **Não pode passar de 10 horas de
   atendimento por dia** — contadas em minutos reais, não em número de consultas.
5. Vê no simulador quanto recebe líquido, já descontados comissão, taxa do
   gateway e impostos.

### A sessão

1. **T-20min:** a sala é criada e o link é enviado aos dois.
2. O paciente vê o **disclaimer** sobre transcrição e marca o checkbox de ciência
   — sem isso não entra. O aceite é registrado com o hash do texto lido.
3. Entra no **lobby** e aguarda o profissional **admitir**.
4. A sessão ocorre com vídeo aberto dos dois lados. A transcrição roda; áudio e
   vídeo **não são gravados** ([ADR 0003](adr/0003-sem-gravacao-apenas-transcricao.md)).
5. O paciente pode entrar com até **15 minutos de atraso** sem penalização.
6. Ao final, responde **3 perguntas** obrigatórias: sobre a plataforma, sobre o
   profissional, e sobre o próprio cuidado.

## Gamificação

Serve a um objetivo de qualidade, não a engajamento vazio:

- **Profissional pontual** ao entrar na sala ganha pontos, que definirão a forma
  de bonificação.
- **Paciente que responde a avaliação** ganha pontos.

Ambos são derivados de `EventoSessao` (append-only) e registrados em
`EventoPontuacao` com `UNIQUE (usuario_id, tipo, referencia_id)` — idempotente
por construção, sem risco de pontuar duas vezes o mesmo fato.

## Modelo de receita

Comissão sobre cada atendimento **concluído**, default **5%**, parametrizável
([ADR 0005](adr/0005-comissao-parametrizavel.md)). O dinheiro não transita pela
plataforma: o split do Mercado Pago envia o valor ao profissional e a comissão à
plataforma ([ADR 0004](adr/0004-mercado-pago-split.md)).

## O que o produto **não** é

- **Não** é atendimento de emergência. Em crise: CVV **188**; emergência: **192**.
- **Não** é prontuário eletrônico. O prontuário é obrigação do profissional
  (Res. CFP 001/2009).
- **Não** substitui atendimento presencial quando o profissional o indicar.
