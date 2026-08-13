"""Rotas do profissional: perfil, especialidades, agenda e simulador."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.sessao_web import COOKIE_CSRF, COOKIE_SESSAO, assinar_token
from app.models import Papel
from app.services.auth_service import AuthService, ContextoRequisicao
from tests import fabricas as f

pytestmark = [pytest.mark.web, pytest.mark.db]


def cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://teste", follow_redirects=False
    )


async def logar(
    c: AsyncClient, app: FastAPI, sessao: AsyncSession, settings: Settings, usuario_id: object
) -> dict[str, str]:
    """Cria uma sessão de verdade para o usuário e devolve os headers CSRF.

    Vai pela AuthService em vez de forjar cookie: assim o teste exercita o mesmo
    caminho de autenticação da aplicação.
    """
    from app.models import Usuario

    usuario = await sessao.get(Usuario, usuario_id)
    assert usuario is not None
    token = await AuthService(sessao).criar_sessao(usuario, ContextoRequisicao())
    await sessao.flush()

    await c.get("/")  # recebe o cookie CSRF
    c.cookies.set(COOKIE_SESSAO, assinar_token(settings, token), domain="teste.local")
    return {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}


class TestPerfil:
    async def test_form_exige_login(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            r = await c.get("/profissional/perfil")
        assert r.status_code == 401

    async def test_paciente_nao_acessa_area_do_profissional(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        paciente = await f.criar_paciente(sessao)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, paciente.usuario_id)
            r = await c.get("/profissional/agenda")
        assert r.status_code == 403

    async def test_salva_perfil_e_avanca(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        usuario = await f.criar_usuario(sessao, papel=Papel.PROFISSIONAL)
        async with cliente(app) as c:
            headers = await logar(c, app, sessao, settings, usuario.id)
            r = await c.post(
                "/profissional/perfil",
                data={
                    "nome_exibicao": "Dra. Teste",
                    "conselho": "CRP",
                    "registro_numero": "998877",
                    "registro_uf": "SP",
                    "descricao": "Atendo adultos.",
                },
                headers=headers,
            )
        assert r.status_code == 303
        assert r.headers["location"] == "/profissional/especialidades"

    async def test_descricao_com_501_caracteres_e_recusada(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """R5 — o limite é 500. A mensagem diz quantos caracteres vieram."""
        usuario = await f.criar_usuario(sessao, papel=Papel.PROFISSIONAL)
        async with cliente(app) as c:
            headers = await logar(c, app, sessao, settings, usuario.id)
            r = await c.post(
                "/profissional/perfil",
                data={
                    "nome_exibicao": "Dra. Teste",
                    "conselho": "CRP",
                    "registro_numero": "998878",
                    "registro_uf": "SP",
                    "descricao": "x" * 501,
                },
                headers=headers,
            )
        assert r.status_code == 422
        assert "501" in r.text

    async def test_uf_invalida_e_recusada(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        usuario = await f.criar_usuario(sessao, papel=Papel.PROFISSIONAL)
        async with cliente(app) as c:
            headers = await logar(c, app, sessao, settings, usuario.id)
            r = await c.post(
                "/profissional/perfil",
                data={
                    "nome_exibicao": "Dra. Teste",
                    "conselho": "CRP",
                    "registro_numero": "998879",
                    "registro_uf": "XX",
                    "descricao": "Atendo adultos.",
                },
                headers=headers,
            )
        assert r.status_code == 422


class TestAgenda:
    async def test_adiciona_e_remove_janela(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        perfil = await f.criar_profissional(sessao)
        async with cliente(app) as c:
            headers = await logar(c, app, sessao, settings, perfil.usuario_id)

            r = await c.post(
                "/profissional/agenda",
                data={"dia_semana": "1", "inicio": "09:00", "fim": "12:00"},
                headers={**headers, "HX-Request": "true"},
            )
            assert r.status_code == 200
            assert "09:00" in r.text and "12:00" in r.text

    async def test_janela_sobreposta_e_recusada(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """A constraint EXCLUDE vira uma mensagem em português, não um 500."""
        perfil = await f.criar_profissional(sessao)
        async with cliente(app) as c:
            headers = await logar(c, app, sessao, settings, perfil.usuario_id)
            await c.post(
                "/profissional/agenda",
                data={"dia_semana": "1", "inicio": "09:00", "fim": "12:00"},
                headers={**headers, "HX-Request": "true"},
            )
            r = await c.post(
                "/profissional/agenda",
                data={"dia_semana": "1", "inicio": "11:00", "fim": "13:00"},
                headers={**headers, "HX-Request": "true"},
            )
        assert r.status_code == 422
        assert "sobrep" in r.text.lower()
        # Erro de domínio em requisição HTMX é redirecionado para o #alerta.
        assert r.headers.get("HX-Retarget") == "#alerta"

    async def test_horario_invertido_e_recusado(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        perfil = await f.criar_profissional(sessao)
        async with cliente(app) as c:
            headers = await logar(c, app, sessao, settings, perfil.usuario_id)
            r = await c.post(
                "/profissional/agenda",
                data={"dia_semana": "1", "inicio": "14:00", "fim": "09:00"},
                headers={**headers, "HX-Request": "true"},
            )
        assert r.status_code == 422


class TestSimulador:
    async def test_mostra_comissao_e_liquido(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Requisito do mapa mental: informar comissão e custos de cartão.

        Sobre R$ 150,00, a comissão padrão de 12% é R$ 18,00.
        """
        perfil = await f.criar_profissional(sessao)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, perfil.usuario_id)
            r = await c.get("/profissional/simulador", params={"valor": "150,00"})

        assert r.status_code == 200
        assert "R$ 18,00" in r.text
        sopa = BeautifulSoup(r.text, "html.parser")
        linhas = sopa.select("tbody tr")
        assert len(linhas) == 3  # Pix, débito e crédito

    async def test_fragmento_htmx_devolve_so_a_tabela(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        perfil = await f.criar_profissional(sessao)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, perfil.usuario_id)
            r = await c.get(
                "/profissional/simulador",
                params={"valor": "200,00"},
                headers={"HX-Request": "true"},
            )
        assert r.status_code == 200
        assert "<table" in r.text
        assert "<!doctype html>" not in r.text.lower()

    async def test_valor_invalido_cai_no_padrao(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        perfil = await f.criar_profissional(sessao)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, perfil.usuario_id)
            r = await c.get("/profissional/simulador", params={"valor": "abc"})
        assert r.status_code == 200
        assert "R$ 150,00" in r.text


class TestValidacaoAmigavel:
    async def test_campo_faltando_vira_mensagem_em_portugues(self, app: FastAPI) -> None:
        """Sem handler de RequestValidationError, o FastAPI devolveria o JSON
        cru do Pydantic direto na tela do usuário."""
        async with cliente(app) as c:
            await c.get("/entrar")
            r = await c.post("/entrar", headers={"X-CSRF-Token": c.cookies[COOKIE_CSRF]}, data={})
        assert r.status_code == 422
        assert "Preencha corretamente" in r.text
        assert '"type":"missing"' not in r.text
