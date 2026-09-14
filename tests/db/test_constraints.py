"""As constraints são regra de negócio — logo, precisam de teste.

Cada teste aqui prova que a regra vale **mesmo se a aplicação errar**: são
INSERTs diretos, sem passar por service nenhum.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tempo import agora_utc
from app.models import (
    Conselho,
    DisponibilidadeRecorrente,
    Papel,
    PerfilProfissional,
    StatusAgendamento,
)
from app.models.perfil import ProfissionalEspecialidade
from tests import fabricas as f

pytestmark = [pytest.mark.db]


class TestLimiteEspecialidades:
    """R4 — máximo de 5 especialidades, garantido pelo banco."""

    async def test_permite_cinco(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        for ordem in range(1, 6):
            esp = await f.criar_especialidade(sessao)
            await f.adicionar_especialidade(sessao, prof, esp, ordem=ordem)
        # Chegou aqui sem erro: 5 é permitido.

    async def test_bloqueia_a_sexta(self, sessao: AsyncSession) -> None:
        """A sexta é impossível porque `ordem` só aceita 1..5.

        Não há como um bug na aplicação, nem um INSERT manual, furar isso.
        """
        prof = await f.criar_profissional(sessao)
        for ordem in range(1, 6):
            esp = await f.criar_especialidade(sessao)
            await f.adicionar_especialidade(sessao, prof, esp, ordem=ordem)

        esp6 = await f.criar_especialidade(sessao)
        with pytest.raises(IntegrityError, match="ordem_1_5"):
            await f.adicionar_especialidade(sessao, prof, esp6, ordem=6)

    async def test_bloqueia_ordem_duplicada(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        esp1 = await f.criar_especialidade(sessao)
        esp2 = await f.criar_especialidade(sessao)
        await f.adicionar_especialidade(sessao, prof, esp1, ordem=1)
        with pytest.raises(IntegrityError, match="uq_profissional_ordem"):
            await f.adicionar_especialidade(sessao, prof, esp2, ordem=1)

    async def test_faixa_de_preco_precisa_ser_coerente(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        esp = await f.criar_especialidade(sessao)
        with pytest.raises(IntegrityError, match="faixa_preco_coerente"):
            sessao.add(
                ProfissionalEspecialidade(
                    profissional_id=prof.usuario_id,
                    especialidade_id=esp.id,
                    ordem=1,
                    preco_min_centavos=20000,
                    preco_padrao_centavos=15000,  # menor que o mínimo
                    preco_max_centavos=25000,
                )
            )
            await sessao.flush()


class TestDescricao:
    """R5 — descrição de no máximo 500 caracteres."""

    async def test_aceita_exatamente_500(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao, descricao="x" * 500)
        assert prof.descricao is not None
        assert len(prof.descricao) == 500

    async def test_rejeita_501(self, sessao: AsyncSession) -> None:
        usuario = await f.criar_usuario(sessao, papel=Papel.PROFISSIONAL)
        with pytest.raises((IntegrityError, Exception)):
            sessao.add(
                PerfilProfissional(
                    usuario_id=usuario.id,
                    nome_exibicao="Teste",
                    conselho=Conselho.CRP,
                    registro_numero="123456",
                    registro_uf="SP",
                    descricao="x" * 501,
                )
            )
            await sessao.flush()


class TestDisponibilidadeSemSobreposicao:
    """R6a — a EXCLUDE impede janelas sobrepostas no mesmo dia."""

    async def test_janelas_adjacentes_sao_permitidas(self, sessao: AsyncSession) -> None:
        """09:00-12:00 e 12:00-18:00 não se sobrepõem: o range é '[)'."""
        prof = await f.criar_profissional(sessao)
        await f.criar_disponibilidade(sessao, prof, dia_semana=1, inicio_min=540, fim_min=720)
        await f.criar_disponibilidade(sessao, prof, dia_semana=1, inicio_min=720, fim_min=1080)

    async def test_bloqueia_sobreposicao(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        await f.criar_disponibilidade(sessao, prof, dia_semana=1, inicio_min=540, fim_min=720)
        with pytest.raises(IntegrityError, match="ex_disponibilidade_sem_sobreposicao"):
            # 11:00-13:00 invade a janela das 09:00-12:00.
            await f.criar_disponibilidade(sessao, prof, dia_semana=1, inicio_min=660, fim_min=780)

    async def test_mesma_janela_em_dias_diferentes_e_permitida(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        await f.criar_disponibilidade(sessao, prof, dia_semana=1, inicio_min=540, fim_min=720)
        await f.criar_disponibilidade(sessao, prof, dia_semana=2, inicio_min=540, fim_min=720)

    async def test_inativa_nao_conta_para_a_constraint(self, sessao: AsyncSession) -> None:
        """A EXCLUDE tem `WHERE (ativo)`: desativar libera o horário."""
        prof = await f.criar_profissional(sessao)
        disp = await f.criar_disponibilidade(
            sessao, prof, dia_semana=1, inicio_min=540, fim_min=720
        )
        disp.ativo = False
        await sessao.flush()
        await f.criar_disponibilidade(sessao, prof, dia_semana=1, inicio_min=600, fim_min=780)

    async def test_rejeita_janela_invertida(self, sessao: AsyncSession) -> None:
        prof = await f.criar_profissional(sessao)
        with pytest.raises(IntegrityError, match="janela_valida"):
            sessao.add(
                DisponibilidadeRecorrente(
                    profissional_id=prof.usuario_id,
                    dia_semana=1,
                    inicio_min=720,
                    fim_min=540,  # fim antes do início
                    vigencia_inicio=agora_utc().date(),
                )
            )
            await sessao.flush()


class TestAgendamentoSemSobreposicao:
    """R6b/c — nem profissional nem paciente ficam com dois compromissos."""

    async def _cenario(self, sessao: AsyncSession):  # type: ignore[no-untyped-def]
        prof = await f.criar_profissional(sessao)
        pac = await f.criar_paciente(sessao)
        esp = await f.criar_especialidade(sessao)
        return prof, pac, esp

    async def test_bloqueia_dois_pacientes_no_mesmo_horario(self, sessao: AsyncSession) -> None:
        prof, pac1, esp = await self._cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        inicio = agora_utc() + timedelta(days=1)

        await f.criar_agendamento(sessao, prof, pac1, esp, inicio=inicio)
        with pytest.raises(IntegrityError, match="ex_agendamento_profissional"):
            await f.criar_agendamento(sessao, prof, pac2, esp, inicio=inicio)

    async def test_bloqueia_paciente_com_dois_profissionais_no_mesmo_horario(
        self, sessao: AsyncSession
    ) -> None:
        prof1, pac, esp = await self._cenario(sessao)
        prof2 = await f.criar_profissional(sessao)
        inicio = agora_utc() + timedelta(days=1)

        await f.criar_agendamento(sessao, prof1, pac, esp, inicio=inicio)
        with pytest.raises(IntegrityError, match="ex_agendamento_paciente"):
            await f.criar_agendamento(sessao, prof2, pac, esp, inicio=inicio)

    async def test_sessoes_consecutivas_sao_permitidas(self, sessao: AsyncSession) -> None:
        """Fim de uma == início da outra não é sobreposição (range '[)')."""
        prof, pac1, esp = await self._cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        inicio = agora_utc() + timedelta(days=1)

        await f.criar_agendamento(sessao, prof, pac1, esp, inicio=inicio, duracao_min=50)
        await f.criar_agendamento(
            sessao, prof, pac2, esp, inicio=inicio + timedelta(minutes=50), duracao_min=50
        )

    async def test_pendente_pagamento_trava_o_slot(self, sessao: AsyncSession) -> None:
        """O ponto sutil do fluxo escolhido.

        Como o paciente escolhe o horário ANTES de pagar, PENDENTE_PAGAMENTO
        precisa ocupar a agenda -- senão dois pacientes pagariam pelo mesmo
        horário e um deles ficaria sem sessão.
        """
        prof, pac1, esp = await self._cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        inicio = agora_utc() + timedelta(days=1)

        await f.criar_agendamento(
            sessao, prof, pac1, esp, inicio=inicio, status=StatusAgendamento.PENDENTE_PAGAMENTO
        )
        with pytest.raises(IntegrityError, match="ex_agendamento_profissional"):
            await f.criar_agendamento(sessao, prof, pac2, esp, inicio=inicio)

    async def test_cancelado_libera_o_slot(self, sessao: AsyncSession) -> None:
        prof, pac1, esp = await self._cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        inicio = agora_utc() + timedelta(days=1)

        await f.criar_agendamento(
            sessao, prof, pac1, esp, inicio=inicio, status=StatusAgendamento.CANCELADO_PACIENTE
        )
        # Cancelado não ocupa: o horário volta ao mercado.
        await f.criar_agendamento(sessao, prof, pac2, esp, inicio=inicio)

    async def test_expirado_libera_o_slot(self, sessao: AsyncSession) -> None:
        """É o que o worker faz quando a reserva vence sem pagamento."""
        prof, pac1, esp = await self._cenario(sessao)
        pac2 = await f.criar_paciente(sessao)
        inicio = agora_utc() + timedelta(days=1)

        await f.criar_agendamento(
            sessao, prof, pac1, esp, inicio=inicio, status=StatusAgendamento.EXPIRADO
        )
        await f.criar_agendamento(sessao, prof, pac2, esp, inicio=inicio)


class TestEmailCaseInsensitive:
    async def test_email_e_citext(self, sessao: AsyncSession) -> None:
        """ "Joao@x.com" e "joao@x.com" não podem virar duas contas."""
        await f.criar_usuario(sessao, email="Joao.Silva@Teste.BR")
        with pytest.raises(IntegrityError, match=r"usuarios_email|uq_usuarios_email"):
            await f.criar_usuario(sessao, email="joao.silva@teste.br")


class TestRegistroConselho:
    async def test_registro_e_unico_por_conselho_e_uf(self, sessao: AsyncSession) -> None:
        """Dois profissionais não podem reivindicar o mesmo CRP."""
        # Número gerado, não fixo: "123456" pertence ao profissional do seed de
        # demonstração, e um valor fixo faria o teste depender do banco estar limpo.
        registro = uuid.uuid4().hex[:10]
        await f.criar_profissional(sessao, registro_numero=registro)
        with pytest.raises(IntegrityError, match="uq_registro_conselho"):
            await f.criar_profissional(sessao, registro_numero=registro)
