"""Cadastro de menor com consentimento do responsável (LGPD art. 14)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.cripto import gerar_chave_base64
from app.core.erros import NaoEncontrado
from app.core.tempo import agora_utc
from app.models import (
    Papel,
    StatusPaciente,
    TipoDocumentoResponsavel,
    Usuario,
    VerificacaoResponsavel,
)
from app.services.responsavel_service import (
    ConviteInvalido,
    DadosMenor,
    DadosResponsavel,
    IdadeNaoAtendida,
    ResponsavelInvalido,
    ResponsavelService,
    eh_menor,
)
from tests import fabricas as f

pytestmark = [pytest.mark.db, pytest.mark.servicos]


class ArmazenamentoFalso:
    """Guarda em memória, para não depender do MinIO nos testes de serviço."""

    nome = "falso"

    def __init__(self) -> None:
        self.objetos: dict[str, bytes] = {}

    async def salvar(self, caminho: str, conteudo: bytes, content_type: str) -> str:
        self.objetos[caminho] = conteudo
        return caminho

    async def ler(self, caminho: str) -> bytes:
        if caminho not in self.objetos:
            raise NaoEncontrado("Arquivo não encontrado.")
        return self.objetos[caminho]

    async def remover(self, caminho: str) -> None:
        self.objetos.pop(caminho, None)

    async def url_temporaria(self, caminho: str, ttl_segundos: int = 60) -> str:
        return f"memoria://{caminho}"


def servico(sessao: AsyncSession, settings: Settings) -> ResponsavelService:
    # Chave própria: o .env de teste pode não ter uma configurada.
    ajustado = settings.model_copy(update={"chave_cripto_transcricao": gerar_chave_base64()})
    return ResponsavelService(sessao, ajustado, ArmazenamentoFalso())


def nascimento_com(idade: int) -> date:
    hoje = agora_utc().date()
    return date(hoje.year - idade, hoje.month, min(hoje.day, 28))


class TestDeteccaoDeIdade:
    def test_menor_e_maior(self) -> None:
        assert eh_menor(nascimento_com(15))
        assert not eh_menor(nascimento_com(25))

    def test_no_dia_do_aniversario_de_18_ja_e_maior(self) -> None:
        hoje = agora_utc().date()
        assert not eh_menor(date(hoje.year - 18, hoje.month, hoje.day))


class TestCaminhoA:
    """O menor se cadastra e fica pendente até o responsável confirmar."""

    async def test_abre_pendencia_e_bloqueia_o_menor(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        paciente = await f.criar_paciente(sessao)
        paciente.data_nascimento = nascimento_com(15)
        await sessao.flush()

        verificacao, token = await servico(sessao, settings).abrir_pendencia(
            paciente, DadosResponsavel(nome="Maria Mãe", email="mae@teste.br")
        )

        assert paciente.status is StatusPaciente.PENDENTE_RESPONSAVEL
        assert not paciente.pode_agendar, "menor pendente não pode agendar"
        assert verificacao.pendente
        assert token  # devolvido em claro, só o hash é persistido
        assert verificacao.token_hash != token

    async def test_exige_algum_contato_do_responsavel(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        paciente = await f.criar_paciente(sessao)
        with pytest.raises(ResponsavelInvalido):
            await servico(sessao, settings).abrir_pendencia(
                paciente, DadosResponsavel(nome="Sem Contato")
            )

    async def test_novo_convite_invalida_o_anterior(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        paciente = await f.criar_paciente(sessao)
        s = servico(sessao, settings)
        _, token_antigo = await s.abrir_pendencia(
            paciente, DadosResponsavel(nome="Maria", email="m@teste.br")
        )
        await s.abrir_pendencia(paciente, DadosResponsavel(nome="Maria", email="m@teste.br"))

        with pytest.raises(ConviteInvalido):
            await s.buscar_por_token(token_antigo)

    async def test_confirmacao_libera_o_cadastro(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        paciente = await f.criar_paciente(sessao)
        paciente.data_nascimento = nascimento_com(16)
        await sessao.flush()

        s = servico(sessao, settings)
        _, token = await s.abrir_pendencia(
            paciente, DadosResponsavel(nome="Maria Mãe", email="mae@teste.br")
        )

        liberado = await s.confirmar(
            token,
            documento=b"imagem-do-rg",
            documento_tipo=TipoDocumentoResponsavel.RG,
            content_type="image/jpeg",
            documento_numero="12.345.678-9",
            ip="203.0.113.7",
        )

        assert liberado.status is StatusPaciente.ATIVO
        assert liberado.pode_agendar
        assert liberado.responsavel_confirmado_em is not None

    async def test_documento_vai_cifrado_para_o_storage(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """O arquivo chega ao storage já ilegível — nem quem opera o MinIO abre."""
        paciente = await f.criar_paciente(sessao)
        s = servico(sessao, settings)
        _, token = await s.abrir_pendencia(
            paciente, DadosResponsavel(nome="Maria", email="m@teste.br")
        )
        original = b"conteudo sensivel do RG"
        await s.confirmar(
            token,
            documento=original,
            documento_tipo=TipoDocumentoResponsavel.RG,
            content_type="image/jpeg",
            documento_numero="123456789",
        )

        armazenamento = s.armazenamento
        assert armazenamento is not None
        guardado = next(iter(armazenamento.objetos.values()))  # type: ignore[attr-defined]
        assert original not in guardado, "o documento não pode ir em claro"

        verificacao = await sessao.scalar(
            select(VerificacaoResponsavel).where(
                VerificacaoResponsavel.paciente_id == paciente.usuario_id
            )
        )
        assert verificacao is not None
        assert await s.ler_documento(verificacao) == original

    async def test_token_invalido(self, sessao: AsyncSession, settings: Settings) -> None:
        with pytest.raises(ConviteInvalido):
            await servico(sessao, settings).buscar_por_token("token-inventado")

    async def test_convite_vencido(self, sessao: AsyncSession, settings: Settings) -> None:
        paciente = await f.criar_paciente(sessao)
        s = servico(sessao, settings)
        verificacao, token = await s.abrir_pendencia(
            paciente, DadosResponsavel(nome="Maria", email="m@teste.br")
        )
        verificacao.expira_em = agora_utc() - timedelta(minutes=1)
        await sessao.flush()

        with pytest.raises(ConviteInvalido):
            await s.buscar_por_token(token)


class TestCaminhoB:
    """O responsável cadastra o menor, consentindo no ato."""

    async def test_menor_nasce_ativo(self, sessao: AsyncSession, settings: Settings) -> None:
        responsavel = await f.criar_usuario(sessao, papel=Papel.PACIENTE, nome="Maria Mãe")
        menor = await servico(sessao, settings).cadastrar_menor(
            responsavel,
            DadosMenor(
                nome_completo="Pedro Filho",
                data_nascimento=nascimento_com(15),
                email="pedro@teste.br",
                senha="senha-forte-2026",
            ),
            parentesco="mãe",
        )
        assert menor.status is StatusPaciente.ATIVO
        assert menor.pode_agendar
        assert menor.responsavel_usuario_id == responsavel.id

    async def test_recusa_maior_de_idade(self, sessao: AsyncSession, settings: Settings) -> None:
        """Adulto cria a própria conta — não vira dependente de ninguém."""
        responsavel = await f.criar_usuario(sessao, papel=Papel.PACIENTE)
        with pytest.raises(ResponsavelInvalido):
            await servico(sessao, settings).cadastrar_menor(
                responsavel,
                DadosMenor(
                    nome_completo="Adulto",
                    data_nascimento=nascimento_com(30),
                    email="adulto@teste.br",
                    senha="senha-forte-2026",
                ),
            )

    async def test_recusa_crianca_abaixo_da_idade_atendida(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Psicoterapia infantil tem exigências próprias que a Fase 1 não cobre."""
        responsavel = await f.criar_usuario(sessao, papel=Papel.PACIENTE)
        with pytest.raises(IdadeNaoAtendida):
            await servico(sessao, settings).cadastrar_menor(
                responsavel,
                DadosMenor(
                    nome_completo="Criança",
                    data_nascimento=nascimento_com(7),
                    email="crianca@teste.br",
                    senha="senha-forte-2026",
                ),
            )


