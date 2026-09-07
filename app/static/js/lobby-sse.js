// Sala de espera: o estado chega por Server-Sent Events.
//
// Substitui o polling de 2 segundos da Fase 1 -- mas não o apaga. O polling
// continua como plano B para navegador sem EventSource e para stream que cai e
// não volta: é melhor o paciente descobrir a admissão 2 segundos depois do que
// ficar preso numa tela que parou de se atualizar.

(function () {
  "use strict";

  var raiz = document.getElementById("lobby");
  if (!raiz) return;

  var urlEventos = raiz.getAttribute("data-eventos");
  var urlEstado = raiz.getAttribute("data-estado");
  var aviso = document.getElementById("lobby-reconectando");

  //: Reconexões seguidas antes de desistir do SSE e cair no polling.
  var FALHAS_ATE_DESISTIR = 3;
  var falhas = 0;
  var fonte = null;
  var desistiu = false;

  function mostrarAviso(visivel) {
    if (aviso) aviso.hidden = !visivel;
  }

  function cairParaPolling() {
    if (desistiu) return;
    desistiu = true;
    if (fonte) {
      fonte.close();
      fonte = null;
    }
    mostrarAviso(false);
    if (!urlEstado || !window.htmx) return;
    // Exatamente o que o template fazia na Fase 1.
    raiz.setAttribute("hx-get", urlEstado);
    raiz.setAttribute("hx-trigger", "load, every 2s");
    raiz.setAttribute("hx-swap", "innerHTML");
    window.htmx.process(raiz);
  }

  if (!("EventSource" in window) || !urlEventos) {
    cairParaPolling();
    return;
  }

  fonte = new EventSource(urlEventos);

  fonte.addEventListener("estado", function (evento) {
    falhas = 0;
    mostrarAviso(false);
    raiz.innerHTML = evento.data;
  });

  fonte.addEventListener("entrar", function (evento) {
    // O servidor decidiu que dá para entrar: navegação de verdade, não swap --
    // a sala é outra página, com iframe e permissão de câmera.
    if (fonte) fonte.close();
    window.location.href = evento.data;
  });

  fonte.addEventListener("error", function () {
    // O EventSource já tenta reconectar sozinho; aqui só avisamos o usuário e
    // contamos as falhas para saber quando parar de insistir.
    falhas += 1;
    mostrarAviso(true);
    if (falhas >= FALHAS_ATE_DESISTIR) cairParaPolling();
  });

  // Sem isto o stream fica aberto no servidor até o timeout quando a pessoa
  // navega para outra página ou fecha a aba.
  window.addEventListener("pagehide", function () {
    if (fonte) fonte.close();
  });
})();
