# ADR 0013 — E-mail em texto puro, sem dado clínico e com link para a aplicação

- **Status:** aceita
- **Data:** 2026-11-27

## Contexto

Com o `SmtpEmailProvider` (N1), as mensagens da outbox passam a sair de verdade.
O corpo de um e-mail atravessa servidores que a plataforma não controla (o
provedor de envio, o do destinatário, filtros de spam, backups) e fica guardado
na caixa de entrada por tempo indefinido, muitas vezes num aparelho
compartilhado. Para quem trata dado de saúde (LGPD art. 11), o e-mail é o canal
menos protegido que existe.

## Decisão

1. **Nada de conteúdo clínico no e-mail.** Sintoma, queixa, especialidade
   buscada e trecho de sessão nunca entram no contexto da notificação. O alerta
   de risco (S1) diz só "há um aviso importante sobre <primeiro nome> no seu
   painel"; o detalhe fica atrás do login.
2. **Texto puro, sem HTML.** Sem HTML não há pixel de rastreio, imagem remota nem
   link disfarçado: abrir o e-mail não avisa ninguém, e o paciente vê o endereço
   real de cada link.
3. **Os links levam à aplicação, nunca a um recurso direto.** O lembrete da sessão
   aponta para `/sessao/{id}`, que passa pelo termo e pelo lobby, e não para a URL
   da sala no daily.co. Quem tiver acesso ao e-mail não entra na sala sem login, e
   a regra de admissão do paciente (sem `paciente_admitido_em`, sem token) vale
   também para quem chega pelo e-mail.
4. **Primeiro nome, não nome completo,** sempre que a mensagem fala de outra
   pessoa (o paciente para o profissional, o menor para o responsável).
5. **Texto em template versionado** (`app/templates/email/<template>.txt`) com
   `StrictUndefined`: contexto faltando vira erro e retry, não e-mail com buraco.
   Um teste de contrato exige que todo template da outbox tenha texto de e-mail e
   seja renderizado com as chaves que os serviços realmente gravam.

## Consequências

- Os e-mails são sóbrios; não há identidade visual nem botão. Se um dia houver
  HTML, ele precisa manter as regras 1 a 4 (sem imagem remota, sem rastreio).
- O profissional precisa entrar no painel para saber do que se trata o alerta.
  É o custo aceito: o login é o controle de acesso ao dado sensível.
- Em produção, o boot falha com `NOTIFICACAO_PROVIDER=console`, e o SMTP exige TLS e
  autenticação: e-mail em claro na rede exporia endereço e assunto.
