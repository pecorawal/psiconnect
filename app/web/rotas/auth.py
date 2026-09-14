"""Rotas de cadastro, login e logout."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.deps import Config, Contexto, DbSession, UsuarioOpcional
from app.core.erros import ErroDominio
from app.core.sessao_web import (
    definir_cookie_sessao,
    limpar_cookie_sessao,
    obter_token_sessao,
)
from app.core.templating import responder
from app.db.sessao import UnitOfWork
from app.models import Papel, PerfilPaciente, StatusPaciente, TipoTermo
from app.services.auth_service import AuthService, DadosCadastro
from app.services.termos_service import TermosService

router = APIRouter(tags=["auth"])

#: Para onde cada papel vai depois de entrar, quando não há destino pedido.
DESTINO_POR_PAPEL = {
    Papel.PACIENTE: "/paciente/sintomas",
    Papel.PROFISSIONAL: "/profissional/perfil",
    Papel.ADMIN: "/admin",
}


def destino_seguro(proximo: str | None, padrao: str) -> str:
    """Valida o ``?proximo=`` antes de mandar alguém para lá.

    Sem isto, ``/entrar?proximo=https://site-falso.test`` vira rampa de
    phishing: o link é do nosso domínio, a pessoa entra de verdade e é jogada
    para fora logo depois de digitar a senha. Só caminho do próprio site passa.
    """
    if not proximo or not proximo.startswith("/"):
        return padrao
    # "//outro.test" e "/\outro.test" são protocol-relative: saem do domínio.
    if proximo.startswith(("//", "/\\")):
        return padrao
    if "\r" in proximo or "\n" in proximo:
        return padrao
    if proximo.split("?", 1)[0] == "/entrar":
        return padrao  # não faz sentido voltar para o próprio login
    return proximo


def montar(templates: Jinja2Templates) -> APIRouter:
    @router.get("/entrar", name="entrar")
    async def form_login(
        request: Request,
        usuario: UsuarioOpcional,
        expirou: bool = False,
        proximo: str = "",
    ) -> Response:
        if usuario is not None:
            return RedirectResponse(
                destino_seguro(proximo, DESTINO_POR_PAPEL[usuario.papel]), status_code=303
            )
        return responder(
            request,
            templates,
            template_completo="auth/entrar.html",
            contexto={
                "titulo": "Entrar",
                "expirou": expirou,
                # Segue no formulário para sobreviver ao POST.
                "proximo": destino_seguro(proximo, ""),
            },
        )

    @router.post("/entrar")
    async def processar_login(
        request: Request,
        sessao: DbSession,
        settings: Config,
        contexto: Contexto,
        email: Annotated[str, Form()],
        senha: Annotated[str, Form()],
        proximo: Annotated[str, Form()] = "",
    ) -> Response:
        async with UnitOfWork(sessao):
            usuario, token = await AuthService(sessao).autenticar(email, senha, contexto)

        # Quem foi barrado a caminho de uma página volta para ela, não para a
        # porta de entrada do papel.
        destino = destino_seguro(proximo, DESTINO_POR_PAPEL[usuario.papel])
        resposta = RedirectResponse(destino, status_code=303)
        definir_cookie_sessao(resposta, settings, token)
        return resposta

    @router.post("/sair", name="sair")
    async def logout(request: Request, sessao: DbSession, settings: Config) -> Response:
        token = obter_token_sessao(request, settings)
        if token:
            async with UnitOfWork(sessao):
                await AuthService(sessao).encerrar_sessao(token)
        resposta = RedirectResponse("/", status_code=303)
        limpar_cookie_sessao(resposta)
        return resposta

    # --- Cadastro ----------------------------------------------------------

    @router.get("/cadastro/paciente", name="cadastro_paciente")
    async def form_cadastro_paciente(request: Request, sessao: DbSession) -> Response:
        termos = await TermosService(sessao).vigentes(
            TipoTermo.TERMOS_USO, TipoTermo.POLITICA_PRIVACIDADE, TipoTermo.CONSENT_DADOS_SAUDE
        )
        return responder(
            request,
            templates,
            template_completo="auth/cadastro_paciente.html",
            contexto={"titulo": "Criar conta", "termos": termos},
        )

    @router.post("/cadastro/paciente")
    async def processar_cadastro_paciente(
        request: Request,
        sessao: DbSession,
        settings: Config,
        contexto: Contexto,
        nome_completo: Annotated[str, Form()],
        email: Annotated[str, Form()],
        senha: Annotated[str, Form()],
        data_nascimento: Annotated[str, Form()],
        telefone: Annotated[str, Form()] = "",
        aceite: Annotated[str, Form()] = "",
    ) -> Response:
        if not aceite:
            raise ErroDominio(
                "É preciso aceitar os termos e o uso dos seus dados de saúde para continuar.",
                campo="aceite",
            )

        async with UnitOfWork(sessao):
            usuario = await AuthService(sessao).cadastrar(
                DadosCadastro(
                    nome_completo=nome_completo,
                    email=email,
                    senha=senha,
                    telefone=telefone or None,
                    data_nascimento=_parse_data(data_nascimento),
                ),
                Papel.PACIENTE,
                contexto,
            )
            await TermosService(sessao).registrar_aceite(
                usuario,
                (
                    TipoTermo.TERMOS_USO,
                    TipoTermo.POLITICA_PRIVACIDADE,
                    TipoTermo.CONSENT_DADOS_SAUDE,
                ),
                ip=contexto.ip,
                user_agent=contexto.user_agent,
            )
            token = await AuthService(sessao).criar_sessao(usuario, contexto)

        # Menor de 18 vai primeiro informar quem responde por ele; sem a
        # autorização, o fluxo de agendamento não abre.
        # Consulta explícita: `usuario.perfil_paciente` acabou de ser criado
        # nesta sessão e o relacionamento ainda não foi carregado — tocá-lo
        # dispararia lazy load fora do contexto async.
        perfil = await sessao.get(PerfilPaciente, usuario.id)
        destino = (
            "/cadastro/responsavel"
            if perfil is not None and perfil.status is StatusPaciente.PENDENTE_RESPONSAVEL
            else "/paciente/sintomas"
        )
        resposta = RedirectResponse(destino, status_code=303)
        definir_cookie_sessao(resposta, settings, token)
        return resposta

    @router.get("/cadastro/profissional", name="cadastro_profissional")
    async def form_cadastro_profissional(request: Request, sessao: DbSession) -> Response:
        termos = await TermosService(sessao).vigentes(
            TipoTermo.TERMOS_USO,
            TipoTermo.POLITICA_PRIVACIDADE,
            TipoTermo.CONTRATO_PROFISSIONAL,
        )
        return responder(
            request,
            templates,
            template_completo="auth/cadastro_profissional.html",
            contexto={"titulo": "Cadastro de profissional", "termos": termos},
        )

    @router.post("/cadastro/profissional")
    async def processar_cadastro_profissional(
        request: Request,
        sessao: DbSession,
        settings: Config,
        contexto: Contexto,
        nome_completo: Annotated[str, Form()],
        email: Annotated[str, Form()],
        senha: Annotated[str, Form()],
        telefone: Annotated[str, Form()] = "",
        aceite: Annotated[str, Form()] = "",
    ) -> Response:
        if not aceite:
            raise ErroDominio(
                "É preciso ler e aceitar o contrato para se cadastrar.", campo="aceite"
            )

        async with UnitOfWork(sessao):
            usuario = await AuthService(sessao).cadastrar(
                DadosCadastro(
                    nome_completo=nome_completo,
                    email=email,
                    senha=senha,
                    telefone=telefone or None,
                ),
                Papel.PROFISSIONAL,
                contexto,
            )
            await TermosService(sessao).registrar_aceite(
                usuario,
                (
                    TipoTermo.TERMOS_USO,
                    TipoTermo.POLITICA_PRIVACIDADE,
                    TipoTermo.CONTRATO_PROFISSIONAL,
                ),
                ip=contexto.ip,
                user_agent=contexto.user_agent,
            )
            token = await AuthService(sessao).criar_sessao(usuario, contexto)

        resposta = RedirectResponse("/profissional/perfil", status_code=303)
        definir_cookie_sessao(resposta, settings, token)
        return resposta

    return router


def _parse_data(texto: str) -> date | None:
    """Aceita ``dd/mm/aaaa`` (digitado) e ``aaaa-mm-dd`` (input type=date)."""
    texto = texto.strip()
    if not texto:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return date.fromisoformat(texto) if formato == "%Y-%m-%d" else _br(texto)
        except ValueError:
            continue
    raise ErroDominio("Data de nascimento inválida.", campo="data_nascimento")


def _br(texto: str) -> date:
    dia, mes, ano = (int(p) for p in texto.split("/"))
    return date(ano, mes, dia)
