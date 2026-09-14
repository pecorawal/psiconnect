"""Fluxo do responsável legal por paciente menor de 18 (LGPD art. 14)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, Response, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import (
    Config,
    Contexto,
    DbSession,
    ProvidersAtuais,
    UsuarioAtual,
    UsuarioOpcional,
)
from app.core.erros import ErroDominio, NaoEncontrado
from app.core.templating import responder
from app.db.sessao import UnitOfWork
from app.models import (
    PerfilPaciente,
    StatusPaciente,
    TipoDocumentoResponsavel,
    TipoTermo,
)
from app.services.responsavel_service import (
    DadosMenor,
    DadosResponsavel,
    ResponsavelService,
)
from app.services.termos_service import TermosService
from app.web.rotas.auth import _parse_data

router = APIRouter(tags=["responsavel"])


def montar(templates: Jinja2Templates) -> APIRouter:
    def _servico(
        sessao: DbSession, settings: Config, providers: ProvidersAtuais
    ) -> ResponsavelService:
        return ResponsavelService(sessao, settings, providers.armazenamento)

    # --- Caminho A: o adolescente informa quem responde por ele -------------

    @router.get("/cadastro/responsavel", name="informar_responsavel")
    async def form_informar(request: Request, usuario: UsuarioAtual, sessao: DbSession) -> Response:
        paciente = await sessao.get(PerfilPaciente, usuario.id)
        if paciente is None:
            raise NaoEncontrado("Cadastro não encontrado.")

        return responder(
            request,
            templates,
            template_completo="responsavel/informar.html",
            contexto={
                "titulo": "Quem é seu responsável?",
                "paciente": paciente,
                "pendente": paciente.status is StatusPaciente.PENDENTE_RESPONSAVEL,
            },
        )

    @router.post("/cadastro/responsavel")
    async def informar(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        responsavel_nome: Annotated[str, Form()],
        responsavel_email: Annotated[str, Form()] = "",
        responsavel_telefone: Annotated[str, Form()] = "",
        parentesco: Annotated[str, Form()] = "",
    ) -> Response:
        paciente = await sessao.get(PerfilPaciente, usuario.id)
        if paciente is None:
            raise NaoEncontrado("Cadastro não encontrado.")

        async with UnitOfWork(sessao):
            await _servico(sessao, settings, providers).abrir_pendencia(
                paciente,
                DadosResponsavel(
                    nome=responsavel_nome,
                    email=responsavel_email or None,
                    telefone_e164=responsavel_telefone or None,
                    parentesco=parentesco or None,
                ),
            )
        return RedirectResponse("/cadastro/responsavel?enviado=1", status_code=303)

    # --- Página pública que o responsável abre pelo link -------------------

    @router.get("/responsavel/confirmar/{token}", name="confirmar_responsavel")
    async def form_confirmar(
        request: Request,
        usuario: UsuarioOpcional,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        token: str,
    ) -> Response:
        """Pública de propósito: o responsável pode não ter conta na plataforma.

        Quem tem o link tem acesso — por isso ele expira em 7 dias, vale uma vez
        e é substituído sempre que um novo convite é gerado.
        """
        verificacao = await _servico(sessao, settings, providers).buscar_por_token(token)
        termos = await TermosService(sessao).vigentes(TipoTermo.CONSENT_RESPONSAVEL)
        menor = await sessao.get(PerfilPaciente, verificacao.paciente_id)

        return responder(
            request,
            templates,
            template_completo="responsavel/confirmar.html",
            contexto={
                "titulo": "Autorizar atendimento",
                "verificacao": verificacao,
                "menor": menor,
                "termo": termos.get(TipoTermo.CONSENT_RESPONSAVEL),
                "token": token,
                "tipos_documento": list(TipoDocumentoResponsavel),
            },
        )

    @router.post("/responsavel/confirmar/{token}")
    async def confirmar(
        request: Request,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        contexto: Contexto,
        token: str,
        documento_tipo: Annotated[str, Form()],
        documento_numero: Annotated[str, Form()],
        documento: Annotated[UploadFile, File()],
        autorizo: Annotated[str, Form()] = "",
    ) -> Response:
        if not autorizo:
            raise ErroDominio("É preciso marcar a autorização para concluir.", campo="autorizo")
        if not documento.filename:
            raise ErroDominio("Envie a cópia do seu documento.", campo="documento")

        async with UnitOfWork(sessao):
            await _servico(sessao, settings, providers).confirmar(
                token,
                documento=await documento.read(),
                documento_tipo=TipoDocumentoResponsavel(documento_tipo),
                content_type=documento.content_type or "application/octet-stream",
                documento_numero=documento_numero,
                ip=contexto.ip,
                user_agent=contexto.user_agent,
            )
        return RedirectResponse("/responsavel/confirmado", status_code=303)

    @router.get("/responsavel/confirmado", name="responsavel_confirmado")
    async def confirmado(request: Request) -> Response:
        return responder(
            request,
            templates,
            template_completo="responsavel/confirmado.html",
            contexto={"titulo": "Autorização registrada"},
        )

    # --- Caminho B: o responsável cadastra o adolescente -------------------

    @router.get("/paciente/dependentes", name="dependentes")
    async def listar_dependentes(
        request: Request, usuario: UsuarioAtual, sessao: DbSession
    ) -> Response:
        from sqlalchemy import select

        dependentes = list(
            (
                await sessao.execute(
                    select(PerfilPaciente).where(
                        PerfilPaciente.responsavel_usuario_id == usuario.id
                    )
                )
            )
            .scalars()
            .all()
        )
        return responder(
            request,
            templates,
            template_completo="responsavel/dependentes.html",
            contexto={"titulo": "Meus dependentes", "dependentes": dependentes},
        )

    @router.post("/paciente/dependentes")
    async def cadastrar_dependente(
        request: Request,
        usuario: UsuarioAtual,
        sessao: DbSession,
        settings: Config,
        providers: ProvidersAtuais,
        contexto: Contexto,
        nome_completo: Annotated[str, Form()],
        data_nascimento: Annotated[str, Form()],
        email: Annotated[str, Form()],
        senha: Annotated[str, Form()],
        parentesco: Annotated[str, Form()] = "",
        telefone: Annotated[str, Form()] = "",
        autorizo: Annotated[str, Form()] = "",
    ) -> Response:
        if not autorizo:
            raise ErroDominio(
                "É preciso declarar que você é o responsável legal e autorizar "
                "o tratamento dos dados.",
                campo="autorizo",
            )

        nascimento: date | None = _parse_data(data_nascimento)
        if nascimento is None:
            raise ErroDominio("Informe a data de nascimento.", campo="data_nascimento")

        async with UnitOfWork(sessao):
            await _servico(sessao, settings, providers).cadastrar_menor(
                usuario,
                DadosMenor(
                    nome_completo=nome_completo,
                    data_nascimento=nascimento,
                    email=email,
                    senha=senha,
                    telefone_e164=telefone or None,
                ),
                parentesco=parentesco or None,
                ip=contexto.ip,
                user_agent=contexto.user_agent,
            )
        return RedirectResponse("/paciente/dependentes?criado=1", status_code=303)

    return router
