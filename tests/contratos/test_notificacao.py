"""Suíte de contrato do `NotificationProvider` (N1).

A mesma bateria roda contra o **console** (fake de dev) e contra o **SMTP
real**, este com o ``smtplib.SMTP`` trocado por um servidor em memória que
guarda o que recebeu. Nenhum teste abre conexão de rede.

Além do contrato comum, o SMTP tem testes próprios: todo template que a outbox
enfileira precisa ter texto de e-mail, o e-mail de alerta de risco não carrega
dado clínico, e a falha do servidor não vaza o endereço do destinatário.
"""

from __future__ import annotations

import smtplib
from collections.abc import Iterator
from email.message import EmailMessage
from typing import Any, ClassVar

import pytest

from app.core.config import Ambiente, Settings
from app.core.erros import ProviderIndisponivel
from app.models.enums import CanalNotificacao
from app.providers.base import NotificationProvider
from app.providers.notificacao.console import ConsoleNotificationProvider
from app.providers.notificacao.smtp import (
    RenderizadorEmail,
    SmtpEmailProvider,
    TemplateDeEmailAusente,
)
from app.providers.registry import _notificacao
from app.services.notificacao_service import (
    TEMPLATE_AGENDAMENTO_CONFIRMADO,
    TEMPLATE_LINK_SESSAO,
)
from app.services.protocolo_risco_service import TEMPLATE_ALERTA_RISCO
from app.services.responsavel_service import TEMPLATE_CONVITE

pytestmark = [pytest.mark.unit, pytest.mark.contrato]

BASE_URL = "https://psiconnect.test"

#: Contexto de cada template exatamente com as chaves que os serviços gravam
#: na outbox. Se um serviço mudar o contexto, o teste de renderização acusa.
CONTEXTOS: dict[str, dict[str, Any]] = {
    TEMPLATE_AGENDAMENTO_CONFIRMADO: {
        "agendamento_id": "8f1c2a8e-0000-4000-8000-000000000001",
        "profissional_nome": "Dra. Ana Souza",
        "profissional_foto": None,
        "paciente_nome": "João",
        "data": "03/12/2026",
        "hora": "14:00",
        "duracao_min": 50,
    },
    TEMPLATE_LINK_SESSAO: {
        "agendamento_id": "8f1c2a8e-0000-4000-8000-000000000001",
        "profissional_nome": "Dra. Ana Souza",
        "profissional_foto": None,
        "paciente_nome": "João",
        "data": "03/12/2026",
        "hora": "14:00",
        "duracao_min": 50,
        "sala_url": "https://psiconnect.daily.co/psi-abc123",
    },
    TEMPLATE_CONVITE: {
        "responsavel_nome": "Maria Lima",
        "menor_primeiro_nome": "Pedro",
        "link": f"{BASE_URL}/responsavel/confirmar/tok",
        "prazo_dias": 7,
    },
    TEMPLATE_ALERTA_RISCO: {"paciente_nome": "Marina"},
}


def _settings(**extra: Any) -> Settings:
    campos: dict[str, Any] = {
        "app_secret_key": "x" * 40,
        "app_base_url": BASE_URL,
        "notificacao_provider": "smtp",
        "smtp_host": "smtp.teste",
        "smtp_port": 1025,
        "email_remetente": "nao-responda@psiconnect.test",
    }
    campos.update(extra)
    return Settings(**campos)


class ServidorSmtpFalso:
    """Faz o papel de ``smtplib.SMTP``: guarda as mensagens em vez de enviar."""

    recebidas: ClassVar[list[EmailMessage]] = []
    falha: ClassVar[smtplib.SMTPException | None] = None
    eventos: ClassVar[list[str]] = []

    def __init__(self, host: str, port: int, timeout: float = 0, **_: Any) -> None:
        self.eventos.append(f"conectar {host}:{port}")

    def __enter__(self) -> ServidorSmtpFalso:
        return self

    def __exit__(self, *_: object) -> None:
        self.eventos.append("sair")

    def starttls(self, **_: Any) -> None:
        self.eventos.append("starttls")

    def login(self, usuario: str, _senha: str) -> None:
        self.eventos.append(f"login {usuario}")

    def send_message(self, mensagem: EmailMessage) -> None:
        if self.falha is not None:
            raise self.falha
        self.recebidas.append(mensagem)


