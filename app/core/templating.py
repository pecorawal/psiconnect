"""Renderização Jinja2 e integração com HTMX.

O padrão do projeto: **a mesma rota serve a página inteira e o fragmento**. Numa
navegação normal o navegador pede tudo; num ``hx-get`` o HTMX manda o header
``HX-Request`` e devolvemos só o pedaço que muda. Isso evita a duplicação
"rota HTML + rota JSON + JS que costura os dois" de uma SPA, que é justamente o
que a escolha de stack (ADR 0001) quis eliminar.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request, Response
from fastapi.templating import Jinja2Templates

from app.core.config import RAIZ_PROJETO, Settings
from app.core.dinheiro import formatar_brl
from app.core.tempo import TZ_BR, formatar_hhmm, para_local

DIRETORIO_TEMPLATES = RAIZ_PROJETO / "app" / "templates"

DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
DIAS_SEMANA_CURTO = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
MESES = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def _tz(valor: str | ZoneInfo | None) -> ZoneInfo:
    if valor is None:
        return TZ_BR
    return valor if isinstance(valor, ZoneInfo) else ZoneInfo(valor)


def filtro_data_br(dt: datetime | date, tz: str | ZoneInfo | None = None) -> str:
    """``2026-08-12`` -> ``'12/08/2026'``."""
    if isinstance(dt, datetime):
        dt = para_local(dt, _tz(tz)).date()
    return dt.strftime("%d/%m/%Y")


def filtro_hora_br(dt: datetime, tz: str | ZoneInfo | None = None) -> str:
    """Hora local do usuário, ``'14:30'``."""
    return para_local(dt, _tz(tz)).strftime("%H:%M")


def filtro_data_hora_br(dt: datetime, tz: str | ZoneInfo | None = None) -> str:
    """``'12/08/2026 às 14:30'``."""
    local = para_local(dt, _tz(tz))
    return f"{local.strftime('%d/%m/%Y')} às {local.strftime('%H:%M')}"


def filtro_data_extenso(dt: datetime | date, tz: str | ZoneInfo | None = None) -> str:
    """``'terça-feira, 12 de agosto'`` -- para confirmações de agendamento."""
    if isinstance(dt, datetime):
        dt = para_local(dt, _tz(tz)).date()
    sufixo = "-feira" if dt.weekday() < 5 else ""
    return f"{DIAS_SEMANA[dt.weekday()]}{sufixo}, {dt.day} de {MESES[dt.month - 1]}"


def contexto_padrao(request: Request) -> dict[str, Any]:
    """Injetado em **todo** template renderizado.

    Evita que cada rota tenha de lembrar de passar ``usuario`` e ``csrf_token``
    -- esquecer o segundo faria o formulário falhar só em produção.
    """
    return {
        "usuario": getattr(request.state, "usuario", None),
        "csrf_token": getattr(request.state, "csrf_token", ""),
    }


def criar_templates(settings: Settings) -> Jinja2Templates:
    templates = Jinja2Templates(
        directory=str(DIRETORIO_TEMPLATES), context_processors=[contexto_padrao]
    )
    env = templates.env
    env.filters["brl"] = formatar_brl
    env.filters["data_br"] = filtro_data_br
    env.filters["hora_br"] = filtro_hora_br
    env.filters["data_hora_br"] = filtro_data_hora_br
    env.filters["data_extenso"] = filtro_data_extenso
    env.filters["hhmm"] = formatar_hhmm
    env.globals["app_nome"] = settings.app_nome
    env.globals["app_env"] = settings.app_env.value
    env.globals["eh_dev"] = settings.eh_dev
    env.globals["dias_semana"] = DIAS_SEMANA
    env.globals["dias_semana_curto"] = DIAS_SEMANA_CURTO
    # Espaços em branco atrapalham fragmentos HTMX inseridos inline.
    env.trim_blocks = True
    env.lstrip_blocks = True
    return templates


def eh_htmx(request: Request) -> bool:
    """True quando a requisição veio de um ``hx-*``, e não de uma navegação."""
    return request.headers.get("HX-Request") == "true"


def eh_boosted(request: Request) -> bool:
    """True em ``hx-boost``: é HTMX, mas o alvo é o ``<body>`` inteiro."""
    return request.headers.get("HX-Boosted") == "true"


def responder(
    request: Request,
    templates: Jinja2Templates,
    *,
    template_completo: str,
    template_parcial: str | None = None,
    contexto: dict[str, Any] | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> Response:
    """Escolhe entre a página inteira e o fragmento conforme a origem do pedido.

    Sem ``template_parcial``, devolve sempre a página completa -- útil para
    telas que não têm swap parcial.
    """
    ctx = dict(contexto or {})
    usar_parcial = template_parcial is not None and eh_htmx(request) and not eh_boosted(request)
    nome = template_parcial if usar_parcial else template_completo
    assert nome is not None
    return templates.TemplateResponse(
        request=request,
        name=nome,
        context=ctx,
        status_code=status_code,
        headers=headers,
    )


def responder_erro_dominio(
    request: Request,
    templates: Jinja2Templates,
    *,
    mensagem: str,
    campo: str | None = None,
    status_code: int = 422,
) -> Response:
    """Fragmento de alerta para um erro de regra de negócio.

    ``HX-Retarget`` redireciona o swap para o container ``#alerta`` do layout,
    independentemente do ``hx-target`` que originou o pedido -- assim um erro
    inesperado nunca substitui o formulário por uma mensagem solta.
    """
    return templates.TemplateResponse(
        request=request,
        name="partials/alerta.html",
        context={"mensagem": mensagem, "campo": campo, "tipo": "erro"},
        status_code=status_code,
        headers={"HX-Retarget": "#alerta", "HX-Reswap": "innerHTML"},
    )
