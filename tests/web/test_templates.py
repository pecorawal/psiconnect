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

from app.core.sessao_web import COOKIE_CSRF

pytestmark = [pytest.mark.web, pytest.mark.db]

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

    async def test_pagina_de_erro_renderiza_com_usuario_logado(
        self, app: FastAPI, sessao, settings
    ) -> None:
        """A página de erro roda DEPOIS do rollback da sessão.

        Se o template segurasse o objeto ORM do usuário, ler qualquer atributo
        dele ali estouraria DetachedInstanceError — e o template de erro
        quebraria justamente por causa do erro. Por isso o contexto recebe um
        snapshot imutável (UsuarioContexto), não a entidade.
        """
        from tests import fabricas as f
        from tests.web.test_profissional import logar

        usuario = await f.criar_usuario(
            sessao, papel=__import__("app.models", fromlist=["Papel"]).Papel.PROFISSIONAL
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://teste") as c:
            await logar(c, app, sessao, settings, usuario.id)
            # UF inválida -> ErroDominio -> rollback -> página de erro
            r = await c.post(
                "/profissional/perfil",
                data={
                    "nome_exibicao": "Teste",
                    "conselho": "CRP",
                    "registro_numero": "111222",
                    "registro_uf": "ZZ",
                    "descricao": "x",
                },
                headers={"X-CSRF-Token": c.cookies[COOKIE_CSRF]},
            )

        assert r.status_code == 422
        assert "UF inválida" in r.text
        # O cabeçalho renderizou: o snapshot sobreviveu ao rollback.
        assert "Sair" in r.text

    async def test_autocomplete_preservado(self, app: FastAPI) -> None:
        sopa = BeautifulSoup(await self._html(app, "/entrar"), "html.parser")
        email = sopa.select_one("#email")
        assert email is not None
        assert email.get("autocomplete") == "username"


class TestCabecalho:
    """O cabeçalho aparece em toda página, inclusive nas que não precisam do
    usuário para nada. Se a resolução dependesse de cada rota lembrar de pedir,
    a home mostraria "Entrar" para quem já está logado."""

    async def _home(self, app: FastAPI, c: AsyncClient) -> str:
        r = await c.get("/")
        assert r.status_code == 200
        return r.text

    async def test_anonimo_ve_entrar(self, app: FastAPI) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://teste") as c:
            html = await self._home(app, c)
        assert ">Entrar<" in html
        assert "Administração" not in html

    async def test_paciente_ve_painel_e_nao_administracao(
        self, app: FastAPI, sessao, settings
    ) -> None:
        from tests import fabricas as f
        from tests.web.test_profissional import logar

        paciente = await f.criar_paciente(sessao)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://teste") as c:
            await logar(c, app, sessao, settings, paciente.usuario_id)
            html = await self._home(app, c)
        assert ">Painel<" in html
        assert "Administração" not in html

    async def test_admin_ve_administracao(self, app: FastAPI, sessao, settings) -> None:
        from app.models import Papel
        from tests import fabricas as f
        from tests.web.test_profissional import logar

        admin = await f.criar_usuario(sessao, papel=Papel.ADMIN)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://teste") as c:
            await logar(c, app, sessao, settings, admin.id)
            html = await self._home(app, c)
        assert "Administração" in html
