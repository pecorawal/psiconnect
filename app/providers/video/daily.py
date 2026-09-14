"""daily.co — salas privadas e meeting tokens.

O modelo: **uma sala por agendamento**, privada, criada pelo worker em T-20min e
com validade que termina 30 minutos depois do fim previsto. Ninguém entra sem um
*meeting token*, emitido na hora e nunca persistido — token guardado no banco é
token que vaza junto com o banco.

O profissional recebe o token com ``is_owner``; o paciente, um token comum. Mas
**quem admite é o PsiConnect, não o daily.co**: o token do paciente só é emitido
depois que o profissional aperta "admitir" (``paciente_admitido_em``). O
*knocking* do provedor fica ligado como segunda barreira, não como a primeira.

Duas decisões que vêm do [ADR 0003](../../../docs/adr/0003-sem-gravacao-apenas-transcricao.md):

* **gravação não é habilitada** em nenhuma sala. Não existe propriedade "não
  gravar" na API — a gravação existe se for pedida, e nós nunca pedimos;
* **o chat fica desligado**: o que se escreve nele não passa pela transcrição
  consentida nem é apagado com ela.

Como no adaptador do Mercado Pago, usa ``httpx`` direto em vez do SDK: são três
chamadas REST, e o retry fica sob nosso controle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings
from app.core.erros import ErroDominio
from app.core.logging import get_logger
from app.providers.base import Participante, Sala

log = get_logger(__name__)

BASE_URL = "https://api.daily.co/v1"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class FalhaNoVideo(ErroDominio):
    codigo = "falha_video"
    status_http = 502
    mensagem_padrao = "Não foi possível preparar a sala de vídeo agora. Tente novamente."


class DailyVideoProvider:
    nome = "daily"

    def __init__(self, settings: Settings, cliente: httpx.AsyncClient | None = None) -> None:
        self._api_key = settings.daily_api_key
        # Aceita tanto "psiconnect" quanto "psiconnect.daily.co" no .env.
        self._dominio = settings.daily_domain.removesuffix(".daily.co")
        # Cliente injetável para os testes de contrato interceptarem com respx.
        self._cliente = cliente

    # --- HTTP ---------------------------------------------------------------

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        reraise=True,
    )
    async def _requisitar(
        self,
        metodo: str,
        caminho: str,
        *,
        json_body: dict[str, Any] | None = None,
        aceitar_404: bool = False,
    ) -> dict[str, Any]:
        """Chama a API. Repete só falha de transporte.

        4xx não é repetido: o pedido chegou e foi recusado; insistir não muda a
        resposta.
        """
        cliente = self._cliente or httpx.AsyncClient(timeout=TIMEOUT)
        proprio = self._cliente is None
        try:
            resposta = await cliente.request(
                metodo,
                f"{BASE_URL}{caminho}",
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        finally:
            if proprio:
                await cliente.aclose()

        if resposta.status_code == 404 and aceitar_404:
            return {}

        corpo: dict[str, Any] = resposta.json() if resposta.content else {}

        if resposta.status_code >= 400:
            # A mensagem do provedor vai para o log, nunca para a tela: pode
            # conter detalhe de conta e não ajuda quem está tentando entrar.
            log.error(
                "daily.erro_requisicao",
                status=resposta.status_code,
                caminho=caminho,
                erro=corpo.get("info") or corpo.get("error"),
            )
            raise FalhaNoVideo()

        return corpo

    # --- Operações ----------------------------------------------------------

    async def criar_sala(self, nome: str, expira_em: datetime, *, privada: bool = True) -> Sala:
        corpo = {
            "name": nome,
            "privacy": "private" if privada else "public",
            "properties": {
                # `exp` é o fim de vida da sala; `eject_at_room_exp` garante que
                # ela esvazie de fato em vez de virar uma sala aberta esquecida.
                "exp": int(expira_em.timestamp()),
                "eject_at_room_exp": True,
                # Segunda barreira: mesmo com token, o participante bate na
                # porta. A primeira barreira é nossa (ver o docstring do módulo).
                "enable_knocking": True,
                "enable_prejoin_ui": True,
                # Chat desligado: o que se escreve nele escapa do consentimento
                # de transcrição e do expurgo (ADR 0003).
                "enable_chat": False,
                "enable_screenshare": True,
                "lang": "pt",
            },
        }

        dados = await self._criar_ou_reaproveitar(nome, corpo)
        return Sala(
            nome=dados.get("name", nome),
            url=dados.get("url") or f"https://{self._dominio}.daily.co/{nome}",
            expira_em=expira_em,
            provedor_sala_id=str(dados.get("id")) if dados.get("id") else None,
        )

    async def _criar_ou_reaproveitar(self, nome: str, corpo: dict[str, Any]) -> dict[str, Any]:
        """Criar a mesma sala duas vezes é 400 no daily.co, não 200.

        Acontece quando o worker repete o preparo e a linha em ``sessoes`` não
        registrou a sala — e a consulta não pode cair por causa disso.
        """
        try:
            return await self._requisitar("POST", "/rooms", json_body=corpo)
        except FalhaNoVideo:
            existente = await self._requisitar("GET", f"/rooms/{nome}", aceitar_404=True)
            if not existente:
                raise
            log.info("daily.sala_reaproveitada", sala=nome)
            return existente

    async def emitir_token(self, sala: str, participante: Participante, expira_em: datetime) -> str:
        dados = await self._requisitar(
            "POST",
            "/meeting-tokens",
            json_body={
                "properties": {
                    "room_name": sala,
                    # Só o primeiro nome: o provedor não precisa saber mais que
                    # isso, e o nome aparece na tela do outro participante.
                    "user_name": participante.nome,
                    "user_id": participante.id,
                    "is_owner": participante.eh_dono,
                    "exp": int(expira_em.timestamp()),
                }
            },
        )
        token = dados.get("token")
        if not token:
            log.error("daily.token_sem_valor", sala=sala)
            raise FalhaNoVideo()
        return str(token)

    async def admitir(self, sala: str, participante_id: str) -> None:
        """Não faz chamada nenhuma — e é isso mesmo.

        No daily.co quem responde ao *knocking* é o dono, pelo SDK, dentro da
        chamada. A admissão que vale para o negócio é a nossa: registrada em
        ``paciente_admitido_em``, é ela que libera a emissão do token.
        """
        return None

    async def encerrar_sala(self, nome: str) -> None:
        # 404 é sucesso: a sala pode ter expirado sozinha antes do encerramento.
        await self._requisitar("DELETE", f"/rooms/{nome}", aceitar_404=True)

    def url_de_entrada(self, sala_url: str, token: str) -> str:
        """O daily.co lê o token do parâmetro ``t``.

        Um ``?token=`` genérico seria ignorado em silêncio e o participante
        cairia na tela de "sala privada" sem entender por quê.
        """
        separador = "&" if "?" in sala_url else "?"
        return f"{sala_url}{separador}t={token}"
