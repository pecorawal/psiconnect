// Configuração global do HTMX.
//
// O app é server-rendered: este é praticamente todo o JavaScript de aplicação
// que existe fora da sala de vídeo.

(function () {
  "use strict";

  document.addEventListener("htmx:configRequest", function (evento) {
    // CSRF: double-submit. O cookie é legível pelo JS de propósito -- o cookie
    // de SESSÃO é HttpOnly e continua invisível. (Fase 1, ADR 0002.)
    var csrf = document.body.getAttribute("data-csrf");
    if (csrf) {
      evento.detail.headers["X-CSRF-Token"] = csrf;
    }
  });

  // Por padrão o HTMX ignora respostas 4xx. Nós usamos 422 para erro de regra
  // de negócio e queremos que o fragmento de alerta seja inserido.
  document.addEventListener("htmx:beforeSwap", function (evento) {
    var status = evento.detail.xhr.status;
    if (status === 422 || status === 409 || status === 410) {
      evento.detail.shouldSwap = true;
      evento.detail.isError = false;
    }
  });

  // Sessão expirada no meio de um hx-post: manda para o login em vez de trocar
  // o formulário por uma página de login dentro de um <div>.
  document.addEventListener("htmx:beforeSwap", function (evento) {
    if (evento.detail.xhr.status === 401) {
      evento.detail.shouldSwap = false;
      window.location.href = "/entrar?expirou=1";
    }
  });

  // Falha de rede: o usuário precisa saber que nada foi salvo.
  document.addEventListener("htmx:sendError", function () {
    var alvo = document.getElementById("alerta");
    if (!alvo) return;
    alvo.innerHTML =
      '<div role="alert" class="my-4 rounded-xl border border-rose-200 bg-rose-50 ' +
      'px-4 py-3 text-sm text-rose-900">Não conseguimos falar com o servidor. ' +
      "Verifique sua conexão e tente de novo.</div>";
  });
})();