@pytest.fixture
def servidor(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[ServidorSmtpFalso]]:
    ServidorSmtpFalso.recebidas = []
    ServidorSmtpFalso.eventos = []
    ServidorSmtpFalso.falha = None
    monkeypatch.setattr(smtplib, "SMTP", ServidorSmtpFalso)
    monkeypatch.setattr(smtplib, "SMTP_SSL", ServidorSmtpFalso)
    yield ServidorSmtpFalso


@pytest.fixture(params=["console", "smtp"])
def provedor(
    request: pytest.FixtureRequest, servidor: type[ServidorSmtpFalso]
) -> NotificationProvider:
    if request.param == "console":
        return ConsoleNotificationProvider()
    return SmtpEmailProvider(_settings())


# ---------------------------------------------------------------------------
# Contrato comum
# ---------------------------------------------------------------------------


class TestContrato:
    def test_e_um_notification_provider(self, provedor: NotificationProvider) -> None:
        assert isinstance(provedor, NotificationProvider)

    def test_suporta_email(self, provedor: NotificationProvider) -> None:
        assert provedor.suporta(CanalNotificacao.EMAIL)

    @pytest.mark.parametrize("template", sorted(CONTEXTOS))
    async def test_envia_cada_template_da_outbox(
        self, provedor: NotificationProvider, template: str
    ) -> None:
        resultado = await provedor.enviar(
            CanalNotificacao.EMAIL,
            "joao@exemplo.com",
            template,
            CONTEXTOS[template],
            f"chave:{template}",
        )
        assert resultado.sucesso
        assert resultado.provedor_msg_id
        assert resultado.erro is None


# ---------------------------------------------------------------------------
# SMTP
# ---------------------------------------------------------------------------


