"""Sala de espera: o stream SSE e o polling que ficou como plano B."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.sessao_web import COOKIE_CSRF, COOKIE_SESSAO, assinar_token
from app.core.tempo import agora_utc
from app.models import Agendamento, PerfilPaciente, PerfilProfissional, Sessao, StatusSessao
from app.services.auth_service import AuthService, ContextoRequisicao
from app.web.rotas import sessao as rotas_sessao
from tests import fabricas as f

pytestmark = [pytest.mark.web, pytest.mark.db]


def cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://teste", follow_redirects=False
    )


async def logar(
    c: AsyncClient, sessao: AsyncSession, settings: Settings, usuario_id: object
) -> dict[str, str]:
    from app.models import Usuario

    usuario = await sessao.get(Usuario, usuario_id)
    assert usuario is not None
    token = await AuthService(sessao).criar_sessao(usuario, ContextoRequisicao())
    await sessao.flush()

    await c.get("/")  # recebe o cookie CSRF
    c.cookies.set(COOKIE_SESSAO, assinar_token(settings, token), domain="teste.local")
    return {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}


async def cenario(
    sessao: AsyncSession,
) -> tuple[PerfilProfissional, PerfilPaciente, Agendamento, Sessao]:
    """Sala pronta, consentimento dado — o paciente parado no lobby."""
    profissional = await f.criar_profissional(sessao)
    paciente = await f.criar_paciente(sessao)
    especialidade = await f.criar_especialidade(sessao)
    agendamento = await f.criar_agendamento(sessao, profissional, paciente, especialidade)

    atendimento = Sessao(
        agendamento_id=agendamento.id,
        provedor_video="fake",
        sala_nome="psi-teste",
        sala_url="http://teste/sessao/simulada/psi-teste",
        status=StatusSessao.SALA_PRONTA,
        transcricao_consentida=True,
    )
    sessao.add(atendimento)
    await sessao.flush()
    return profissional, paciente, agendamento, atendimento


async def ler_stream(c: AsyncClient, url: str, *, limite_s: float = 10.0) -> str:
    """Consome o stream inteiro e devolve o texto bruto.

    Nada de ler o começo e desistir: o ``ASGITransport`` do httpx junta o corpo
    todo antes de entregar (``ASGIResponseStream`` faz ``b"".join``), então um
    stream que não termina simplesmente pendura. Por isso a fixture abaixo dá um
    teto curto de vida ao stream — é o mesmo caminho de código que, em produção,
    fecha a conexão de 15 em 15 minutos para o EventSource reconectar.
    """
    async with asyncio.timeout(limite_s):
        r = await c.get(url)
    assert r.status_code == 200, r.status_code
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-store"
    # nginx bufferizando um SSE é o mesmo que não ter SSE.
    assert r.headers["x-accel-buffering"] == "no"
    return r.text


@pytest.fixture(autouse=True)
def _stream_curto(monkeypatch: pytest.MonkeyPatch) -> None:
    """Em produção: ciclo de 2s, stream de 15min. Aqui, tudo em milissegundos."""
    monkeypatch.setattr(rotas_sessao, "INTERVALO_SSE_S", 0.01)
    monkeypatch.setattr(rotas_sessao, "DURACAO_MAXIMA_STREAM_S", 0.2)


class TestStreamDeEventos:
    async def test_exige_login(self, app: FastAPI, sessao: AsyncSession) -> None:
        _, _, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            r = await c.get(f"/sessao/{agendamento.id}/eventos")
        assert r.status_code == 401

    async def test_estranho_leva_404_e_nao_um_stream_morto(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """A autorização é resolvida antes de abrir o stream.

        Se virasse um stream que fecha no primeiro byte, o EventSource ficaria
        reabrindo a conexão para sempre.
        """
        _, _, agendamento, _ = await cenario(sessao)
        intruso = await f.criar_paciente(sessao, nome="Outra Pessoa")
        async with cliente(app) as c:
            await logar(c, sessao, settings, intruso.usuario_id)
            r = await c.get(f"/sessao/{agendamento.id}/eventos")
        assert r.status_code == 404
        assert not r.headers["content-type"].startswith("text/event-stream")

    async def test_primeiro_evento_traz_o_estado_de_espera(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        _, paciente, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            recebido = await ler_stream(c, f"/sessao/{agendamento.id}/eventos")

        assert "event: estado" in recebido
        assert "retry: " in recebido  # o cliente aprende quando reconectar
        assert "Aguardando o profissional" in recebido
        assert "event: entrar" not in recebido

    async def test_manda_entrar_quando_o_profissional_admite(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        _, paciente, agendamento, atendimento = await cenario(sessao)
        atendimento.profissional_entrou_em = agora_utc()
        atendimento.paciente_admitido_em = agora_utc()
        await sessao.flush()

        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            recebido = await ler_stream(c, f"/sessao/{agendamento.id}/eventos")

        assert "event: entrar" in recebido
        assert f"data: /sessao/{agendamento.id}/sala" in recebido

    async def test_o_estado_so_e_reenviado_quando_muda(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Vários ciclos sem novidade não podem virar vários eventos.

        Reenviar o mesmo HTML a cada 2s desperdiçaria banda e faria a tela
        piscar — o ganho sobre o polling desapareceria.
        """
        _, paciente, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            recebido = await ler_stream(c, f"/sessao/{agendamento.id}/eventos")

        # Foram ~20 ciclos de leitura; um único evento saiu.
        assert recebido.count("event: estado") == 1

    async def test_manda_keepalive_no_silencio(
        self,
        app: FastAPI,
        sessao: AsyncSession,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sem tráfego nenhum, um proxy no caminho derruba a conexão ociosa."""
        monkeypatch.setattr(rotas_sessao, "INTERVALO_KEEPALIVE_S", 0.03)
        _, paciente, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            recebido = await ler_stream(c, f"/sessao/{agendamento.id}/eventos")

        assert ": ping" in recebido


class TestPollingContinuaSendoPlanoB:
    async def test_rota_de_estado_ainda_responde(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """O JS cai para /estado quando o SSE não existe ou morre."""
        _, paciente, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            r = await c.get(f"/sessao/{agendamento.id}/estado")
        assert r.status_code == 200
        assert "Aguardando o profissional" in r.text

    async def test_lobby_entrega_os_dois_caminhos(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        _, paciente, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            r = await c.get(f"/sessao/{agendamento.id}/lobby")

        assert r.status_code == 200
        assert f'data-eventos="/sessao/{agendamento.id}/eventos"' in r.text
        assert f'data-estado="/sessao/{agendamento.id}/estado"' in r.text
        assert "lobby-sse.js" in r.text
        # O fragmento já vem do servidor: a tela não fica vazia até o primeiro
        # evento chegar.
        assert "Aguardando o profissional" in r.text


class TestEntradaNaSala:
    """A admissão do profissional precisa valer no servidor, não só na tela."""

    async def test_paciente_nao_admitido_volta_para_o_lobby(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Digitar a URL da sala não pode furar a fila.

        Com provedor fake isso não tinha efeito visível; com sala de verdade é
        entrar na chamada sem ter sido chamado.
        """
        _, paciente, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            r = await c.get(f"/sessao/{agendamento.id}/sala")

        assert r.status_code == 303
        assert r.headers["location"] == f"/sessao/{agendamento.id}/lobby"

    async def test_paciente_admitido_entra(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        _, paciente, agendamento, atendimento = await cenario(sessao)
        atendimento.paciente_admitido_em = agora_utc()
        await sessao.flush()

        async with cliente(app) as c:
            await logar(c, sessao, settings, paciente.usuario_id)
            r = await c.get(f"/sessao/{agendamento.id}/sala")

        assert r.status_code == 200
        # O token vai na URL montada pelo provedor, não concatenada no template.
        assert "/sessao/simulada/psi-teste?token=faketoken." in r.text

    async def test_profissional_entra_sem_esperar_ninguem(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Ele é o dono da sala: é quem abre a porta."""
        profissional, _, agendamento, _ = await cenario(sessao)
        async with cliente(app) as c:
            await logar(c, sessao, settings, profissional.usuario_id)
            r = await c.get(f"/sessao/{agendamento.id}/sala")

        assert r.status_code == 200

    async def test_servico_recusa_token_de_quem_nao_foi_admitido(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """A regra vive no serviço; o redirecionamento da rota é só cortesia."""
        from app.core.erros import SessaoNaoDisponivel
        from app.models import Usuario
        from app.providers.registry import montar_providers
        from app.services.parametros_service import ParametrosService
        from app.services.sessao_service import SessaoService

        _, paciente, _, atendimento = await cenario(sessao)
        usuario = await sessao.get(Usuario, paciente.usuario_id)
        assert usuario is not None

        servico = SessaoService(
            sessao,
            ParametrosService(sessao, settings),
            montar_providers(settings).video,
        )
        with pytest.raises(SessaoNaoDisponivel):
            await servico.token_de_entrada(atendimento, usuario)
