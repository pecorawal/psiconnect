"""Fábrica da aplicação.

``create_app()`` em vez de um ``app`` global montado no import: é o que permite
que os testes construam instâncias isoladas, com settings e providers próprios.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import saude
from app.api.webhooks import mercadopago as webhook_mercadopago
from app.core.config import RAIZ_PROJETO, Settings, get_settings
from app.core.deps import obter_usuario_opcional
from app.core.erros import ErroDominio
from app.core.logging import configurar_logging, get_logger
from app.core.seguranca import gerar_token_opaco
from app.core.sessao_web import COOKIE_CSRF, DURACAO_SESSAO, csrf_valido
from app.core.templating import criar_templates, eh_htmx
from app.db.sessao import fechar_engine, init_engine
from app.providers.registry import montar_providers
from app.web.rotas import (
    admin,
    auth,
    avaliacao,
    dev,
    midia,
    paciente,
    painel,
    profissional,
    publico,
    responsavel,
)
from app.web.rotas import sessao as rotas_sessao

log = get_logger(__name__)

#: Nome técnico do campo -> rótulo que o usuário reconhece do formulário.
ROTULOS_CAMPOS = {
    "email": "e-mail",
    "senha": "senha",
    "nome_completo": "nome completo",
    "nome_exibicao": "nome de exibição",
    "data_nascimento": "data de nascimento",
    "registro_numero": "número do registro",
    "registro_uf": "UF do registro",
    "descricao": "descrição",
    "dia_semana": "dia da semana",
    "inicio": "horário inicial",
    "fim": "horário final",
}


def _rotular(campo: str) -> str:
    return ROTULOS_CAMPOS.get(campo, campo.replace("_", " "))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    # Falha cedo: melhor não subir do que subir inseguro.
    settings.validar_para_producao()
    settings.validar_armazenamento()
    settings.validar_criptografia()
    init_engine(settings)
    app.state.providers = montar_providers(settings)
    log.info(
        "app.iniciada",
        ambiente=settings.app_env.value,
        providers={
            "pagamento": settings.pagamento_provider,
            "video": settings.video_provider,
            "notificacao": settings.notificacao_provider,
        },
    )
    yield
    await fechar_engine()
    log.info("app.encerrada")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configurar_logging(settings)

    app = FastAPI(
        title=settings.app_nome,
        description="Plataforma de conexão entre psicólogos e pacientes",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.eh_producao else None,
        redoc_url=None,
        openapi_url="/openapi.json" if not settings.eh_producao else None,
    )
    app.state.settings = settings
    templates = criar_templates(settings)
    app.state.templates = templates

    # --- Middlewares -------------------------------------------------------
    @app.middleware("http")
    async def contexto_de_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Correlaciona todos os logs de uma requisição por um request_id."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            metodo=request.method,
            caminho=request.url.path,
        )
        resposta = await call_next(request)
        resposta.headers["X-Request-ID"] = request_id
        return resposta

    @app.middleware("http")
    async def protecao_csrf(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Double-submit: cookie legível + header/campo que precisa combinar.

        `SameSite=Lax` já barra a maior parte dos ataques, mas não cobre
        navegador antigo nem subdomínio comprometido. O custo aqui é uma
        comparação de string.
        """
        token = request.cookies.get(COOKIE_CSRF)
        novo = not token
        if novo:
            token = gerar_token_opaco()
        request.state.csrf_token = token

        if not await csrf_valido(request):
            log.warning("csrf.rejeitado", caminho=request.url.path)
            return JSONResponse(
                status_code=403,
                content={
                    "codigo": "csrf_invalido",
                    "mensagem": "Sua sessão expirou. Recarregue a página e tente de novo.",
                },
            )

        resposta = await call_next(request)
        if novo and token:
            resposta.set_cookie(
                COOKIE_CSRF,
                token,
                max_age=int(DURACAO_SESSAO.total_seconds()),
                httponly=False,  # o JS precisa lê-lo para devolver no header
                secure=settings.cookies_seguros,
                samesite="lax",
                path="/",
            )
        return resposta

    @app.middleware("http")
    async def cabecalhos_seguranca(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        resposta = await call_next(request)
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "DENY")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        resposta.headers.setdefault("Permissions-Policy", "geolocation=(), payment=(), usb=()")
        if settings.eh_producao:
            resposta.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return resposta

    # --- Tratamento de erros de domínio ------------------------------------
    @app.exception_handler(ErroDominio)
    async def tratar_erro_dominio(request: Request, exc: ErroDominio) -> Response:
        """Traduz o erro de negócio conforme quem pediu: JSON, fragmento ou página.

        A camada de serviço nunca soube nada disso -- ela só levantou a exceção.
        """
        log.info("erro_dominio", codigo=exc.codigo, campo=exc.campo)

        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=exc.status_http, content=exc.as_dict())

        if eh_htmx(request):
            return templates.TemplateResponse(
                request=request,
                name="partials/alerta.html",
                context={"mensagem": exc.mensagem_usuario, "campo": exc.campo, "tipo": "erro"},
                status_code=exc.status_http,
                headers={"HX-Retarget": "#alerta", "HX-Reswap": "innerHTML"},
            )

        return templates.TemplateResponse(
            request=request,
            name="erros/erro.html",
            context={
                "titulo": "Não foi possível continuar",
                "mensagem": exc.mensagem_usuario,
                "codigo": exc.codigo,
            },
            status_code=exc.status_http,
        )

    @app.exception_handler(RequestValidationError)
    async def tratar_erro_validacao(request: Request, exc: RequestValidationError) -> Response:
        """Erro de validação de formulário vira mensagem em português.

        Sem isto o FastAPI devolve o JSON cru do Pydantic
        (``{"detail":[{"type":"missing","loc":["body","email"], ...}]}``) --
        que numa navegação normal aparece como texto na tela.
        """
        campos = [
            str(erro["loc"][-1])
            for erro in exc.errors()
            if erro.get("loc") and erro["loc"][0] in ("body", "query", "form")
        ]
        mensagem = (
            f"Preencha corretamente: {', '.join(_rotular(c) for c in campos)}."
            if campos
            else "Alguns campos não foram preenchidos corretamente."
        )
        erro = ErroDominio(mensagem, campo=campos[0] if campos else None)
        return await tratar_erro_dominio(request, erro)

    # --- Estáticos e rotas -------------------------------------------------
    app.mount(
        "/static",
        StaticFiles(directory=str(RAIZ_PROJETO / "app" / "static")),
        name="static",
    )
    app.include_router(saude.router)
    # Webhook fica FORA de `contexto_usuario`: quem chama é o Mercado Pago, sem
    # cookie de sessão. A autenticação é a assinatura HMAC.
    app.include_router(webhook_mercadopago.router)

    # Resolver o usuário em TODA rota HTML, e não só nas que precisam dele:
    # o cabeçalho é renderizado em toda página, e sem isto ele mostraria
    # "Entrar / Começar" para quem já está logado em qualquer tela que não
    # declarasse a dependência. É barato (as rotas autenticadas já fazem essa
    # consulta, e o FastAPI reaproveita a dependência dentro da requisição) e
    # tira do desenvolvedor a chance de esquecer.
    # /healthz fica de fora de propósito: liveness não toca no banco.
    contexto_usuario = [Depends(obter_usuario_opcional)]

    app.include_router(publico.montar(templates), dependencies=contexto_usuario)
    app.include_router(auth.montar(templates), dependencies=contexto_usuario)
    app.include_router(profissional.montar(templates), dependencies=contexto_usuario)
    app.include_router(paciente.montar(templates), dependencies=contexto_usuario)
    app.include_router(painel.montar(templates), dependencies=contexto_usuario)
    app.include_router(rotas_sessao.montar(templates), dependencies=contexto_usuario)
    app.include_router(avaliacao.montar(templates), dependencies=contexto_usuario)
    app.include_router(responsavel.montar(templates), dependencies=contexto_usuario)
    app.include_router(admin.montar(templates), dependencies=contexto_usuario)
    # /midia por último: tem rota curinga /midia/{token} que capturaria
    # /midia/foto/... se viesse antes das específicas.
    app.include_router(midia.montar(templates), dependencies=contexto_usuario)

    # Rotas de dev nem sequer existem fora de dev/teste: são atalhos que
    # substituem worker, webhook e aprovação de cadastro.
    if settings.permite_rotas_dev:
        app.include_router(dev.montar(templates), dependencies=contexto_usuario)

    return app


app = create_app()
