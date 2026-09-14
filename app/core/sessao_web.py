"""Cookie de sessão e proteção CSRF.

Desenho em ADR 0002. Resumo:

* cookie ``psiconnect_sessao``, ``HttpOnly``, com token opaco assinado; no banco
  só o SHA-256;
* CSRF por *double-submit*: um segundo cookie, este **legível pelo JS** de
  propósito, cujo valor o HTMX devolve no header ``X-CSRF-Token``. O cookie de
  sessão continua invisível ao JS, que é o que importa contra XSS.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import Settings
from app.core.seguranca import comparar_seguro, gerar_token_opaco

COOKIE_SESSAO = "psiconnect_sessao"
COOKIE_CSRF = "psiconnect_csrf"
HEADER_CSRF = "X-CSRF-Token"
CAMPO_CSRF = "csrf_token"

DURACAO_SESSAO = timedelta(days=14)
#: Métodos que alteram estado e por isso exigem CSRF.
METODOS_INSEGUROS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
#: Rotas fora do CSRF: webhooks se autenticam por assinatura HMAC do provedor.
PREFIXOS_SEM_CSRF = ("/api/webhooks/",)


def _serializador(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.app_secret_key, salt="psiconnect.sessao")


def assinar_token(settings: Settings, token: str) -> str:
    return str(_serializador(settings).dumps(token))


def ler_token_assinado(settings: Settings, valor: str) -> str | None:
    """Devolve o token, ou ``None`` se a assinatura for inválida ou vencida."""
    try:
        lido = _serializador(settings).loads(valor, max_age=int(DURACAO_SESSAO.total_seconds()))
    except (BadSignature, SignatureExpired):
        return None
    return str(lido) if isinstance(lido, str) else None


def definir_cookie_sessao(resposta: Response, settings: Settings, token: str) -> None:
    resposta.set_cookie(
        COOKIE_SESSAO,
        assinar_token(settings, token),
        max_age=int(DURACAO_SESSAO.total_seconds()),
        httponly=True,  # invisível ao JS: é a defesa contra XSS
        secure=settings.cookies_seguros,
        samesite="lax",
        path="/",
    )


def limpar_cookie_sessao(resposta: Response) -> None:
    resposta.delete_cookie(COOKIE_SESSAO, path="/")
    resposta.delete_cookie(COOKIE_CSRF, path="/")


def obter_token_sessao(request: Request, settings: Settings) -> str | None:
    bruto = request.cookies.get(COOKIE_SESSAO)
    if bruto:
        return ler_token_assinado(settings, bruto)
    # A API (app nativo futuro) manda o mesmo tipo de token como Bearer, contra a
    # mesma tabela SessaoLogin. Um único conceito de sessão, uma única revogação.
    autorizacao = request.headers.get("Authorization", "")
    if autorizacao.startswith("Bearer "):
        return autorizacao.removeprefix("Bearer ").strip() or None
    return None


# --- CSRF ------------------------------------------------------------------


def garantir_cookie_csrf(request: Request, resposta: Response, settings: Settings) -> str:
    """Devolve o token CSRF da requisição, criando o cookie se não houver."""
    token = request.cookies.get(COOKIE_CSRF)
    if not token:
        token = gerar_token_opaco()
        resposta.set_cookie(
            COOKIE_CSRF,
            token,
            max_age=int(DURACAO_SESSAO.total_seconds()),
            httponly=False,  # precisa ser legível: o JS o copia para o header
            secure=settings.cookies_seguros,
            samesite="lax",
            path="/",
        )
    return token


async def csrf_valido(request: Request) -> bool:
    """Compara o cookie com o header (ou o campo do formulário).

    ``SameSite=Lax`` já barra a maior parte dos ataques, mas não todos os
    navegadores em uso, e não protege contra subdomínio comprometido. O
    double-submit é barato e fecha a lacuna.
    """
    if request.method not in METODOS_INSEGUROS:
        return True
    if request.url.path.startswith(PREFIXOS_SEM_CSRF):
        return True

    do_cookie = request.cookies.get(COOKIE_CSRF)
    if not do_cookie:
        return False

    enviado = request.headers.get(HEADER_CSRF)
    if not enviado:
        # Formulário sem HTMX (ou com JS desabilitado) manda no corpo.
        try:
            formulario = await request.form()
        except Exception:
            return False
        valor = formulario.get(CAMPO_CSRF)
        enviado = valor if isinstance(valor, str) else None

    return bool(enviado) and comparar_seguro(do_cookie, enviado or "")
