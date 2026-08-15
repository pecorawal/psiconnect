"""Painel: próximas sessões de cada papel."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.core.deps import DbSession, UsuarioAtual
from app.core.templating import responder
from app.core.tempo import agora_utc
from app.models import Agendamento, Papel, Sessao, StatusAgendamento
from app.services.credito_service import CreditoService

router = APIRouter(tags=["painel"])

#: Estados que o usuário ainda vê como "por vir".
ATIVOS = (
    StatusAgendamento.PENDENTE_PAGAMENTO,
    StatusAgendamento.CONFIRMADO,
    StatusAgendamento.EM_ANDAMENTO,
)


def montar(templates: Jinja2Templates) -> APIRouter:
    @router.get("/painel", name="painel")
    async def painel(request: Request, usuario: UsuarioAtual, sessao: DbSession) -> Response:
        eh_profissional = usuario.papel is Papel.PROFISSIONAL
        coluna = Agendamento.profissional_id if eh_profissional else Agendamento.paciente_id

        consulta = (
            select(Agendamento)
            .where(coluna == usuario.id, Agendamento.status.in_(ATIVOS))
            .order_by(Agendamento.inicio_utc)
            .limit(20)
        )
        proximos = list((await sessao.execute(consulta)).scalars().all())

        # Sessões já preparadas, para o painel oferecer o botão de entrar.
        sessoes = {}
        if proximos:
            linhas = (
                await sessao.execute(
                    select(Sessao).where(Sessao.agendamento_id.in_([a.id for a in proximos]))
                )
            ).scalars()
            sessoes = {s.agendamento_id: s for s in linhas}

        realizados = list(
            (
                await sessao.execute(
                    select(Agendamento)
                    .where(coluna == usuario.id, Agendamento.status == StatusAgendamento.REALIZADO)
                    .order_by(Agendamento.inicio_utc.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )

        return responder(
            request,
            templates,
            template_completo="painel/index.html",
            contexto={
                "titulo": "Seu painel",
                "eh_profissional": eh_profissional,
                "proximos": proximos,
                "realizados": realizados,
                "sessoes": sessoes,
                # Saldo de pacote: sem isto o paciente não sabe que já tem
                # sessões pagas e acaba comprando de novo.
                "creditos": (
                    [] if eh_profissional else await CreditoService(sessao).saldo(usuario.id)
                ),
                "agora": agora_utc(),
            },
        )

    return router