class TestSmtp:
    def test_so_atende_email(self) -> None:
        """WhatsApp sem provedor real: o worker cancela, em vez de fingir envio."""
        p = SmtpEmailProvider(_settings())
        assert not p.suporta(CanalNotificacao.WHATSAPP)
        assert not p.suporta(CanalNotificacao.PUSH)

    async def test_monta_o_email(self, servidor: type[ServidorSmtpFalso]) -> None:
        p = SmtpEmailProvider(_settings())
        await p.enviar(
            CanalNotificacao.EMAIL,
            "joao@exemplo.com",
            TEMPLATE_AGENDAMENTO_CONFIRMADO,
            CONTEXTOS[TEMPLATE_AGENDAMENTO_CONFIRMADO],
            "confirmado:abc:EMAIL",
        )
        [msg] = servidor.recebidas
        assert msg["To"] == "joao@exemplo.com"
        assert msg["From"] == "PsiConnect <nao-responda@psiconnect.test>"
        assert msg["Subject"] == "Sessão confirmada: 03/12/2026 às 14:00"
        corpo = msg.get_content()
        assert "Dra. Ana Souza" in corpo
        assert f"{BASE_URL}/painel" in corpo
        # Texto puro: sem HTML, sem imagem remota para rastrear abertura.
        assert msg.get_content_type() == "text/plain"

    async def test_message_id_estavel_por_chave(self, servidor: type[ServidorSmtpFalso]) -> None:
        """Retry da mesma linha chega com o mesmo Message-ID; linhas diferentes, não."""
        p = SmtpEmailProvider(_settings())
        contexto = CONTEXTOS[TEMPLATE_ALERTA_RISCO]
        for chave in ("risco:a", "risco:a", "risco:b"):
            await p.enviar(
                CanalNotificacao.EMAIL, "x@exemplo.com", TEMPLATE_ALERTA_RISCO, contexto, chave
            )
        ids = [m["Message-ID"] for m in servidor.recebidas]
        assert ids[0] == ids[1] != ids[2]
        assert ids[0].endswith("@psiconnect.test>")

    async def test_link_da_sessao_aponta_para_a_aplicacao(
        self, servidor: type[ServidorSmtpFalso]
    ) -> None:
        """A sala do daily.co exige token: o e-mail leva ao lobby, que admite o paciente."""
        p = SmtpEmailProvider(_settings())
        contexto = CONTEXTOS[TEMPLATE_LINK_SESSAO]
        await p.enviar(CanalNotificacao.EMAIL, "x@exemplo.com", TEMPLATE_LINK_SESSAO, contexto, "k")
        corpo = servidor.recebidas[0].get_content()
        assert f"{BASE_URL}/sessao/{contexto['agendamento_id']}" in corpo
        assert "daily.co" not in corpo

    async def test_alerta_de_risco_sem_dado_clinico(
        self, servidor: type[ServidorSmtpFalso]
    ) -> None:
        p = SmtpEmailProvider(_settings())
        await p.enviar(
            CanalNotificacao.EMAIL,
            "dra@exemplo.com",
            TEMPLATE_ALERTA_RISCO,
            CONTEXTOS[TEMPLATE_ALERTA_RISCO],
            "risco:k",
        )
        msg = servidor.recebidas[0]
        texto = (msg["Subject"] + msg.get_content()).lower()
        assert "marina" in texto
        for termo in ("suic", "machuc", "violên", "sintoma", "risco"):
            assert termo not in texto, termo

    async def test_tls_e_login_quando_configurados(self, servidor: type[ServidorSmtpFalso]) -> None:
        p = SmtpEmailProvider(
            _settings(smtp_port=587, smtp_tls=True, smtp_usuario="u", smtp_senha="s")
        )
        await p.enviar(
            CanalNotificacao.EMAIL,
            "x@exemplo.com",
            TEMPLATE_ALERTA_RISCO,
            {"paciente_nome": "A"},
            "k",
        )
        assert servidor.eventos == ["conectar smtp.teste:587", "starttls", "login u", "sair"]

    async def test_falha_do_servidor_nao_vaza_o_endereco(
        self, servidor: type[ServidorSmtpFalso]
    ) -> None:
        servidor.falha = smtplib.SMTPRecipientsRefused(
            {"joao@exemplo.com": (550, b"joao@exemplo.com: mailbox unavailable")}
        )
        resultado = await SmtpEmailProvider(_settings()).enviar(
            CanalNotificacao.EMAIL,
            "joao@exemplo.com",
            TEMPLATE_ALERTA_RISCO,
            {"paciente_nome": "A"},
            "k",
        )
        assert not resultado.sucesso
        assert resultado.erro == "SMTPRecipientsRefused"
        assert "joao" not in (resultado.erro or "")

    async def test_contexto_incompleto_e_erro(self, servidor: type[ServidorSmtpFalso]) -> None:
        """Melhor ir para retry/FALHA do que sair um e-mail com buraco no texto."""
        with pytest.raises(Exception, match="paciente_nome"):
            await SmtpEmailProvider(_settings()).enviar(
                CanalNotificacao.EMAIL, "x@exemplo.com", TEMPLATE_ALERTA_RISCO, {}, "k"
            )
        assert servidor.recebidas == []

    def test_template_desconhecido_e_erro(self) -> None:
        with pytest.raises(TemplateDeEmailAusente):
            RenderizadorEmail(BASE_URL).renderizar("nao_existe", {})

    def test_todo_template_tem_contexto_de_teste(self) -> None:
        """Template de e-mail novo sem entrada em CONTEXTOS passaria sem teste."""
        assert RenderizadorEmail(BASE_URL).templates() == set(CONTEXTOS)


class TestBoot:
    def test_registry_escolhe_smtp(self) -> None:
        assert isinstance(_notificacao(_settings()), SmtpEmailProvider)

    def test_producao_sem_tls_falha_no_boot(self) -> None:
        with pytest.raises(ProviderIndisponivel, match="SMTP_TLS"):
            _notificacao(_settings(app_env=Ambiente.PRODUCAO, smtp_usuario="u", smtp_senha="s"))

    def test_producao_sem_credencial_falha_no_boot(self) -> None:
        with pytest.raises(ProviderIndisponivel, match="SMTP_USUARIO"):
            _notificacao(_settings(app_env=Ambiente.PRODUCAO, smtp_port=587, smtp_tls=True))

    def test_console_e_recusado_em_producao(self) -> None:
        s = _settings(app_env=Ambiente.PRODUCAO, notificacao_provider="console")
        with pytest.raises(RuntimeError, match="NOTIFICACAO_PROVIDER"):
            s.validar_para_producao()
