# ADR 0012 — PWA que instala, mas não guarda nada de clínico

- **Status:** aceita
- **Data:** 2026-09-07

## Contexto

O brief original pede que o paciente use a plataforma "tanto pela web quanto
pelo seu aplicativo em celular". Um PWA entrega isso sem uma segunda base de
código: ícone na tela inicial, abertura em tela cheia, sem barra de navegador.

Só que a receita padrão de PWA é *cachear páginas para funcionar offline* — e
aqui isso é exatamente o que não se pode fazer.

## A tensão que precisou ser resolvida

Qualquer página do PsiConnect pode conter nome de paciente, horário de consulta,
sintoma selecionado ou, mais adiante, trecho de transcrição. Um service worker
que guarda respostas de navegação cria uma **cópia de dado pessoal sensível de
saúde** (LGPD art. 11) no disco do aparelho:

- sobrevive ao logout, porque o cache não tem noção de sessão;
- vai junto se o aparelho for perdido, emprestado ou vendido;
- fica fora de qualquer expurgo que a plataforma execute (art. 18).

O ganho de ter a agenda offline não paga esse risco: sem rede não dá para
agendar, pagar nem entrar em sessão de vídeo.

## Decisão

Service worker em `/sw.js` (na raiz — fora dela ele só controlaria o próprio
diretório), com uma regra única:

> **Só o app shell entra no cache.** CSS, JavaScript, ícones e a página
> `/offline`. Nenhuma rota da aplicação, nunca.

Concretamente, no `fetch`:

| Requisição | O que acontece |
|---|---|
| `GET /static/*` e `/offline` | cache-first, versionado pelo nome do cache |
| Navegação que falha por falta de rede | devolve `/offline` (não guarda nada) |
| **Todo o resto** — inclusive o SSE do lobby e qualquer página com dado clínico | passa direto para a rede, sem tocar no cache |

Não-GET nunca passa pelo service worker.

A regra é testada, não só documentada: `tests/web/test_pwa.py` lê o array
`SHELL` **do arquivo JavaScript que roda no navegador** e falha se aparecer
qualquer caminho que não seja `/static/…` ou `/offline`.

A página offline é autocontida — estilo embutido, sem herdar o layout. Servida
pelo cache, herdar o cabeçalho significaria mostrar um estado de login que pode
estar errado. Ela repete o canal de crise: o **188** funciona sem internet.

## Consequências

- O app instala e abre como aplicativo, mas **não funciona offline** — mostra
  uma tela explicando isso. É o comportamento correto para o domínio.
- O `manifest.webmanifest` é servido por rota, não como arquivo estático: o
  `mimetypes` do Python não conhece a extensão, e sem
  `application/manifest+json` o navegador ignora o manifesto.
- Ícones PNG (192, 512 e um *maskable*) são gerados por
  `scripts/gerar_icones.py` a partir da mesma marca do favicon, para que trocar
  a cor não exija abrir um editor de imagem.
- Uma CSP restrita (Fase 7) não quebra nada disto: não há script nem handler
  inline em nenhuma das páginas novas.
