"""Suíte de contrato do `VideoProvider`.

A mesma bateria roda contra o **fake** e contra o **daily.co real**, este com
`respx` interceptando o HTTP e devolvendo respostas no formato documentado pelo
provedor. Nenhum teste toca a rede: tudo isto roda sem conta no daily.co.

O objetivo é o mesmo da suíte de pagamento — impedir que o fake minta. Um fake
que aceita o que o real recusa faz a suíte passar e a sala quebrar em produção,
com o paciente já esperando na tela.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx

from app.core.config import Settings
from app.core.tempo import agora_utc
from app.providers.base import Participante, VideoProvider
from app.providers.video.daily import BASE_URL, DailyVideoProvider, FalhaNoVideo
from app.providers.video.fake import FakeVideoProvider

pytestmark = pytest.mark.unit

SALA = "psi-abc123"
DOMINIO = "psiconnect"


def _settings(**extra: Any) -> Settings:
    campos: dict[str, Any] = {
        "daily_api_key": "teste-chave",
        "daily_domain": DOMINIO,
        "app_base_url": "https://psiconnect.test",
        "chave_criptografia": "0" * 44,
        "secret_key": "x" * 40,
    }
    campos.update(extra)
    return Settings(**campos)


def _sala_criada(**extra: Any) -> dict[str, Any]:
    """Resposta de `POST /v1/rooms`, campos conforme a doc do daily.co."""
    base = {
        "id": "d61cd7b2-3e2c-4a1b-9f0e-1a2b3c4d5e6f",
        "name": SALA,
        "api_created": True,
        "privacy": "private",
        "url": f"https://{DOMINIO}.daily.co/{SALA}",
        "created_at": "2026-09-07T12:00:00.000Z",
    }
    base.update(extra)
    return base


def _profissional() -> Participante:
    return Participante(id="11111111-1111-1111-1111-111111111111", nome="Ana", eh_dono=True)


def _paciente() -> Participante:
    return Participante(id="22222222-2222-2222-2222-222222222222", nome="João", eh_dono=False)


# ---------------------------------------------------------------------------
# Bateria comum: roda contra os dois provedores
# ---------------------------------------------------------------------------


@pytest.fixture(params=["fake", "daily"])
def provedor(request: pytest.FixtureRequest) -> VideoProvider:
    if request.param == "fake":
        return FakeVideoProvider(_settings())
    return DailyVideoProvider(_settings())


@pytest.fixture
def api() -> Any:
    """Intercepta a API do daily.co. Inócuo para o fake, que não faz HTTP."""
    with respx.mock as mock:
        mock.post(f"{BASE_URL}/rooms").mock(return_value=httpx.Response(200, json=_sala_criada()))
        mock.post(f"{BASE_URL}/meeting-tokens").mock(
            return_value=httpx.Response(200, json={"token": "eyJhbGciOi.Zm9v.YmFy"})
        )
        mock.delete(f"{BASE_URL}/rooms/{SALA}").mock(
            return_value=httpx.Response(200, json={"deleted": True, "name": SALA})
        )
        yield mock


class TestContratoComum:
    async def test_sala_criada_tem_url_e_validade(self, provedor: VideoProvider, api: Any) -> None:
        expira = agora_utc() + timedelta(hours=1)
        sala = await provedor.criar_sala(SALA, expira, privada=True)

        assert sala.nome == SALA
        assert sala.url.startswith("http")
        assert sala.expira_em == expira

    async def test_token_e_opaco(self, provedor: VideoProvider, api: Any) -> None:
        """Nada de informação útil embutida: o token vai na URL do iframe."""
        token = await provedor.emitir_token(SALA, _paciente(), agora_utc() + timedelta(hours=2))
        assert token
        assert "João" not in token

    async def test_url_de_entrada_carrega_o_token(self, provedor: VideoProvider, api: Any) -> None:
        """O template não sabe o nome do parâmetro — quem sabe é o provedor."""
        expira = agora_utc() + timedelta(hours=1)
        sala = await provedor.criar_sala(SALA, expira)
        token = await provedor.emitir_token(SALA, _profissional(), expira)

        url = provedor.url_de_entrada(sala.url, token)
        assert url.startswith(sala.url)
        assert token in url
        assert "?" in url

    async def test_url_de_entrada_nao_estraga_query_existente(
        self, provedor: VideoProvider
    ) -> None:
        url = provedor.url_de_entrada("https://exemplo.test/sala?lang=pt", "abc")
        assert "lang=pt" in url
        assert url.count("?") == 1

    async def test_admitir_nao_estoura(self, provedor: VideoProvider, api: Any) -> None:
        await provedor.criar_sala(SALA, agora_utc() + timedelta(hours=1))
        await provedor.admitir(SALA, _paciente().id)

    async def test_encerrar_sala_desconhecida_e_silencioso(
        self, provedor: VideoProvider, api: Any
    ) -> None:
        """O encerramento roda no fim de toda sessão; não pode derrubar nada."""
        api.delete(f"{BASE_URL}/rooms/{SALA}").mock(
            return_value=httpx.Response(404, json={"error": "not-found"})
        )
        await provedor.encerrar_sala(SALA)


# ---------------------------------------------------------------------------
# Específico do daily.co: o que vai no corpo da requisição
# ---------------------------------------------------------------------------


class TestCorpoDaRequisicao:
    async def test_sala_e_privada_e_expira(self, api: Any) -> None:
        expira = agora_utc() + timedelta(hours=1)
        await DailyVideoProvider(_settings()).criar_sala(SALA, expira, privada=True)

        enviado = api.calls.last.request
        corpo = _json(enviado)
        assert corpo["privacy"] == "private"
        assert corpo["properties"]["exp"] == int(expira.timestamp())
        # Sem isto a sala continua de pé depois do `exp`, só sem deixar entrar.
        assert corpo["properties"]["eject_at_room_exp"] is True

    async def test_nunca_pede_gravacao(self, api: Any) -> None:
        """ADR 0003: não guardamos áudio nem vídeo.

        No daily.co a gravação existe se for pedida — então o teste é que a
        chave sequer apareça no corpo.
        """
        await DailyVideoProvider(_settings()).criar_sala(SALA, agora_utc() + timedelta(hours=1))
        corpo = _json(api.calls.last.request)
        assert "enable_recording" not in corpo["properties"]

    async def test_chat_desligado(self, api: Any) -> None:
        """O que se escreve no chat escapa do consentimento de transcrição."""
        await DailyVideoProvider(_settings()).criar_sala(SALA, agora_utc() + timedelta(hours=1))
        assert _json(api.calls.last.request)["properties"]["enable_chat"] is False

    async def test_so_o_profissional_e_dono(self, api: Any) -> None:
        """`is_owner` é o que dá controle da chamada: nunca vai para o paciente."""
        provedor = DailyVideoProvider(_settings())
        expira = agora_utc() + timedelta(hours=2)

        await provedor.emitir_token(SALA, _profissional(), expira)
        assert _json(api.calls.last.request)["properties"]["is_owner"] is True

        await provedor.emitir_token(SALA, _paciente(), expira)
        propriedades = _json(api.calls.last.request)["properties"]
        assert propriedades["is_owner"] is False
        assert propriedades["room_name"] == SALA
        assert propriedades["exp"] == int(expira.timestamp())

    async def test_manda_so_o_primeiro_nome(self, api: Any) -> None:
        """Minimização: o provedor de vídeo não precisa do nome completo."""
        await DailyVideoProvider(_settings()).emitir_token(
            SALA, _paciente(), agora_utc() + timedelta(hours=2)
        )
        assert _json(api.calls.last.request)["properties"]["user_name"] == "João"

    async def test_token_vai_no_parametro_t(self) -> None:
        """`?token=` seria ignorado em silêncio e o participante veria
        "sala privada" sem entender por quê."""
        url = DailyVideoProvider(_settings()).url_de_entrada(
            f"https://{DOMINIO}.daily.co/{SALA}", "abc.def"
        )
        assert url.endswith("?t=abc.def")

    async def test_dominio_com_sufixo_e_aceito(self) -> None:
        """`.env` com `psiconnect.daily.co` não pode virar `...daily.co.daily.co`."""
        provedor = DailyVideoProvider(_settings(daily_domain="psiconnect.daily.co"))
        with respx.mock as mock:
            mock.post(f"{BASE_URL}/rooms").mock(
                return_value=httpx.Response(200, json=_sala_criada(url=None))
            )
            sala = await provedor.criar_sala(SALA, agora_utc() + timedelta(hours=1))
        assert sala.url == f"https://{DOMINIO}.daily.co/{SALA}"


class TestFalhas:
    async def test_sala_repetida_e_reaproveitada(self) -> None:
        """O worker pode repetir o preparo; 400 do provedor não pode derrubar.

        Acontece quando a sala existe lá e a linha em `sessoes` não registrou.
        """
        with respx.mock as mock:
            mock.post(f"{BASE_URL}/rooms").mock(
                return_value=httpx.Response(
                    400, json={"error": "invalid-request-error", "info": "already exists"}
                )
            )
            mock.get(f"{BASE_URL}/rooms/{SALA}").mock(
                return_value=httpx.Response(200, json=_sala_criada())
            )
            sala = await DailyVideoProvider(_settings()).criar_sala(
                SALA, agora_utc() + timedelta(hours=1)
            )
        assert sala.url == f"https://{DOMINIO}.daily.co/{SALA}"

    async def test_erro_de_verdade_na_criacao_estoura(self) -> None:
        with respx.mock as mock:
            mock.post(f"{BASE_URL}/rooms").mock(
                return_value=httpx.Response(400, json={"error": "invalid-request-error"})
            )
            mock.get(f"{BASE_URL}/rooms/{SALA}").mock(return_value=httpx.Response(404, json={}))
            with pytest.raises(FalhaNoVideo):
                await DailyVideoProvider(_settings()).criar_sala(
                    SALA, agora_utc() + timedelta(hours=1)
                )

    async def test_erro_de_servidor_vira_erro_de_dominio(self) -> None:
        """502 nosso, não stack trace: a rota traduz para uma tela decente."""
        with respx.mock as mock:
            mock.post(f"{BASE_URL}/meeting-tokens").mock(
                return_value=httpx.Response(500, json={"error": "server-error"})
            )
            with pytest.raises(FalhaNoVideo) as erro:
                await DailyVideoProvider(_settings()).emitir_token(
                    SALA, _paciente(), agora_utc() + timedelta(hours=2)
                )
        assert erro.value.status_http == 502

    async def test_resposta_sem_token_nao_passa(self) -> None:
        """200 com corpo vazio não pode virar `?t=None` na URL do iframe."""
        with respx.mock as mock:
            mock.post(f"{BASE_URL}/meeting-tokens").mock(return_value=httpx.Response(200, json={}))
            with pytest.raises(FalhaNoVideo):
                await DailyVideoProvider(_settings()).emitir_token(
                    SALA, _paciente(), agora_utc() + timedelta(hours=2)
                )

    async def test_falha_de_rede_e_repetida(self) -> None:
        """Só transporte é repetido — 4xx significa que o pedido foi recusado."""
        with respx.mock as mock:
            rota = mock.post(f"{BASE_URL}/rooms")
            rota.side_effect = [
                httpx.ConnectError("sem rota"),
                httpx.Response(200, json=_sala_criada()),
            ]
            sala = await DailyVideoProvider(_settings()).criar_sala(
                SALA, agora_utc() + timedelta(hours=1)
            )
        assert sala.nome == SALA
        assert rota.call_count == 2


def _json(requisicao: httpx.Request) -> Any:
    import json

    return json.loads(requisicao.content)
