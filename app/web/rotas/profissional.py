"""Área do profissional: perfil, especialidades, simulador e agenda."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import (
    Config,
    DbSession,
    ProfissionalAtual,
    ProvidersAtuais,
    UsuarioAtual,
)
from app.core.dinheiro import reais_para_centavos
from app.core.erros import ErroDominio
from app.core.templating import responder
from app.core.tempo import parse_hhmm
from app.db.sessao import UnitOfWork
from app.models import Conselho, MetodoPagamento
from app.services.disponibilidade_service import DisponibilidadeService
from app.services.parametros_service import ParametrosService
from app.services.perfil_service import DadosPerfil, PerfilProfissionalService
from app.services.regras.precificacao import TabelaTaxas, simular_todos_metodos

router = APIRouter(prefix="/profissional", tags=["profissional"])


def montar(templates: Jinja2Templates) -> APIRouter:
    def _servico(
        sessao: DbSession, settings: Config, providers: ProvidersAtuais
    ) -> PerfilProfissionalService:
        return PerfilProfissionalService(
            sessao, ParametrosService(sessao, settings), providers.armazenamento
        )

    # --- Perfil -------------------------------------------------------------

    @router.get("/perfil", name="perfil_profissional")
    async def form_perfil(
        request: Request, usuario: UsuarioAtual, sessao: DbSession, settings: Config
    ) -> Response:
        return responder(
            request,
            templates,
            template_completo="profissional/perfil.html",
            contexto={
                "titulo": "Seu perfil",
                "perfil": usuario.perfil_profissional,
                "conselhos": list(Conselho),
            },
        )

    @router.post("/perfil")
    async def salvar_perfil(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        nome_exibicao: Annotated[str, Form()],
        conselho: Annotated[str, Form()],
        registro_numero: Annotated[str, Form()],
        registro_uf: Annotated[str, Form()],
        descricao: Annotated[str, Form()],
        foto: Annotated[UploadFile | None, File()] = None,
    ) -> Response:
        servico = _servico(sessao, settings, providers)
        async with UnitOfWork(sessao):
            perfil = await servico.salvar_perfil(
                usuario,
                DadosPerfil(
                    nome_exibicao=nome_exibicao,
                    conselho=Conselho(conselho),
                    registro_numero=registro_numero,
                    registro_uf=registro_uf,
                    descricao=descricao,
                ),
            )
            if foto is not None and foto.filename:
                await servico.salvar_foto(perfil, await foto.read(), foto.filename)

        return RedirectResponse("/profissional/especialidades", status_code=303)

    # --- Especialidades -----------------------------------------------------

    @router.get("/especialidades", name="especialidades_profissional")
    async def form_especialidades(
        request: Request,
        perfil: ProfissionalAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
    ) -> Response:
        servico = _servico(sessao, settings, providers)
        catalogo = await servico.listar_catalogo()
        por_categoria: dict[str, list[object]] = {}
        for esp in catalogo:
            por_categoria.setdefault(esp.categoria or "outras", []).append(esp)

        return responder(
            request,
            templates,
            template_completo="profissional/especialidades.html",
            contexto={
                "titulo": "Suas especialidades",
                "perfil": perfil,
                "por_categoria": por_categoria,
                "escolhidas": {e.especialidade_id: e for e in perfil.especialidades},
                "maximo": await ParametrosService(sessao, settings).max_especialidades(),
            },
        )

    @router.post("/especialidades")
    async def salvar_especialidades(
        request: Request,
        perfil: ProfissionalAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
    ) -> Response:
        formulario = await request.form()
        ids = formulario.getlist("especialidade_id")

        escolhas: list[tuple[uuid.UUID, int, int, int]] = []
        for valor in ids:
            if not isinstance(valor, str):
                continue
            preco_bruto = formulario.get(f"preco_{valor}")
            if not isinstance(preco_bruto, str) or not preco_bruto.strip():
                raise ErroDominio("Informe o preço de cada especialidade escolhida.", campo="preco")
            centavos = reais_para_centavos(preco_bruto)
            # Faixa sugerida em torno do preço padrão: −20% / +30%.
            escolhas.append((uuid.UUID(valor), int(centavos * 0.8), centavos, int(centavos * 1.3)))

        async with UnitOfWork(sessao):
            await _servico(sessao, settings, providers).definir_especialidades(perfil, escolhas)

        return RedirectResponse("/profissional/agenda", status_code=303)

    # --- Simulador de recebimento ------------------------------------------

    @router.get("/simulador", name="simulador")
    async def simulador(
        request: Request,
        perfil: ProfissionalAtual,
        sessao: DbSession,
        settings: Config,
        valor: str = "150,00",
    ) -> Response:
        """Cumpre o requisito do mapa mental: mostrar ao profissional a comissão
        e os custos de cartão e impostos **antes** de ele definir o preço."""
        parametros = ParametrosService(sessao, settings)
        try:
            bruto = reais_para_centavos(valor)
        except Exception:
            bruto = 15000

        simulacao = simular_todos_metodos(
            bruto,
            percentual_comissao=await parametros.comissao_percentual(),
            tabela=TabelaTaxas(),
        )
        return responder(
            request,
            templates,
            template_completo="profissional/simulador.html",
            template_parcial="partials/simulador_resultado.html",
            contexto={
                "titulo": "Quanto você recebe",
                "valor": valor,
                "bruto": bruto,
                "simulacao": simulacao,
                "metodos": list(MetodoPagamento),
            },
        )

    # --- Agenda -------------------------------------------------------------

    @router.get("/agenda", name="agenda_profissional")
    async def ver_agenda(
        request: Request, perfil: ProfissionalAtual, sessao: DbSession
    ) -> Response:
        disponibilidades = await DisponibilidadeService(sessao).listar(perfil.usuario_id)
        por_dia: dict[int, list[object]] = {d: [] for d in range(7)}
        for disp in disponibilidades:
            por_dia[disp.dia_semana].append(disp)

        return responder(
            request,
            templates,
            template_completo="profissional/agenda.html",
            template_parcial="partials/grade_agenda.html",
            contexto={"titulo": "Sua agenda", "perfil": perfil, "por_dia": por_dia},
        )

    @router.post("/agenda")
    async def adicionar_disponibilidade(
        request: Request,
        perfil: ProfissionalAtual,
        sessao: DbSession,
        dia_semana: Annotated[int, Form()],
        inicio: Annotated[str, Form()],
        fim: Annotated[str, Form()],
    ) -> Response:
        async with UnitOfWork(sessao):
            await DisponibilidadeService(sessao).adicionar(
                perfil.usuario_id,
                dia_semana=dia_semana,
                inicio_min=parse_hhmm(inicio),
                fim_min=parse_hhmm(fim),
            )
        return await ver_agenda(request, perfil, sessao)

    @router.delete("/agenda/{disponibilidade_id}")
    async def remover_disponibilidade(
        request: Request,
        perfil: ProfissionalAtual,
        sessao: DbSession,
        disponibilidade_id: uuid.UUID,
    ) -> Response:
        async with UnitOfWork(sessao):
            await DisponibilidadeService(sessao).remover(perfil.usuario_id, disponibilidade_id)
        return await ver_agenda(request, perfil, sessao)

    return router
