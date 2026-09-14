"""Rotas auxiliares de desenvolvimento.

**Só existem quando ``APP_ENV=dev``** — o router nem é montado fora disso. São
atalhos que substituem, no ambiente local, coisas que em produção acontecem por
worker ou por webhook do provedor: preparar a sala antes da hora, confirmar um
Pix, inspecionar a caixa de notificações.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app.core.deps import Config, DbSession, ProvidersAtuais, UsuarioAtual
from app.core.erros import NaoEncontrado
from app.core.templating import responder
from app.core.tempo import agora_utc
from app.db.sessao import UnitOfWork
from app.models import (
    Agendamento,
    Notificacao,
    Pagamento,
    PerfilProfissional,
    StatusCadastro,
    StatusPagamento,
)
from app.services.checkout_service import CheckoutService
from app.services.parametros_service import ParametrosService
from app.services.sessao_service import SessaoService

router = APIRouter(prefix="/dev", tags=["dev"], include_in_schema=False)


def montar(templates: Jinja2Templates) -> APIRouter:
    @router.get("/notificacoes", name="dev_notificacoes")
    async def notificacoes(request: Request, sessao: DbSession) -> Response:
        """A caixa de saída. Com NOTIFICACAO_PROVIDER=console é aqui que se vê
        o que teria ido para o WhatsApp e o e-mail."""
        linhas = list(
            (
                await sessao.execute(
                    select(Notificacao).order_by(Notificacao.criado_em.desc()).limit(50)
                )
            )
            .scalars()
            .all()
        )
        return responder(
            request,
            templates,
            template_completo="dev/notificacoes.html",
            contexto={"titulo": "Notificações (dev)", "notificacoes": linhas},
        )

    @router.post("/aprovar-profissional/{profissional_id}", name="dev_aprovar_profissional")
    async def aprovar_profissional(
        request: Request, sessao: DbSession, profissional_id: uuid.UUID
    ) -> Response:
        """Faz o papel do admin verificando o registro no conselho.

        Enquanto o cadastro não é aprovado, o profissional **não aparece** para
        pacientes — isso é intencional e vale em produção. O que muda aqui é
        apenas quem aprova: na Fase 6 é o painel admin, com verificação do CRP
        contra o cadastro público do CFP.
        """
        perfil = await sessao.get(PerfilProfissional, profissional_id)
        if perfil is None:
            raise NaoEncontrado("Profissional não encontrado.")

        async with UnitOfWork(sessao):
            perfil.status_cadastro = StatusCadastro.APROVADO
            perfil.registro_verificado_em = agora_utc()
        return RedirectResponse("/painel", status_code=303)

    @router.post("/preparar-sala/{agendamento_id}", name="dev_preparar_sala")
    async def preparar_sala(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        """Antecipa o que o worker faria em T-20min."""
        agendamento = await sessao.get(Agendamento, agendamento_id)
        if agendamento is None:
            raise NaoEncontrado("Agendamento não encontrado.")

        servico = SessaoService(sessao, ParametrosService(sessao, settings), providers.video)
        async with UnitOfWork(sessao):
            await servico.preparar_sala(agendamento)
        return RedirectResponse("/painel", status_code=303)

    @router.post("/confirmar-pix/{pagamento_id}", name="dev_confirmar_pix")
    async def confirmar_pix(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        pagamento_id: uuid.UUID,
    ) -> Response:
        """Simula o pagador escaneando o QR.

        Em produção quem faz isso é o webhook do Mercado Pago (Fase 2); o
        caminho a partir daqui é exatamente o mesmo.
        """
        pagamento = await sessao.get(Pagamento, pagamento_id)
        if pagamento is None:
            raise NaoEncontrado("Pagamento não encontrado.")
        if pagamento.status is StatusPagamento.APROVADO:
            return RedirectResponse("/painel", status_code=303)

        agendamento = await sessao.scalar(
            select(Agendamento)
            .join(
                Pagamento,
                Pagamento.compra_plano_id.is_not(None),
            )
            .where(Agendamento.paciente_id == usuario.id)
            .order_by(Agendamento.criado_em.desc())
            .limit(1)
        )
        if agendamento is None:
            raise NaoEncontrado("Agendamento não encontrado.")

        servico = CheckoutService(sessao, ParametrosService(sessao, settings), providers.pagamento)
        async with UnitOfWork(sessao):
            await servico.confirmar_pagamento(pagamento, agendamento)
        return RedirectResponse("/painel", status_code=303)

    return router
