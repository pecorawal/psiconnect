"""Área administrativa.

Toda rota daqui passa por ``StaffAutorizado``, que aplica a política central de
``app/core/permissoes.py``: o prefixo define o módulo, o método HTTP define a
operação, e a ``Role`` do usuário decide. Não há decorador de permissão por
endpoint — a política inteira se lê num arquivo só.

**O verbo importa.** Como o método define a operação, editar precisa ser
PATCH/PUT e não POST: um formulário de edição enviado por POST exigiria
permissão de *criar*, e um papel com direito apenas de *editar* levaria 403.
Formulário HTML só fala GET e POST, então as edições usam ``hx-patch``.

Um endpoint novo sob ``/admin`` **precisa** entrar em ``PREFIXO_PARA_MODULO``,
senão é negado (e o log registra o esquecimento).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import Config, DbSession, StaffAutorizado
from app.core.templating import responder
from app.core.tempo import agora_utc
from app.db.sessao import UnitOfWork
from app.models import (
    MODULOS_SISTEMA,
    OPERACOES_CRUD,
    Papel,
    PerfilProfissional,
    StatusCadastro,
)
from app.services.parametros_service import ParametrosService
from app.services.usuarios_service import DadosUsuarioAdmin, UsuariosService

router = APIRouter(prefix="/admin", tags=["admin"])

#: Rótulos legíveis para os módulos, usados nas telas.
ROTULOS_MODULO = {
    "usuarios": "Contas e papéis",
    "profissionais": "Profissionais",
    "pacientes": "Pacientes",
    "agendamentos": "Agendamentos",
    "financeiro": "Financeiro",
    "taxonomias": "Especialidades e sintomas",
    "configuracoes": "Parâmetros do sistema",
    "termos": "Termos e políticas",
    "auditoria": "Auditoria",
    "documentos": "Documentos de identificação",
}

ROTULOS_OPERACAO = {
    "create": "criar",
    "read": "ver",
    "update": "editar",
    "delete": "excluir",
}


def montar(templates: Jinja2Templates) -> APIRouter:
    # --- Contas -------------------------------------------------------------

    @router.get("/usuarios", name="admin_usuarios")
    async def listar_usuarios(
        request: Request,
        staff: StaffAutorizado,
        sessao: DbSession,
        busca: str = "",
        papel: str = "",
    ) -> Response:
        servico = UsuariosService(sessao)
        return responder(
            request,
            templates,
            template_completo="admin/usuarios.html",
            template_parcial="partials/tabela_usuarios.html",
            contexto={
                "titulo": "Contas",
                "usuarios": await servico.listar_usuarios(
                    papel=Papel(papel) if papel else None, busca=busca
                ),
                "papeis": await servico.listar_papeis(),
                "papeis_conta": list(Papel),
                "busca": busca,
                "filtro_papel": papel,
                "pode_editar": staff.permite("usuarios", "update"),
            },
        )

    @router.post("/usuarios", name="admin_criar_usuario")
    async def criar_usuario(
        request: Request,
        staff: StaffAutorizado,
        sessao: DbSession,
        nome_completo: Annotated[str, Form()],
        email: Annotated[str, Form()],
        senha: Annotated[str, Form()],
        papel: Annotated[str, Form()],
        role_id: Annotated[str, Form()] = "",
    ) -> Response:
        async with UnitOfWork(sessao):
            await UsuariosService(sessao).criar_usuario(
                DadosUsuarioAdmin(
                    nome_completo=nome_completo,
                    email=email,
                    papel=Papel(papel),
                    role_id=uuid.UUID(role_id) if role_id else None,
                    senha=senha,
                )
            )
        return RedirectResponse("/admin/usuarios?criado=1", status_code=303)

    @router.patch("/usuarios/{usuario_id}", name="admin_atualizar_usuario")
    async def atualizar_usuario(
        request: Request,
        staff: StaffAutorizado,
        sessao: DbSession,
        usuario_id: uuid.UUID,
        role_id: Annotated[str, Form()] = "",
        ativo: Annotated[str, Form()] = "",
    ) -> Response:
        servico = UsuariosService(sessao)
        alvo = await servico.buscar_usuario(usuario_id)

        async with UnitOfWork(sessao):
            await servico.definir_papel_administrativo(
                alvo, uuid.UUID(role_id) if role_id else None, por=staff
            )
            await servico.definir_ativo(alvo, ativo == "1", por=staff)

        return await listar_usuarios(request, staff, sessao)

    # --- Papéis -------------------------------------------------------------

    @router.get("/papeis", name="admin_papeis")
    async def listar_papeis(
        request: Request, staff: StaffAutorizado, sessao: DbSession
    ) -> Response:
        return responder(
            request,
            templates,
            template_completo="admin/papeis.html",
            contexto={
                "titulo": "Papéis e permissões",
                "papeis": await UsuariosService(sessao).listar_papeis(),
                "modulos": MODULOS_SISTEMA,
                "operacoes": OPERACOES_CRUD,
                "rotulos_modulo": ROTULOS_MODULO,
                "rotulos_operacao": ROTULOS_OPERACAO,
                "pode_editar": staff.permite("usuarios", "update"),
            },
        )

    @router.post("/papeis", name="admin_criar_papel")
    async def criar_papel(
        request: Request,
        staff: StaffAutorizado,
        sessao: DbSession,
        nome: Annotated[str, Form()],
        descricao: Annotated[str, Form()] = "",
    ) -> Response:
        formulario = await request.form()
        async with UnitOfWork(sessao):
            await UsuariosService(sessao).criar_papel(
                nome, descricao, _permissoes_do_formulario(formulario)
            )
        return RedirectResponse("/admin/papeis?criado=1", status_code=303)

    @router.patch("/papeis/{papel_id}/permissoes", name="admin_permissoes")
    async def atualizar_permissoes(
        request: Request,
        staff: StaffAutorizado,
        sessao: DbSession,
        papel_id: uuid.UUID,
    ) -> Response:
        formulario = await request.form()
        servico = UsuariosService(sessao)
        papel = await servico.buscar_papel(papel_id)
        async with UnitOfWork(sessao):
            await servico.atualizar_permissoes(papel, _permissoes_do_formulario(formulario))
        return RedirectResponse("/admin/papeis?salvo=1", status_code=303)

    @router.delete("/papeis/{papel_id}", name="admin_excluir_papel")
    async def excluir_papel(
        request: Request, staff: StaffAutorizado, sessao: DbSession, papel_id: uuid.UUID
    ) -> Response:
        servico = UsuariosService(sessao)
        papel = await servico.buscar_papel(papel_id)
        async with UnitOfWork(sessao):
            await servico.excluir_papel(papel)
        return await listar_papeis(request, staff, sessao)

    # --- Profissionais: verificação de registro no conselho ----------------

    @router.get("/profissionais", name="admin_profissionais")
    async def listar_profissionais(
        request: Request, staff: StaffAutorizado, sessao: DbSession
    ) -> Response:
        from sqlalchemy import select

        pendentes = list(
            (
                await sessao.execute(
                    select(PerfilProfissional)
                    .where(PerfilProfissional.status_cadastro == StatusCadastro.EM_ANALISE)
                    .order_by(PerfilProfissional.criado_em)
                )
            )
            .scalars()
            .all()
        )
        return responder(
            request,
            templates,
            template_completo="admin/profissionais.html",
            contexto={
                "titulo": "Verificação de registro",
                "pendentes": pendentes,
                "pode_editar": staff.permite("profissionais", "update"),
            },
        )

    @router.patch("/profissionais/{profissional_id}", name="admin_verificar_profissional")
    async def verificar_profissional(
        request: Request,
        staff: StaffAutorizado,
        sessao: DbSession,
        profissional_id: uuid.UUID,
        decisao: Annotated[str, Form()],
    ) -> Response:
        from app.core.erros import NaoEncontrado

        perfil = await sessao.get(PerfilProfissional, profissional_id)
        if perfil is None:
            raise NaoEncontrado("Profissional não encontrado.")

        async with UnitOfWork(sessao):
            if decisao == "aprovar":
                perfil.status_cadastro = StatusCadastro.APROVADO
                perfil.registro_verificado_em = agora_utc()
                perfil.registro_verificado_por_id = staff.id
            else:
                perfil.status_cadastro = StatusCadastro.REJEITADO

        return await listar_profissionais(request, staff, sessao)

    # --- Parâmetros do sistema ---------------------------------------------

    @router.get("/parametros", name="admin_parametros")
    async def listar_parametros(
        request: Request, staff: StaffAutorizado, sessao: DbSession, settings: Config
    ) -> Response:
        from sqlalchemy import select

        from app.models import ParametroSistema

        parametros = list(
            (await sessao.execute(select(ParametroSistema).order_by(ParametroSistema.chave)))
            .scalars()
            .all()
        )
        return responder(
            request,
            templates,
            template_completo="admin/parametros.html",
            contexto={
                "titulo": "Parâmetros do sistema",
                "parametros": parametros,
                "pode_editar": staff.permite("configuracoes", "update"),
            },
        )

    @router.patch("/parametros/{chave}", name="admin_salvar_parametro")
    async def salvar_parametro(
        request: Request,
        staff: StaffAutorizado,
        sessao: DbSession,
        chave: str,
        valor: Annotated[str, Form()],
    ) -> Response:
        from app.core.erros import ErroDominio, NaoEncontrado
        from app.models import ParametroSistema

        parametro = await sessao.get(ParametroSistema, chave)
        if parametro is None:
            raise NaoEncontrado("Parâmetro não encontrado.")

        try:
            convertido: object = int(valor) if parametro.tipo == "int" else float(valor)
        except ValueError as exc:
            raise ErroDominio(
                f"“{valor}” não é um número válido para este parâmetro.", campo="valor"
            ) from exc

        async with UnitOfWork(sessao):
            parametro.valor = convertido
            parametro.atualizado_em = agora_utc()
            parametro.atualizado_por_id = staff.id

        # O cache tem TTL de 30s; sem invalidar, a mudança pareceria não ter
        # funcionado por meio minuto.
        ParametrosService.invalidar_cache()
        return RedirectResponse("/admin/parametros?salvo=1", status_code=303)

    return router


def _permissoes_do_formulario(formulario: object) -> dict[str, set[str]]:
    """Lê as caixas ``perm_<modulo>_<operacao>`` do formulário."""
    marcadas: dict[str, set[str]] = {}
    itens = getattr(formulario, "multi_items", None)
    if itens is None:
        return marcadas
    for chave, _ in itens():
        if not isinstance(chave, str) or not chave.startswith("perm_"):
            continue
        resto = chave.removeprefix("perm_")
        modulo, _, operacao = resto.rpartition("_")
        if modulo in MODULOS_SISTEMA and operacao in OPERACOES_CRUD:
            marcadas.setdefault(modulo, set()).add(operacao)
    return marcadas
