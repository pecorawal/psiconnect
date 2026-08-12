# ADR 0001 — FastAPI + Jinja2 + HTMX em vez de SPA

- **Status:** aceita
- **Data:** 2026-08-12

## Contexto

O requisito original pede um sistema "em python", com "visual MUITO clean, de
fácil aceitação e cadastramento", web mas que funcione bem em tablet e celular
"de maneira que seja possível utilizar por qualquer um dos dispositivos".

Havia três caminhos: (a) FastAPI servindo HTML com HTMX, (b) FastAPI como API
JSON + SPA React/Vite instalável como PWA, (c) frontend em Python puro
(Reflex/NiceGUI).

## Decisão

**(a) FastAPI + Jinja2 + HTMX + Tailwind**, server-rendered, num único codebase,
instalável como PWA via manifest + service worker.

## Consequências

Positivas:

- Um processo, um deploy, uma linguagem. Produção não instala Node — o CSS é
  compilado em dev e o `app/static/css/app.css` é **commitado** de propósito.
- Sem contrato duplicado entre backend e frontend, e sem estado de UI replicado
  no cliente. Uma regra de negócio existe num lugar só.
- Time pequeno consegue evoluir o produto inteiro sem trocar de contexto.

Negativas / mitigação:

- Interações muito ricas exigem JS pontual. Aceito: a única tela realmente
  interativa é a sala de vídeo (Fase 4), que carrega o SDK do daily.co
  vendorizado.
- Um app móvel nativo futuro precisaria de API JSON. Mitigado desde já:
  `app/api/v1/` existe e compartilha **os mesmos services** que `app/web/`. A
  API não é retrabalho, é outra fachada sobre a mesma regra.
- Offline real é limitado. Aceito: uma consulta psicológica exige rede de
  qualquer forma, e o service worker **não deve** cachear rota com dado clínico.

## Notas

Todo asset de terceiro é vendorizado em `app/static/vendor/`, nunca via CDN:
uma CSP restrita bloquearia, e carregar script externo numa página com dado de
saúde é risco desnecessário.
