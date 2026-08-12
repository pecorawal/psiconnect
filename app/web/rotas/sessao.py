"""Sessão: disclaimer, lobby, sala e encerramento."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import Config, Contexto, DbSession, ProvidersAtuais, UsuarioAtual
from app.core.templating import responder
from app.db.sessao import UnitOfWork
from app.models import Papel, TipoTermo
from app.services.parametros_service import ParametrosService
from app.services.sessao_service import SessaoService
from app.services.termos_service import TermosService

router = APIRouter(prefix="/sessao", tags=["sessao"])


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
        token = await servico.token_de_entrada(sessao_atendimento, usuario)
        eh_profissional = usuario.papel is Papel.PROFISSIONAL

        return responder(
            request,
            templates,
            template_completo="sessao/sala.html",
            contexto={
                "titulo": "Sessão",
                "sessao": sessao_atendimento,
                "token": token,
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