class TestHigieneDeDados:
    async def test_pendencia_vencida_apaga_o_cadastro(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Sem consentimento não há base legal para guardar dado de menor —
        então o certo é remover, não arquivar."""
        paciente = await f.criar_paciente(sessao)
        usuario_id = paciente.usuario_id
        s = servico(sessao, settings)
        verificacao, _ = await s.abrir_pendencia(
            paciente, DadosResponsavel(nome="Maria", email="m@teste.br")
        )
        verificacao.expira_em = agora_utc() - timedelta(days=1)
        await sessao.flush()

        removidos = await s.expirar_pendencias()
        assert usuario_id in removidos
        assert await sessao.get(Usuario, usuario_id) is None

    async def test_pendencia_no_prazo_nao_e_removida(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        paciente = await f.criar_paciente(sessao)
        s = servico(sessao, settings)
        await s.abrir_pendencia(paciente, DadosResponsavel(nome="Maria", email="m@teste.br"))
        assert await s.expirar_pendencias() == []

    async def test_documento_e_apagado_apos_a_verificacao(
        self, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Cumprida a função de provar o consentimento, a imagem vai embora —
        fica só o registro de que houve verificação."""
        paciente = await f.criar_paciente(sessao)
        s = servico(sessao, settings)
        _, token = await s.abrir_pendencia(
            paciente, DadosResponsavel(nome="Maria", email="m@teste.br")
        )
        await s.confirmar(
            token,
            documento=b"rg",
            documento_tipo=TipoDocumentoResponsavel.RG,
            content_type="image/jpeg",
            documento_numero="12345678",
        )

        verificacao = await sessao.scalar(
            select(VerificacaoResponsavel).where(
                VerificacaoResponsavel.paciente_id == paciente.usuario_id
            )
        )
        assert verificacao is not None
        verificacao.confirmado_em = agora_utc() - timedelta(days=60)
        await sessao.flush()

        assert await s.remover_documentos_verificados(apos_dias=30) == 1
        await sessao.refresh(verificacao)
        assert verificacao.documento_chave is None
        assert verificacao.documento_removido_em is not None
        # O rastro permanece conferível.
        assert verificacao.documento_tipo is TipoDocumentoResponsavel.RG
        assert verificacao.documento_final == "5678"
