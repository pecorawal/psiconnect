// Registro do service worker e o convite para instalar.
//
// O registro é silencioso e falha em silêncio: sem HTTPS (ou localhost) o
// navegador recusa, e isso não pode quebrar página nenhuma.

(function () {
  "use strict";

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {
        // Sem PWA o app continua inteiro; não há nada a dizer ao usuário.
      });
    });
  }

  // O Chrome só deixa chamar prompt() dentro do gesto do usuário, então o
  // evento é guardado e o botão -- escondido até aqui -- aparece.
  var convite = null;
  var botao = document.getElementById("instalar-app");

  window.addEventListener("beforeinstallprompt", function (evento) {
    evento.preventDefault();
    convite = evento;
    if (botao) botao.hidden = false;
  });

  if (botao) {
    botao.addEventListener("click", function () {
      if (!convite) return;
      convite.prompt();
      convite = null;
      botao.hidden = true;
    });
  }

  window.addEventListener("appinstalled", function () {
    convite = null;
    if (botao) botao.hidden = true;
  });
})();
