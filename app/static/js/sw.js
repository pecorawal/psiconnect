// Service worker do PsiConnect.
//
// REGRA QUE MANDA AQUI: o cache guarda **só o app shell** -- CSS, JS e ícones.
// Nenhuma rota da aplicação entra, nunca. Qualquer página do PsiConnect pode
// conter nome de paciente, horário de consulta, sintoma ou transcrição; deixar
// isso no disco do dispositivo é criar uma cópia de dado sensível de saúde
// (LGPD art. 11) fora do controle da plataforma, que sobrevive ao logout e vai
// junto se o aparelho for perdido.
//
// O ganho do PWA aqui não é ler consulta offline -- é abrir como app, instalar
// na tela inicial e não baixar o CSS toda vez.

"use strict";

// Mudar a versão descarta o cache anterior no `activate`.
var CACHE = "psiconnect-shell-v1";

// Só o que está nesta lista é guardado. Ver `tests/web/test_pwa.py`, que falha
// se alguém acrescentar uma rota de aplicação.
var SHELL = [
  "/offline",
  "/static/css/app.css",
  "/static/vendor/htmx.min.js",
  "/static/js/htmx-config.js",
  "/static/js/lobby-sse.js",
  "/static/js/pwa.js",
  "/static/img/favicon.svg",
  "/static/img/icone-192.png",
  "/static/img/icone-512.png",
];

self.addEventListener("install", function (evento) {
  evento.waitUntil(
    caches
      .open(CACHE)
      .then(function (cache) {
        return cache.addAll(SHELL);
      })
      // A versão nova assume sem esperar a aba antiga fechar.
      .then(function () {
        return self.skipWaiting();
      })
  );
});

self.addEventListener("activate", function (evento) {
  evento.waitUntil(
    caches
      .keys()
      .then(function (nomes) {
        return Promise.all(
          nomes.map(function (nome) {
            return nome === CACHE ? null : caches.delete(nome);
          })
        );
      })
      .then(function () {
        return self.clients.claim();
      })
  );
});

function ehDoShell(url) {
  return url.pathname === "/offline" || url.pathname.indexOf("/static/") === 0;
}

self.addEventListener("fetch", function (evento) {
  var requisicao = evento.request;

  // Não-GET nunca passa por cache: são as escritas do app.
  if (requisicao.method !== "GET") return;

  var url = new URL(requisicao.url);
  if (url.origin !== self.location.origin) return;

  if (ehDoShell(url)) {
    evento.respondWith(
      caches.match(requisicao).then(function (guardado) {
        // Cache-first: o shell é versionado pelo nome do cache.
        return (
          guardado ||
          fetch(requisicao).then(function (resposta) {
            if (resposta && resposta.ok) {
              var copia = resposta.clone();
              caches.open(CACHE).then(function (cache) {
                cache.put(requisicao, copia);
              });
            }
            return resposta;
          })
        );
      })
    );
    return;
  }

  // Todo o resto -- inclusive tudo que tem dado clínico, e o stream SSE do
  // lobby -- vai para a rede sem passar por aqui. A única cortesia é trocar o
  // erro do navegador por uma página nossa quando a navegação falha.
  if (requisicao.mode === "navigate") {
    evento.respondWith(
      fetch(requisicao).catch(function () {
        return caches.match("/offline");
      })
    );
  }
});
