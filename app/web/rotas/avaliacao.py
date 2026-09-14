"""Avaliação de fim de sessão."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import DbSession, PacienteAtual
from app.core.erros import NaoEncontrado
from app.core.templating import responder
from app.db.sessao import UnitOfWork
from app.models import Agendamento, StatusAgendamento
from app.services.avaliacao_service import AvaliacaoService

router = APIRouter(prefix="/avaliacao", tags=["avaliacao"])


def montar(templates: Jinja2Templates) -> APIRouter:
    @router.get("/{agendamento_id}", name="avaliacao")
    async def formulario(
        request: Request,
        paciente: PacienteAtual,
        sessao: DbSession,
        agendamento_id: uuid.UUID,
    ) -> Response:
        agendamento = await sessao.get(Agendamento, agendamento_id)
        if agendamento is None or agendamento.paciente_id != paciente.usuario_id:
            raise NaoEncontrado("Sessão não encontrada.")
        if agendamento.status is not StatusAgendamento.REALIZADO:
            raise NaoEncontrado("Só é possível avaliar uma sessão já realizada.")

        return responder(
            request,
            templates,
            template_completo="avaliacao/formulario.html",
            contexto={"titulo": "Como foi sua sessão?", "agendamento": agendamento},
        )

    @router.post("/{agendamento_id}")
    async def responder_avaliacao(
        request: Request,
        paciente: PacienteAtual,
        sessao: DbSession,
        agendamento_id: uuid.UUID,
        nota_plataforma: Annotated[int, Form()],
        nota_profissional: Annotated[int, Form()],
        nota_proprio_cuidado: Annotated[int, Form()],
        comentario_plataforma: Annotated[str, Form()] = "",
        comentario_profissional: Annotated[str, Form()] = "",
        comentario_proprio_cuidado: Annotated[str, Form()] = "",
    ) -> Response:
        async with UnitOfWork(sessao):
            await AvaliacaoService(sessao).responder(
                paciente_id=paciente.usuario_id,
                agendamento_id=agendamento_id,
                nota_plataforma=nota_plataforma,
                nota_profissional=nota_profissional,
                nota_proprio_cuidado=nota_proprio_cuidado,
                comentario_plataforma=comentario_plataforma,
                comentario_profissional=comentario_profissional,
                comentario_proprio_cuidado=comentario_proprio_cuidado,
            )
        return RedirectResponse("/painel?avaliada=1", status_code=303)

    return router
