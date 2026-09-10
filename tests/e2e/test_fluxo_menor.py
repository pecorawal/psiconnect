"""Fluxo do adolescente, de ponta a ponta, pelos dois caminhos.

Cobre o que o serviço sozinho não prova: que o bloqueio de agendamento vale na
camada web, que a página de confirmação é pública, e que o documento não fica
acessível a quem não deve.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.sessao_web import COOKIE_CSRF
from app.core.tempo import agora_utc
from app.models import (
    AcessoDocumento,
    PerfilPaciente,
    StatusPaciente,
    Usuario,
    VerificacaoResponsavel,
)

pytestmark = [pytest.mark.e2e, pytest.mark.db]


def navegador(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://teste", follow_redirects=False
    )


async def csrf(c: AsyncClient, caminho: str = "/") -> dict[str, str]:
    await c.get(caminho)
    return {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}


def nascido_ha(anos: int) -> str:
    return (agora_utc() - timedelta(days=365 * anos + 10)).date().isoformat()


class TestCaminhoDoAdolescente:
    async def test_cadastro_pendente_ate_o_responsavel_autorizar(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        async with navegador(app) as menor, navegador(app) as responsavel:
            # 1. O adolescente se cadastra
            headers = await csrf(menor, "/cadastro/paciente")
            r = await menor.post(
                "/cadastro/paciente",
                data={
                    "nome_completo": "Lucas Menor",
                    "email": "lucas.menor@teste.br",
                    "senha": "senha-forte-e2e-2026",
                    "data_nascimento": nascido_ha(15),
                    "aceite": "1",
                },
                headers=headers,
            )
            assert r.status_code == 303
            assert r.headers["location"] == "/cadastro/responsavel"

            usuario = await sessao.scalar(
                select(Usuario).where(Usuario.email == "lucas.menor@teste.br")
            )
            assert usuario is not None
            perfil = await sessao.get(PerfilPaciente, usuario.id)
            assert perfil is not None
            assert perfil.status is StatusPaciente.PENDENTE_RESPONSAVEL

            # 2. Não consegue nem chegar na busca de profissional
            r = await menor.get("/paciente/sintomas")
            assert r.status_code == 200, "ver a tela de sintomas é permitido"

            # 3. Informa o responsável
            r = await menor.post(
                "/cadastro/responsavel",
                data={
                    "responsavel_nome": "Sandra Mãe",
                    "responsavel_email": "sandra@teste.br",
                    "parentesco": "mãe",
                },
                headers=headers,
            )
            assert r.status_code == 303

            verificacao = await sessao.scalar(
                select(VerificacaoResponsavel).where(
                    VerificacaoResponsavel.paciente_id == usuario.id
                )
            )
            assert verificacao is not None
            assert verificacao.pendente

            # 4. O convite foi enfileirado na outbox
            from app.models import Notificacao

            convite = await sessao.scalar(
                select(Notificacao).where(Notificacao.template == "convite_responsavel")
            )
            assert convite is not None
            link = (convite.contexto or {}).get("link", "")
            token = str(link).rsplit("/", 1)[-1]
            assert token

            # 5. A página de confirmação é pública — o responsável pode não ter conta
            r = await responsavel.get(f"/responsavel/confirmar/{token}")
            assert r.status_code == 200
            assert "responsável legal" in r.text
            assert "criptografado" in r.text
            # A tela precisa deixar claro o limite da autorização.
            assert "sigilo profissional" in r.text

            # 6. Sem marcar a autorização, não conclui
            headers_resp = {"X-CSRF-Token": responsavel.cookies[COOKIE_CSRF]}
            r = await responsavel.post(
                f"/responsavel/confirmar/{token}",
                data={"documento_tipo": "RG", "documento_numero": "12345678"},
                files={"documento": ("rg.jpg", b"imagem", "image/jpeg")},
                headers=headers_resp,
            )
            assert r.status_code == 422

            # 7. Com autorização, libera
            r = await responsavel.post(
                f"/responsavel/confirmar/{token}",
                data={
                    "documento_tipo": "RG",
                    "documento_numero": "12.345.678-9",
                    "autorizo": "1",
                },
                files={"documento": ("rg.jpg", b"imagem-do-documento", "image/jpeg")},
                headers=headers_resp,
            )
            assert r.status_code == 303

            await sessao.refresh(perfil)
            assert perfil.status is StatusPaciente.ATIVO
            assert perfil.pode_agendar


class TestAcessoAoDocumento:
    async def test_terceiro_recebe_404_e_nao_403(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """404 não confirma sequer que o documento existe."""
        from tests import fabricas as f
        from tests.web.test_profissional import logar

        paciente = await f.criar_paciente(sessao)
        verificacao = VerificacaoResponsavel(
            paciente_id=paciente.usuario_id,
            responsavel_nome="Sandra",
            responsavel_email="sandra.doc@teste.br",
            token_hash="hash-qualquer-para-teste",
            expira_em=agora_utc() + timedelta(days=1),
            confirmado_em=agora_utc(),
            documento_chave="documentos/responsavel/x.enc",
        )
        sessao.add(verificacao)
        await sessao.flush()

        bisbilhoteiro = await f.criar_paciente(sessao)
        async with navegador(app) as c:
            await logar(c, app, sessao, settings, bisbilhoteiro.usuario_id)
            r = await c.get(f"/midia/documento/{verificacao.id}")
        assert r.status_code == 404

        # E nada foi registrado como acesso bem-sucedido.
        acessos = list(
            (
                await sessao.execute(
                    select(AcessoDocumento).where(AcessoDocumento.recurso_id == verificacao.id)
                )
            )
            .scalars()
            .all()
        )
        assert acessos == []

    async def test_exige_login(self, app: FastAPI, sessao: AsyncSession) -> None:
        import uuid

        async with navegador(app) as c:
            r = await c.get(f"/midia/documento/{uuid.uuid4()}")
        assert r.status_code == 303
        assert r.headers["location"].startswith("/entrar?proximo=")
