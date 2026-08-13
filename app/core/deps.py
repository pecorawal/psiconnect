"""Injeção de dependência.

Tipos anotados em vez de ``Depends(...)`` repetido em cada assinatura: a rota
declara ``usuario: UsuarioAtual`` e pronto.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.erros import NaoAutenticado, NaoAutorizado
from app.core.permissoes import autorizar, eh_staff
from app.core.sessao_web import obter_token_sessao
from app.core.templating import UsuarioContexto
from app.db.sessao import get_db
from app.models import Papel, PerfilPaciente, PerfilProfissional, Usuario
from app.providers.registry import Providers
from app.services.auth_service import AuthService, ContextoRequisicao

DbSession = Annotated[AsyncSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


def obter_providers(request: Request) -> Providers:
    return request.app.state.providers  # type: ignore[no-any-return]


ProvidersAtuais = Annotated[Providers, Depends(obter_providers)]


def obter_contexto(request: Request) -> ContextoRequisicao:
    """IP e user-agent para a trilha de auditoria.

    Atrás de proxy, o IP real vem em ``X-Forwarded-For``. Confiar nesse header
    exige que o proxy o sobrescreva -- se ele apenas repassar, o cliente pode
    forjar o próprio IP no log.
    """
    encaminhado = request.headers.get("X-Forwarded-For", "")
    ip = encaminhado.split(",")[0].strip() if encaminhado else None
    if not ip and request.client:
        ip = request.client.host
    return ContextoRequisicao(ip=ip, user_agent=request.headers.get("User-Agent"))


Contexto = Annotated[ContextoRequisicao, Depends(obter_contexto)]


async def obter_usuario_opcional(
    request: Request, sessao: DbSession, settings: Config
) -> Usuario | None:
    """Usuário logado, ou ``None``. Para páginas públicas que mudam se há login."""
    token = obter_token_sessao(request, settings)
    if not token:
        return None
    usuario = await AuthService(sessao).resolver_sessao(token)
    request.state.usuario = usuario
    # Snapshot imutável para os templates: o objeto ORM desanexa quando a sessão
    # fecha ou faz rollback, e a página de erro (que renderiza DEPOIS do
    # rollback) estouraria DetachedInstanceError.
    request.state.usuario_ctx = (
        UsuarioContexto(
            id=usuario.id,
            nome_completo=usuario.nome_completo,
            email=usuario.email,
            papel=usuario.papel.value,
        )
        if usuario is not None
        else None
    )
    return usuario


UsuarioOpcional = Annotated[Usuario | None, Depends(obter_usuario_opcional)]


async def obter_usuario_atual(usuario: UsuarioOpcional) -> Usuario:
    if usuario is None:
        raise NaoAutenticado()
    return usuario


UsuarioAtual = Annotated[Usuario, Depends(obter_usuario_atual)]


def requer_papel(*papeis: Papel):  # type: ignore[no-untyped-def]
    """Dependência que exige um dos papéis informados."""

    async def _verificar(usuario: UsuarioAtual) -> Usuario:
        if usuario.papel not in papeis:
            raise NaoAutorizado()
        return usuario

    return _verificar


async def obter_perfil_profissional(
    usuario: Annotated[Usuario, Depends(requer_papel(Papel.PROFISSIONAL))],
    sessao: DbSession,
) -> PerfilProfissional:
    # Consulta explícita em vez de `usuario.perfil_profissional`: o
    # relacionamento é carregado junto com o usuário e fica em cache no identity
    # map. Se o perfil foi criado depois disso na MESMA sessão, o atributo ainda
    # vale None -- e o profissional recém-cadastrado levaria 403 no próprio
    # passo seguinte do onboarding.
    perfil = await sessao.get(PerfilProfissional, usuario.id)
    if perfil is None:
        raise NaoAutorizado("Complete seu cadastro profissional para continuar.")
    return perfil


ProfissionalAtual = Annotated[PerfilProfissional, Depends(obter_perfil_profissional)]


async def obter_perfil_paciente(
    usuario: Annotated[Usuario, Depends(requer_papel(Papel.PACIENTE))],
    sessao: DbSession,
) -> PerfilPaciente:
    perfil = await sessao.get(PerfilPaciente, usuario.id)
    if perfil is None:
        raise NaoAutorizado("Complete seu cadastro para continuar.")
    return perfil


PacienteAtual = Annotated[PerfilPaciente, Depends(obter_perfil_paciente)]
AdminAtual = Annotated[Usuario, Depends(requer_papel(Papel.ADMIN))]


async def requer_permissao(request: Request, usuario: UsuarioAtual) -> Usuario:
    """Aplica a política administrativa central (app/core/permissoes.py).

    Montada nas rotas ``/admin``. A política vem do prefixo da rota e do método
    HTTP, não de um decorador por endpoint — assim dá para auditar tudo lendo
    um arquivo só.
    """
    if not eh_staff(usuario):
        raise NaoAutorizado()
    autorizar(usuario, request)
    return usuario


StaffAutorizado = Annotated[Usuario, Depends(requer_permissao)]
