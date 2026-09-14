# ADR 0002 — Cookie de sessão httpOnly em vez de JWT

- **Status:** aceita
- **Data:** 2026-08-12

## Contexto

A aplicação é server-rendered com HTMX e trata dado sensível de saúde (LGPD
art. 11). Era preciso escolher entre JWT stateless e sessão referenciável.

## Decisão

**Cookie de sessão `HttpOnly` com store no servidor** (tabela `SessaoLogin`).

- Valor: token opaco `secrets.token_urlsafe(32)`, assinado com `itsdangerous`.
- No banco guarda-se apenas o `SHA-256` do token.
- Flags: `HttpOnly`, `Secure` (fora de dev), `SameSite=Lax`, `Path=/`,
  `Max-Age` 14 dias com renovação deslizante.
- CSRF por double-submit: cookie legível pelo JS + header `X-CSRF-Token`
  injetado globalmente pelo HTMX.
- `/api/v1` usa **Bearer token opaco na mesma tabela** (`origem={WEB,APP}`) —
  não JWT. Um único conceito de sessão, uma única revogação.

## Justificativa

1. **XSS.** JWT em `localStorage` exige JS para anexar `Authorization` em toda
   requisição e fica legível por qualquer script injetado. Cookie `HttpOnly` é
   invisível ao JS e o navegador o envia sozinho em cada `hx-get`/`hx-post`.
   Num app de saúde, trocar isso por "statelessness" é péssimo negócio.
2. **Revogação imediata.** Suspender um profissional, encerrar sessão num
   aparelho perdido, atender pedido de exclusão da LGPD — tudo isso exige
   invalidar sessão *agora*. JWT stateless só revoga com uma blocklist, ou seja,
   reinventando o session store com mais partes móveis.
3. **Auditoria de graça.** `SessaoLogin` guarda IP, user-agent e último acesso,
   que é exatamente o rastro que a LGPD espera de um sistema com dado sensível.

## Consequências

- Toda requisição autenticada consulta a sessão. Custo baixo (índice por hash) e
  aceitável na escala do produto; se virar gargalo, cacheia-se em Redis mantendo
  a semântica de revogação.
- Escalar horizontalmente exige o store compartilhado — que já é o Postgres.
