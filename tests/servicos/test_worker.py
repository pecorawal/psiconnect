"""Worker: outbox, expiração de reservas, salas T-20min e no-show."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.tempo import agora_utc
from app.models import (
    CanalNotificacao,
    Notificacao,
    Sessao,
    StatusAgendamento,
    StatusNotificacao,
    StatusSessao,
)
from app.providers.base import ResultadoEnvio
from app.providers.notificacao.console import ConsoleNotificationProvider
from app.providers.video.fake import FakeVideoProvider
from app.services.notificacao_service import NotificacaoService
from app.services.parametros_service import ParametrosService
from app.services.sessao_service import SessaoService
from app.workers import agenda, outbox
from tests import fabricas as f

pytestmark = [pytest.mark.db, pytest.mark.servicos]


def parametros(sessao: AsyncSession, settings: Settings) -> ParametrosService:
    ParametrosService.invalidar_cache()
    return ParametrosService(sessao, settings)


async def cenario_confirmado(sessao: AsyncSession, *, minutos_ate: int = 10):  # type: ignore[no-untyped-def]
    """Um agendamento confirmado começando em ``minutos_ate`` minutos."""
    prof = await f.criar_profissional(sessao)
    pac = await f.criar_paciente(sessao)
    esp = await f.criar_especialidade(sessao)
    await f.adicionar_especialidade(sessao, prof, esp, ordem=1)
    agendamento = await f.criar_agendamento(
        sessao,
        prof,
        pac,
        esp,
        inicio=agora_utc() + timedelta(minutes=minutos_ate),
        status=StatusAgendamento.CONFIRMADO,
    )
    return prof, pac, esp, agendamento


class TestPrepararSalas:
    async def test_cria_sala_de_quem_comeca_em_breve(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """R8 — a sala abre e o link é enfileirado 20 min antes."""
        _, _, _, agendamento = await cenario_confirmado(sessao, minutos_ate=10)

        criadas = await agenda.preparar_salas(
            sessao, parametros(sessao, settings), FakeVideoProvider(settings)
        )
        assert criadas == 1

        sessao_atendimento = await sessao.scalar(
            select(Sessao).where(Sessao.agendamento_id == agendamento.id)
        )
        assert sessao_atendimento is not None
        assert sessao_atendimento.status is StatusSessao.SALA_PRONTA
        assert sessao_atendimento.sala_url
        assert sessao_atendimento.link_enviado_em is not None

    async def test_nao_prepara_sala_de_consulta_distante(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        await cenario_confirmado(sessao, minutos_ate=180)
        criadas = await agenda.preparar_salas(
            sessao, parametros(sessao, settings), FakeVideoProvider(settings)
        )
        assert criadas == 0

    async def test_e_idempotente(self, sessao: AsyncSession, settings: Settings) -> None:
        """Rodar o worker duas vezes não cria sala nem notificação em dobro."""
        await cenario_confirmado(sessao, minutos_ate=10)
        video = FakeVideoProvider(settings)

        assert await agenda.preparar_salas(sessao, parametros(sessao, settings), video) == 1
        assert await agenda.preparar_salas(sessao, parametros(sessao, settings), video) == 0

        total_links = await sessao.scalar(
            select(func.count())
            .select_from(Notificacao)
            .where(Notificacao.template == "link_sessao")
        )
        # Paciente e profissional, e-mail apenas (o demo não tem telefone).
        assert int(total_links or 0) <= 4

    async def test_link_e_agendado_para_t_menos_20(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        _, _, _, agendamento = await cenario_confirmado(sessao, minutos_ate=10)
        await agenda.preparar_salas(
            sessao, parametros(sessao, settings), FakeVideoProvider(settings)
        )
        notificacao = await sessao.scalar(
            select(Notificacao).where(Notificacao.template == "link_sessao").limit(1)
        )
        assert notificacao is not None
        esperado = agendamento.inicio_utc - timedelta(minutes=20)
        assert abs((notificacao.agendada_para - esperado).total_seconds()) < 2


class TestExpiracaoDeReservas:
    async def test_libera_reserva_vencida(self, sessao: AsyncSession, settings: Settings) -> None:
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        esp = await f.criar_especialidade(sessao)
        agendamento = await f.criar_agendamento(
            sessao, prof, pac, esp, status=StatusAgendamento.PENDENTE_PAGAMENTO
        )
        agendamento.reserva_expira_em = agora_utc() - timedelta(minutes=1)
        await sessao.flush()

        assert await agenda.expirar_reservas(sessao, parametros(sessao, settings)) >= 1
        await sessao.refresh(agendamento)
        assert agendamento.status is StatusAgendamento.EXPIRADO


class TestNoShow:
    async def test_paciente_que_nao_entrou_leva_falta(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """R7 — passada a tolerância de 15 min, quem não entrou levou falta."""
        _, _, _, agendamento = await cenario_confirmado(sessao, minutos_ate=-30)
        servico = SessaoService(sessao, parametros(sessao, settings), FakeVideoProvider(settings))
        await servico.preparar_sala(agendamento)

        assert await agenda.marcar_no_show(sessao, parametros(sessao, settings)) == 1
        await sessao.refresh(agendamento)
        assert agendamento.status is StatusAgendamento.NO_SHOW_PACIENTE

    async def test_dentro_da_tolerancia_nao_marca(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        _, _, _, agendamento = await cenario_confirmado(sessao, minutos_ate=-5)
        servico = SessaoService(sessao, parametros(sessao, settings), FakeVideoProvider(settings))
        await servico.preparar_sala(agendamento)

        assert await agenda.marcar_no_show(sessao, parametros(sessao, settings)) == 0
        await sessao.refresh(agendamento)
        assert agendamento.status is StatusAgendamento.CONFIRMADO


class TestOutbox:
    async def test_envia_pendentes(self, sessao: AsyncSession, settings: Settings) -> None:
        usuario = await f.criar_usuario(sessao)
        await NotificacaoService(sessao).enfileirar(
            usuario=usuario,
            canal=CanalNotificacao.EMAIL,
            template="teste",
            contexto={},
            chave_idempotencia=f"teste:{usuario.id}",
        )

        enviadas, falhas = await outbox.enviar_pendentes(sessao, ConsoleNotificationProvider())
        assert (enviadas, falhas) == (1, 0)

    async def test_nao_envia_o_que_ainda_nao_venceu(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        usuario = await f.criar_usuario(sessao)
        await NotificacaoService(sessao).enfileirar(
            usuario=usuario,
            canal=CanalNotificacao.EMAIL,
            template="futuro",
            contexto={},
            chave_idempotencia=f"futuro:{usuario.id}",
            agendada_para=agora_utc() + timedelta(hours=1),
        )
        enviadas, _ = await outbox.enviar_pendentes(sessao, ConsoleNotificationProvider())
        assert enviadas == 0

    async def test_falha_agenda_nova_tentativa_com_espera_crescente(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        usuario = await f.criar_usuario(sessao)
        notificacao = await NotificacaoService(sessao).enfileirar(
            usuario=usuario,
            canal=CanalNotificacao.EMAIL,
            template="falha",
            contexto={},
            chave_idempotencia=f"falha:{usuario.id}",
        )
        assert notificacao is not None

        class ProvedorQueFalha:
            nome = "falha"

            def suporta(self, canal: CanalNotificacao) -> bool:
                return True

            async def enviar(self, *args: object, **kwargs: object) -> ResultadoEnvio:
                return ResultadoEnvio(sucesso=False, erro="provedor fora do ar")

        enviadas, falhas = await outbox.enviar_pendentes(sessao, ProvedorQueFalha())
        assert (enviadas, falhas) == (0, 0)  # ainda vai tentar de novo
        await sessao.refresh(notificacao)
        assert notificacao.status is StatusNotificacao.PENDENTE
        assert notificacao.tentativas == 1
        assert notificacao.proxima_tentativa_em is not None
        assert "fora do ar" in (notificacao.erro or "")

    async def test_desiste_apos_o_maximo_de_tentativas(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """A linha vira FALHA e **fica** — some do radar seria pior."""
        usuario = await f.criar_usuario(sessao)
        notificacao = await NotificacaoService(sessao).enfileirar(
            usuario=usuario,
            canal=CanalNotificacao.EMAIL,
            template="desiste",
            contexto={},
            chave_idempotencia=f"desiste:{usuario.id}",
        )
        assert notificacao is not None
        notificacao.tentativas = outbox.MAX_TENTATIVAS - 1
        await sessao.flush()

        class ProvedorQueFalha:
            nome = "falha"

            def suporta(self, canal: CanalNotificacao) -> bool:
                return True

            async def enviar(self, *args: object, **kwargs: object) -> ResultadoEnvio:
                return ResultadoEnvio(sucesso=False, erro="erro")

        _, falhas = await outbox.enviar_pendentes(sessao, ProvedorQueFalha())
        assert falhas == 1
        await sessao.refresh(notificacao)
        assert notificacao.status is StatusNotificacao.FALHA

    async def test_provedor_que_levanta_excecao_nao_derruba_o_lote(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        usuario = await f.criar_usuario(sessao)
        servico = NotificacaoService(sessao)
        for i in range(2):
            await servico.enfileirar(
                usuario=usuario,
                canal=CanalNotificacao.EMAIL,
                template="lote",
                contexto={},
                chave_idempotencia=f"lote:{usuario.id}:{i}",
            )

        class ProvedorQueExplode:
            nome = "explode"

            def suporta(self, canal: CanalNotificacao) -> bool:
                return True

            async def enviar(self, *args: object, **kwargs: object) -> ResultadoEnvio:
                raise RuntimeError("conexão recusada")

        # Não levanta: o erro fica registrado na linha, e as duas continuam
        # pendentes para nova tentativa.
        enviadas, _ = await outbox.enviar_pendentes(sessao, ProvedorQueExplode())
        assert enviadas == 0

        linhas = list(
            (await sessao.execute(select(Notificacao).where(Notificacao.template == "lote")))
            .scalars()
            .all()
        )
        assert len(linhas) == 2
        assert all(n.status is StatusNotificacao.PENDENTE for n in linhas)
        assert all("conexão recusada" in (n.erro or "") for n in linhas)


class TestIdempotenciaDaOutbox:
    async def test_mesma_chave_nao_duplica(self, sessao: AsyncSession, settings: Settings) -> None:
        usuario = await f.criar_usuario(sessao)
        servico = NotificacaoService(sessao)
        chave = f"unica:{usuario.id}"

        primeira = await servico.enfileirar(
            usuario=usuario,
            canal=CanalNotificacao.EMAIL,
            template="t",
            contexto={},
            chave_idempotencia=chave,
        )
        segunda = await servico.enfileirar(
            usuario=usuario,
            canal=CanalNotificacao.EMAIL,
            template="t",
            contexto={},
            chave_idempotencia=chave,
        )
        assert primeira is not None
        assert segunda is None

    async def test_sem_telefone_o_whatsapp_e_pulado(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Ausência de telefone não é erro — é ausência de dado."""
        usuario = await f.criar_usuario(sessao)
        assert usuario.telefone_e164 is None
        criada = await NotificacaoService(sessao).enfileirar(
            usuario=usuario,
            canal=CanalNotificacao.WHATSAPP,
            template="t",
            contexto={},
            chave_idempotencia=f"zap:{usuario.id}",
        )
        assert criada is None
