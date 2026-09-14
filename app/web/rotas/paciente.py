"""Fluxo do paciente: sintomas → profissional → horário → pagamento."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import Config, DbSession, PacienteAtual, ProvidersAtuais
from app.core.erros import ErroDominio, NaoEncontrado
from app.core.templating import responder
from app.core.tempo import TZ_BR, para_local
from app.db.sessao import UnitOfWork
from app.models import MetodoPagamento, SlugPlano
from app.services.agendamento_service import AgendamentoService, PedidoReserva
from app.services.avaliacao_service import AvaliacaoService
from app.services.checkout_service import CheckoutService
from app.services.credito_service import CreditoService
from app.services.disponibilidade_service import DisponibilidadeService
from app.services.matching_service import MatchingService
from app.services.parametros_service import ParametrosService
from app.services.responsavel_service import CadastroPendente

router = APIRouter(prefix="/paciente", tags=["paciente"])


def montar(templates: Jinja2Templates) -> APIRouter:
    # --- Sintomas -----------------------------------------------------------

    @router.get("/sintomas", name="sintomas")
    async def form_sintomas(
        request: Request, paciente: PacienteAtual, sessao: DbSession
    ) -> Response:
        servico = MatchingService(sessao)
        sintomas = await servico.listar_sintomas()
        por_categoria: dict[str, list[object]] = {}
        for s in sintomas:
            por_categoria.setdefault(s.categoria or "outros", []).append(s)

        atuais = {
            s.sintoma_id: s.intensidade
            for s in await servico.sintomas_do_paciente(paciente.usuario_id)
        }
        return responder(
            request,
            templates,
            template_completo="paciente/sintomas.html",
            contexto={
                "titulo": "Como você está se sentindo?",
                "por_categoria": por_categoria,
                "atuais": atuais,
            },
        )

    @router.post("/sintomas")
    async def salvar_sintomas(
        request: Request, paciente: PacienteAtual, sessao: DbSession
    ) -> Response:
        formulario = await request.form()
        escolhidos: dict[uuid.UUID, int] = {}
        for valor in formulario.getlist("sintoma_id"):
            if not isinstance(valor, str):
                continue
            intensidade = formulario.get(f"intensidade_{valor}")
            escolhidos[uuid.UUID(valor)] = (
                int(intensidade) if isinstance(intensidade, str) and intensidade.isdigit() else 3
            )

        if not escolhidos:
            raise ErroDominio(
                "Selecione ao menos um sintoma para encontrarmos o profissional certo.",
                campo="sintomas",
            )

        async with UnitOfWork(sessao):
            await MatchingService(sessao).registrar_sintomas(paciente, escolhidos)

        return RedirectResponse("/paciente/profissionais", status_code=303)

    # --- Profissionais ------------------------------------------------------

    @router.get("/profissionais", name="profissionais")
    async def listar_profissionais(
        request: Request, paciente: PacienteAtual, sessao: DbSession
    ) -> Response:
        servico = MatchingService(sessao)
        ranking = await servico.profissionais_para(paciente.usuario_id)
        return responder(
            request,
            templates,
            template_completo="paciente/profissionais.html",
            contexto={
                "titulo": "Profissionais para você",
                "ranking": ranking,
                # Sintoma com bandeira de risco destaca o canal de crise.
                "risco": await servico.tem_sintoma_de_risco(paciente.usuario_id),
            },
        )

    # --- Escolha do horário -------------------------------------------------

    @router.get("/agendar/{profissional_id}", name="escolher_horario")
    async def escolher_horario(
        request: Request,
        paciente: PacienteAtual,
        sessao: DbSession,
        settings: Config,
        profissional_id: uuid.UUID,
    ) -> Response:
        # Bloqueia já aqui, e não só no POST: mostrar horários a quem não pode
        # marcar é convidar para uma frustração no último clique.
        if not paciente.pode_agendar:
            raise CadastroPendente()

        matching = MatchingService(sessao)
        perfil = await matching.buscar_profissional(profissional_id)
        if perfil is None:
            raise NaoEncontrado("Profissional não encontrado.")

        # A consulta é marcada na especialidade que casa com o que o paciente
        # relatou -- não na primeira da lista do profissional.
        oferta = await matching.melhor_especialidade(paciente.usuario_id, profissional_id)
        if oferta is None:
            raise NaoEncontrado("Este profissional ainda não definiu suas especialidades.")

        parametros = ParametrosService(sessao, settings)
        duracao = perfil.duracao_sessao_min or await parametros.duracao_sessao_min()
        slots = await DisponibilidadeService(sessao).slots_disponiveis(perfil, duracao_min=duracao)

        # Agrupa por dia local para a UI mostrar "quinta, 14 de agosto".
        por_dia: dict[str, list[object]] = {}
        for slot in slots:
            chave = para_local(slot.inicio_utc, TZ_BR).date().isoformat()
            por_dia.setdefault(chave, []).append(slot)

        return responder(
            request,
            templates,
            template_completo="paciente/horarios.html",
            contexto={
                "titulo": f"Horários de {perfil.nome_exibicao}",
                "perfil": perfil,
                "por_dia": por_dia,
                "duracao": duracao,
                "oferta": oferta,
            },
        )

    @router.post("/agendar/{profissional_id}")
    async def reservar(
        request: Request,
        paciente: PacienteAtual,
        sessao: DbSession,
        settings: Config,
        profissional_id: uuid.UUID,
        inicio: Annotated[str, Form()],
        especialidade_id: Annotated[str, Form()],
    ) -> Response:
        perfil = await MatchingService(sessao).buscar_profissional(profissional_id)
        if perfil is None:
            raise NaoEncontrado("Profissional não encontrado.")

        # Menor sem autorização do responsável não marca nada (LGPD art. 14).
        if not paciente.pode_agendar:
            raise CadastroPendente()

        # R11 — avaliação pendente bloqueia MARCAR nova sessão, e só isso.
        # Nunca bloqueia sair, pedir suporte ou acessar os próprios dados.
        await AvaliacaoService(sessao).exigir_avaliacoes_em_dia(paciente.usuario_id)

        servico = AgendamentoService(sessao, ParametrosService(sessao, settings))
        async with UnitOfWork(sessao):
            agendamento = await servico.reservar(
                PedidoReserva(
                    paciente=paciente,
                    profissional=perfil,
                    especialidade_id=uuid.UUID(especialidade_id),
                    inicio_utc=datetime.fromisoformat(inicio),
                )
            )

        return RedirectResponse(f"/paciente/checkout/{agendamento.id}", status_code=303)

    # --- Checkout -----------------------------------------------------------

    @router.get("/checkout/{agendamento_id}", name="checkout")
    async def form_checkout(
        request: Request,
        paciente: PacienteAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        from app.models import Agendamento

        agendamento = await sessao.get(Agendamento, agendamento_id)
        if agendamento is None or agendamento.paciente_id != paciente.usuario_id:
            raise NaoEncontrado("Agendamento não encontrado.")

        servico = CheckoutService(sessao, ParametrosService(sessao, settings), providers.pagamento)

        # Se o paciente já tem crédito com este profissional, a tela precisa
        # oferecer isso antes de qualquer plano -- senão ele paga duas vezes
        # pela mesma sessão sem perceber.
        creditos = CreditoService(sessao)
        saldos = [
            s
            for s in await creditos.saldo(paciente.usuario_id)
            if s.profissional_id == agendamento.profissional_id
            and s.especialidade_id == agendamento.especialidade_id
        ]
        disponiveis = sum(s.disponiveis for s in saldos)
        expira_em = min((s.expira_em for s in saldos if s.expira_em), default=None)

        return responder(
            request,
            templates,
            template_completo="paciente/checkout.html",
            contexto={
                "titulo": "Confirmar e pagar",
                "agendamento": agendamento,
                "planos": await servico.listar_planos(),
                "metodos": list(MetodoPagamento),
                "creditos_disponiveis": disponiveis,
                "creditos_expiram_em": expira_em,
            },
        )

    @router.post("/checkout/{agendamento_id}/credito")
    async def usar_credito(
        request: Request,
        paciente: PacienteAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
    ) -> Response:
        """Confirma a sessão gastando um crédito do pacote, sem cobrar nada."""
        servico = CheckoutService(sessao, ParametrosService(sessao, settings), providers.pagamento)
        async with UnitOfWork(sessao):
            await servico.usar_credito(paciente=paciente, agendamento_id=agendamento_id)
        return RedirectResponse("/painel", status_code=303)

    @router.post("/checkout/{agendamento_id}")
    async def pagar(
        request: Request,
        paciente: PacienteAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        agendamento_id: uuid.UUID,
        plano: Annotated[str, Form()],
        metodo: Annotated[str, Form()],
    ) -> Response:
        servico = CheckoutService(sessao, ParametrosService(sessao, settings), providers.pagamento)
        async with UnitOfWork(sessao):
            resultado = await servico.finalizar(
                paciente=paciente,
                agendamento_id=agendamento_id,
                plano_slug=SlugPlano(plano),
                metodo=MetodoPagamento(metodo),
            )

        if resultado.aguardando_pagamento:
            return RedirectResponse(
                f"/paciente/pagamento/{resultado.pagamento.id}", status_code=303
            )
        return RedirectResponse("/painel", status_code=303)

    @router.get("/pagamento/{pagamento_id}", name="aguardando_pagamento")
    async def aguardando(
        request: Request, paciente: PacienteAtual, sessao: DbSession, pagamento_id: uuid.UUID
    ) -> Response:
        from app.models import Pagamento

        pagamento = await sessao.get(Pagamento, pagamento_id)
        if pagamento is None:
            raise NaoEncontrado("Pagamento não encontrado.")
        return responder(
            request,
            templates,
            template_completo="paciente/pagamento_pix.html",
            contexto={"titulo": "Aguardando pagamento", "pagamento": pagamento},
        )

    return router
