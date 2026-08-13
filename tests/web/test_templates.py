"""Sanidade do HTML gerado.

Existe por causa de um bug real: com ``trim_blocks`` ligado, o Jinja remove a
quebra de linha depois de ``{% endif %}``, e dois atributos condicionais em
linhas seguidas saíam colados — ``requiredautocomplete="username"``,
``checkeddisabled``. O servidor continuava validando, então nenhum teste de
rota falhava; só o navegador deixava de aplicar `required`, `checked` e
`disabled`.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.web

RAIZ_TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"

#: Atributos booleanos de HTML: se colarem com o seguinte, somem em silêncio.
BOOLEANOS = ("checked", "disabled", "required", "readonly", "selected", "multiple")


class TestAtributosCondicionais:
    def test_atributo_booleano_condicional_termina_com_espaco(self) -> None:
        padrao = re.compile(r"\{%\s*if [^%]*%\}(" + "|".join(BOOLEANOS) + r")\{%\s*endif\s*%\}")
        problemas: list[str] = []
        for arquivo in sorted(RAIZ_TEMPLATES.rglob("*.html")):
            texto = arquivo.read_text(encoding="utf-8")
            for m in padrao.finditer(texto):
                linha = texto[: m.start()].count("\n") + 1
                problemas.append(f"{arquivo.relative_to(RAIZ_TEMPLATES)}:{linha}")

        assert not problemas, (
            "Atributo condicional sem espaço antes de {% endif %}: com "
            "trim_blocks, cola no atributo seguinte e ambos somem.\n" + "\n".join(problemas)
        )


class TestHtmlRenderizado:
    async def _html(self, app: FastAPI, caminho: str) -> str:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://teste") as c:
            r = await c.get(caminho)
        assert r.status_code == 200, caminho
        return r.text

    @pytest.mark.parametrize(
        "caminho", ["/", "/entrar", "/cadastro/paciente", "/cadastro/profissional"]
    )
    async def test_nenhum_atributo_grudado(self, app: FastAPI, caminho: str) -> None:
        html = await self._html(app, caminho)
        for booleano in BOOLEANOS:
            # `required` seguido direto de letra = atributo colado no próximo.
            grudado = re.search(rf"\b{booleano}[a-z]+=", html)
            assert grudado is None, (
                f"{caminho}: atributo grudado — {grudado.group(0) if grudado else ''}"
            )

    async def test_campos_obrigatorios_tem_required(self, app: FastAPI) -> None:
        """O `required` do navegador é a primeira barreira; sem ele o usuário
        só descobre o erro depois de enviar."""
        sopa = BeautifulSoup(await self._html(app, "/entrar"), "html.parser")
        email = sopa.select_one("#email")
        senha = sopa.select_one("#senha")
        assert email is not None and email.has_attr("required")
        assert senha is not None and senha.has_attr("required")

    async def test_autocomplete_preservado(self, app: FastAPI) -> None:
        sopa = BeautifulSoup(await self._html(app, "/entrar"), "html.parser")
        email = sopa.select_one("#email")
        assert email is not None
        assert email.get("autocomplete") == "username"
