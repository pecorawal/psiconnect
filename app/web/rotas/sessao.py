"""Sessão: disclaimer, lobby, sala e encerramento."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import Config, Contexto, DbSession, ProvidersAtuais, UsuarioAtual
from app.core.logging import get_logger
from app.core.sse import CABECALHOS, KEEPALIVE, MEDIA_TYPE, formatar_evento
from app.core.templating import responder
from app.db.sessao import UnitOfWork
from app.models import Papel, TipoTermo
from app.services.parametros_service import ParametrosService
from app.services.sessao_service import SessaoService
from app.services.termos_service import TermosService

log = get_logger(__name__)

router = APIRouter(prefix="/sessao", tags=["sessao"])

#: Intervalo entre leituras do estado no stream. A suíte reduz este valor.
INTERVALO_SSE_S = 2.0
#: Silêncio maior que isto e um proxy no caminho pode derrubar a conexão.
INTERVALO_KEEPALIVE_S = 15.0
#: Teto de vida do stream. Ao fim dele o EventSource reconecta sozinho — o que
#: também recicla a conexão e cobre o caso do cliente que sumiu sem avisar.
DURACAO_MAXIMA_STREAM_S = 900.0
#: Quanto o navegador espera antes de tentar reconectar.
RETRY_CLIENTE_MS = 3000


def montar(templates: Jinja2Templates) -> APIRouter:
    def _servico(sessao: DbSession, settings: Config, providers: ProvidersAtuais) -> SessaoService:
        return SessaoService(sessao, ParametrosService(sessao, settings), providers.video)

    @router.get("/{agendamento_id}", name="sessao_disclaimer")
    async def disclaimer(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        """Porta de entrada. O paciente vê o disclaimer; o profissional vai direto."""
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)

        if usuario.papel is Papel.PROFISSIONAL or sessao_atendimento.transcricao_consentida:
            return RedirectResponse(f"/sessao/{agendamento_id}/lobby", status_code=303)

        termos = await TermosService(sessao).vigentes(TipoTermo.CONSENT_TRANSCRICAO)
        return responder(
            request,
            templates,
            template_completo="sessao/disclaimer.html",
            contexto={
                "titulo": "Antes de entrar",
                "sessao": sessao_atendimento,
                "termo": termos.get(TipoTermo.CONSENT_TRANSCRICAO),
            },
        )

    @router.post("/{agendamento_id}/consentir")
    async def consentir(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        contexto: Contexto,
        agendamento_id: uuid.UUID,
        ciente: Annotated[str, Form()] = "",
    ) -> Response:
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)

        async with UnitOfWork(sessao):
            await servico.registrar_consentimento(
                sessao_atendimento,
                usuario,
                aceitou=bool(ciente),
                ip=contexto.ip,
                user_agent=contexto.user_agent,
            )
        return RedirectResponse(f"/sessao/{agendamento_id}/lobby", status_code=303)

    @router.get("/{agendamento_id}/lobby", name="sessao_lobby")
    async def lobby(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)

        async with UnitOfWork(sessao):
            estado = await servico.entrar_no_lobby(sessao_atendimento, usuario)

        if estado.pode_entrar:
            return RedirectResponse(f"/sessao/{agendamento_id}/sala", status_code=303)

        return responder(
            request,
            templates,
            template_completo="sessao/lobby.html",
            template_parcial="partials/estado_lobby.html",
            contexto={
                "titulo": "Sala de espera",
                "estado": estado,
                "agendamento_id": agendamento_id,
            },
        )

    @router.get("/{agendamento_id}/estado", name="sessao_estado")
    async def estado(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        """Polling do lobby. Devolve só o fragmento de estado."""
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)
        estado_atual = await servico.estado_lobby(sessao_atendimento, usuario)

        headers = {}
        if estado_atual.pode_entrar:
            # HX-Redirect faz o HTMX navegar de verdade, em vez de injetar a
            # sala dentro do fragmento do lobby.
            headers["HX-Redirect"] = f"/sessao/{agendamento_id}/sala"

        return templates.TemplateResponse(
            request=request,
            name="partials/estado_lobby.html",
            context={"estado": estado_atual, "agendamento_id": agendamento_id},
            headers=headers,
        )

    @router.get("/{agendamento_id}/eventos", name="sessao_eventos")
    async def eventos(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        """Estado do lobby por SSE — substitui o polling de 2s da Fase 1.

        A primeira leitura acontece **fora** do gerador de propósito: assim um
        erro de autorização vira 401/404 de verdade, e não um stream que abre e
        morre no primeiro byte — que o EventSource tentaria reabrir para sempre.
        """
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)
        estado_inicial = await servico.estado_lobby(sessao_atendimento, usuario)

        template_estado = templates.get_template("partials/estado_lobby.html")
        url_sala = f"/sessao/{agendamento_id}/sala"

        # O usuário sai da sessão do SQLAlchemy: entre uma leitura e outra
        # fazemos rollback para devolver a conexão ao pool, e isso expiraria os
        # atributos dele — custando um SELECT a mais por ciclo só para reler o
        # id. Destacado, ele continua legível e não pertence a transação nenhuma.
        if usuario in sessao:
            sessao.expunge(usuario)

        async def transmitir() -> AsyncIterator[str]:
            estado_atual = estado_inicial
            ultimo_fragmento = ""
            ultimo_envio = time.monotonic()
            limite = time.monotonic() + DURACAO_MAXIMA_STREAM_S

            try:
                while True:
                    fragmento = template_estado.render(
                        {"estado": estado_atual, "agendamento_id": agendamento_id}
                    )
                    if fragmento != ultimo_fragmento:
                        yield formatar_evento(fragmento, evento="estado", retry_ms=RETRY_CLIENTE_MS)
                        ultimo_fragmento = fragmento
                        ultimo_envio = time.monotonic()

                    if estado_atual.pode_entrar:
                        yield formatar_evento(url_sala, evento="entrar")
                        return

                    if time.monotonic() >= limite:
                        return

                    # Devolve a conexão ao pool durante a espera. Um paciente
                    # pode ficar 20 minutos no lobby; segurar uma conexão por
                    # pessoa esgotaria o pool muito antes do volume que a
                    # plataforma precisa aguentar.
                    await sessao.rollback()
                    await asyncio.sleep(INTERVALO_SSE_S)

                    if await request.is_disconnected():
                        return

                    if time.monotonic() - ultimo_envio >= INTERVALO_KEEPALIVE_S:
                        yield KEEPALIVE
                        ultimo_envio = time.monotonic()

                    atual = await servico.buscar_por_agendamento(agendamento_id, usuario)
                    estado_atual = await servico.estado_lobby(atual, usuario)
            except asyncio.CancelledError:
                raise
            except Exception:
                # O stream já começou: não há como devolver um status de erro.
                # Encerrar é o menos ruim — o cliente reconecta e, se o problema
                # persistir, recebe o erro na abertura da próxima conexão.
                log.exception("sessao.sse_interrompido", agendamento_id=str(agendamento_id))
                return

        return StreamingResponse(transmitir(), media_type=MEDIA_TYPE, headers=CABECALHOS)

    @router.post("/{agendamento_id}/admitir", name="sessao_admitir")
    async def admitir(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)
        async with UnitOfWork(sessao):
            await servico.admitir_paciente(sessao_atendimento, usuario)
        return RedirectResponse(f"/sessao/{agendamento_id}/sala", status_code=303)

    @router.get("/{agendamento_id}/sala", name="sessao_sala")
    async def sala(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)
        eh_profissional = usuario.papel is Papel.PROFISSIONAL

        # A regra está no serviço; aqui é só cortesia: mandar de volta para a
        # sala de espera é mais útil do que uma página de erro.
        if not eh_profissional and sessao_atendimento.paciente_admitido_em is None:
            return RedirectResponse(f"/sessao/{agendamento_id}/lobby", status_code=303)

        token = await servico.token_de_entrada(sessao_atendimento, usuario)

        return responder(
            request,
            templates,
            template_completo="sessao/sala.html",
            contexto={
                "titulo": "Sessão",
                "sessao": sessao_atendimento,
                # Cada provedor põe o token num parâmetro diferente: montar a URL
                # aqui deixaria o template dependendo de qual está ligado.
                "url_sala": providers.video.url_de_entrada(
                    sessao_atendimento.sala_url or "", token
                ),
                "eh_profissional": eh_profissional,
                "aguardando_paciente": (
                    eh_profissional and sessao_atendimento.paciente_admitido_em is None
                ),
            },
        )

    @router.post("/{agendamento_id}/encerrar", name="sessao_encerrar")
    async def encerrar(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        servico = _servico(sessao, settings, providers)
        sessao_atendimento = await servico.buscar_por_agendamento(agendamento_id, usuario)
        async with UnitOfWork(sessao):
            await servico.encerrar(sessao_atendimento, usuario)
        return RedirectResponse("/painel", status_code=303)

    # --- Sala simulada (só com VIDEO_PROVIDER=fake) -------------------------

    @router.get("/simulada/{nome}", name="sala_simulada", include_in_schema=False)
    async def sala_simulada(request: Request, settings: Config, nome: str) -> Response:
        """Substitui o iframe do provedor de vídeo no ambiente de dev.

        O fluxo de lobby e admissão é real; só o WebRTC é falso. É isso que tira
        o daily.co do caminho crítico da Fase 1.
        """
        return responder(
            request,
            templates,
            template_completo="sessao/sala_simulada.html",
            contexto={"titulo": "Sala simulada", "nome": nome},
        )

    return router
