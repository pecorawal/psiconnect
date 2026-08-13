"""Cadastro, login, CSRF e autorização."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.sessao_web import COOKIE_CSRF, COOKIE_SESSAO

pytestmark = [pytest.mark.web, pytest.mark.db]


def cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://teste", follow_redirects=False
    )


async def _com_csrf(c: AsyncClient) -> dict[str, str]:
    """Visita uma página para receber o cookie CSRF e devolve o header."""
    await c.get("/entrar")
    return {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}


class TestCsrf:
    async def test_get_recebe_cookie_csrf(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            r = await c.get("/entrar")
        assert r.status_code == 200
        assert COOKIE_CSRF in c.cookies

    async def test_post_sem_token_e_rejeitado(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            await c.get("/entrar")  # pega o cookie
            r = await c.post("/entrar", data={"email": "x@y.br", "senha": "12345678"})
        assert r.status_code == 403
        assert r.json()["codigo"] == "csrf_invalido"

    async def test_post_com_token_errado_e_rejeitado(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            await c.get("/entrar")
            r = await c.post(
                "/entrar",
                data={"email": "x@y.br", "senha": "12345678"},
                headers={"X-CSRF-Token": "token-inventado"},
            )
        assert r.status_code == 403

    async def test_cookie_de_sessao_e_httponly(self, app: FastAPI) -> None:
        """O cookie de sessão precisa ser invisível ao JS (ADR 0002)."""
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            r = await c.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Maria Teste",
                    "email": "maria.httponly@teste.br",
                    "senha": "senha-boa-2026",
                    "data_nascimento": "1990-01-01",
                    "aceite": "1",
                },
                headers=headers,
            )
        assert r.status_code == 303
        set_cookie = "".join(v for k, v in r.headers.multi_items() if k.lower() == "set-cookie")
        assert COOKIE_SESSAO in set_cookie
        assert "httponly" in set_cookie.lower()


class TestCadastroPaciente:
    async def test_cadastro_e_login_funcionam(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            r = await c.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "João Teste",
                    "email": "joao.fluxo@teste.br",
                    "senha": "senha-boa-2026",
                    "data_nascimento": "1990-01-01",
                    "aceite": "1",
                },
                headers=headers,
            )
            assert r.status_code == 303
            assert r.headers["location"] == "/paciente/sintomas"
            assert COOKIE_SESSAO in c.cookies

    async def test_menor_de_18_vai_para_o_fluxo_do_responsavel(self, app: FastAPI) -> None:
        """Menor se cadastra, mas a conta nasce pendente (LGPD art. 14)."""
        from datetime import timedelta

        from app.core.tempo import agora_utc

        quinze_anos = (agora_utc() - timedelta(days=365 * 15 + 10)).date()
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            r = await c.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Adolescente Teste",
                    "email": "adolescente@teste.br",
                    "senha": "senha-boa-2026",
                    "data_nascimento": quinze_anos.isoformat(),
                    "aceite": "1",
                },
                headers=headers,
            )
        assert r.status_code == 303
        assert r.headers["location"] == "/cadastro/responsavel"

    async def test_crianca_abaixo_de_12_e_recusada(self, app: FastAPI) -> None:
        """Psicoterapia infantil exige setting e formação que este produto
        não contempla."""
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            r = await c.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Criança Teste",
                    "email": "crianca@teste.br",
                    "senha": "senha-boa-2026",
                    "data_nascimento": "2019-01-01",
                    "aceite": "1",
                },
                headers=headers,
            )
        assert r.status_code == 422
        assert "12 anos" in r.text

    async def test_senha_fraca_e_recusada(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            r = await c.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Fraco Teste",
                    "email": "fraco@teste.br",
                    "senha": "123",
                    "data_nascimento": "1990-01-01",
                    "aceite": "1",
                },
                headers=headers,
            )
        assert r.status_code == 422

    async def test_sem_aceite_nao_cadastra(self, app: FastAPI) -> None:
        """Consentimento para dado de saúde é obrigatório (LGPD art. 11)."""
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            r = await c.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Sem Aceite",
                    "email": "sem.aceite@teste.br",
                    "senha": "senha-boa-2026",
                    "data_nascimento": "1990-01-01",
                },
                headers=headers,
            )
        assert r.status_code == 422

    async def test_email_duplicado(self, app: FastAPI) -> None:
        dados = {
            "nome_completo": "Dup Teste",
            "email": "duplicado@teste.br",
            "senha": "senha-boa-2026",
            "data_nascimento": "1990-01-01",
            "aceite": "1",
        }
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            await c.post("/cadastro/paciente", data=dados, headers=headers)
        async with cliente(app) as c2:
            headers = await _com_csrf(c2)
            r = await c2.post("/cadastro/paciente", data=dados, headers=headers)
        assert r.status_code == 409


class TestLogin:
    async def test_credenciais_invalidas_nao_revelam_se_email_existe(self, app: FastAPI) -> None:
        """A mensagem tem de ser idêntica nos dois casos.

        Distinguir permitiria descobrir quem tem conta -- numa plataforma de
        psicologia, isso já é informação sensível por si só.
        """
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            inexistente = await c.post(
                "/entrar",
                data={"email": "ninguem@teste.br", "senha": "seja-la-o-que-for"},
                headers=headers,
            )
        async with cliente(app) as c2:
            headers = await _com_csrf(c2)
            await c2.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Existe Teste",
                    "email": "existe@teste.br",
                    "senha": "senha-boa-2026",
                    "data_nascimento": "1990-01-01",
                    "aceite": "1",
                },
                headers=headers,
            )
        async with cliente(app) as c3:
            headers = await _com_csrf(c3)
            senha_errada = await c3.post(
                "/entrar",
                data={"email": "existe@teste.br", "senha": "senha-errada-mesmo"},
                headers=headers,
            )

        assert inexistente.status_code == senha_errada.status_code == 401
        assert "E-mail ou senha incorretos" in inexistente.text
        assert "E-mail ou senha incorretos" in senha_errada.text

    async def test_login_e_logout(self, app: FastAPI) -> None:
        async with cliente(app) as c:
            headers = await _com_csrf(c)
            await c.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Ciclo Teste",
                    "email": "ciclo@teste.br",
                    "senha": "senha-boa-2026",
                    "data_nascimento": "1990-01-01",
                    "aceite": "1",
                },
                headers=headers,
            )
            assert COOKIE_SESSAO in c.cookies

            r = await c.post("/sair", headers={"X-CSRF-Token": c.cookies[COOKIE_CSRF]})
            assert r.status_code == 303
            assert not c.cookies.get(COOKIE_SESSAO)
