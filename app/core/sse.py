"""Server-Sent Events: o formato do protocolo, isolado.

Um módulo só para isto porque o formato tem três armadilhas que valem teste:
**uma linha ``data:`` por linha do conteúdo** (o HTML de um fragmento tem
várias, e mandá-lo cru quebra o stream), o **encerramento com linha em branco**
(sem ele o navegador segura o evento no buffer esperando o resto), e o
**comentário de keep-alive**, que impede o proxy de derrubar uma conexão que
passou minutos em silêncio — exatamente o caso de quem espera no lobby.
"""

from __future__ import annotations

#: Comentário SSE: o navegador ignora, o proxy vê tráfego e mantém a conexão.
KEEPALIVE = ": ping\n\n"

#: Buferizar um stream é o mesmo que não ter stream. O ``X-Accel-Buffering``
#: desliga o buffer do nginx, que é onde isto costuma morrer em produção.
CABECALHOS = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
}

MEDIA_TYPE = "text/event-stream"


def formatar_evento(
    dados: str,
    *,
    evento: str | None = None,
    identificador: str | None = None,
    retry_ms: int | None = None,
) -> str:
    """Monta um evento SSE a partir de um payload que pode ter várias linhas.

    ``retry_ms`` diz ao navegador quanto esperar antes de reconectar sozinho.
    """
    linhas: list[str] = []
    if evento is not None:
        linhas.append(f"event: {evento}")
    if identificador is not None:
        linhas.append(f"id: {identificador}")
    if retry_ms is not None:
        linhas.append(f"retry: {retry_ms}")
    # `splitlines()` de uma string vazia devolve [] -- e um evento sem nenhuma
    # linha `data:` não chega ao ouvinte do lado do cliente.
    for linha in dados.splitlines() or [""]:
        linhas.append(f"data: {linha}")
    return "\n".join(linhas) + "\n\n"
