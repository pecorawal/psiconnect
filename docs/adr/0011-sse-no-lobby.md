# ADR 0011 — SSE no lobby, com o polling como plano B

- **Status:** aceita
- **Data:** 2026-09-07

## Contexto

A sala de espera da Fase 1 perguntava ao servidor "já posso entrar?" a cada 2
segundos, via `hx-trigger="every 2s"`. Funciona, mas o paciente pode passar 20
minutos ali: são **600 requisições completas** — cookie, resolução de sessão,
consulta ao banco e renderização de fragmento — para transmitir um `sim` que
chega uma vez só.

O que se quer é o oposto: o servidor fala quando tem novidade.

## A tensão que precisou ser resolvida

**Conexão persistente custa conexão de banco.** A forma ingênua de escrever o
stream — abrir a sessão do SQLAlchemy no início e ler dentro do laço — segura
uma conexão do pool por paciente em espera. Com `pool_size=10` e `max_overflow=20`,
30 pessoas no lobby travam a aplicação inteira, inclusive para quem só quer
carregar a home.

E o polling não pode simplesmente sumir: `EventSource` não existe em todo
navegador, e um stream que cai e não volta deixaria o paciente numa tela parada
sem saber que foi admitido.

## Decisão

**SSE em `GET /sessao/{id}/eventos`**, com três regras:

1. **A conexão do banco é devolvida entre as leituras.** O laço faz
   `await sessao.rollback()` antes do `sleep`, o que encerra a transação e
   libera a conexão para o pool; a leitura seguinte abre outra. O objeto do
   usuário é destacado da sessão (`expunge`) para que o rollback não expire seus
   atributos e obrigue a um `SELECT` a mais por ciclo.
2. **Evento só sai quando o HTML muda.** O fragmento é renderizado e comparado
   com o anterior; iguais, nada é enviado. Sem isso o SSE seria polling com
   outro nome. Um comentário `: ping` a cada 15 s impede que um proxy derrube a
   conexão ociosa.
3. **A autorização acontece antes de abrir o stream.** Um 401/404 precisa ser um
   status HTTP de verdade: como stream que fecha no primeiro byte, o
   `EventSource` tentaria reabrir para sempre.

O **polling continua vivo** em `GET /sessao/{id}/estado`. O `js/lobby-sse.js`
liga o SSE quando pode e cai para o polling quando não pode — sem `EventSource`,
ou depois de três reconexões seguidas falhas. O fragmento inicial vem
renderizado do servidor, então a tela nunca aparece vazia.

O stream tem teto de 15 minutos. Ao expirar, o navegador reconecta sozinho: é o
que recicla a conexão e cobre o cliente que sumiu sem fechar a aba.

## Consequências

- Uma conexão HTTP aberta por paciente em espera (não uma conexão de banco).
  Atrás de proxy, isso exige `proxy_buffering off` — o `X-Accel-Buffering: no`
  já vai no cabeçalho.
- Dois caminhos de código para o mesmo estado. O custo é real e foi aceito
  porque o fallback é o código que já existia e continua coberto por teste.
- O `ASGITransport` do httpx junta o corpo inteiro antes de entregar, então a
  suíte não consegue ler um stream infinito: os testes encurtam o teto de vida
  do stream. A entrega incremental foi verificada contra o servidor real.

## Alternativas descartadas

- **WebSocket:** bidirecional para um problema que é de mão única, e reconexão
  por conta própria. O `EventSource` já reconecta sozinho.
- **Long polling:** segura a conexão igual e não ganha nada sobre SSE.
- **Extensão SSE do HTMX:** seria mais um arquivo vendorizado para 40 linhas de
  JavaScript que também precisam decidir o fallback.
