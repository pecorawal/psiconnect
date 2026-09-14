"""Construtores de entidades para testes.

Builders explícitos em vez de factory_boy: num domínio com tantas invariantes
(faixa de preço coerente, ordem 1..5, janelas válidas), ser explícito sobre o que
o teste precisa é mais legível do que herdar defaults mágicos.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tempo import agora_utc
from app.models import (
    Agendamento,
    Conselho,
    DisponibilidadeRecorrente,
    Especialidade,
    Papel,
    PerfilPaciente,
    PerfilProfissional,
    Sintoma,
    SintomaEspecialidade,
    StatusAgendamento,
    StatusCadastro,
    Usuario,
)
from app.models.perfil import ProfissionalEspecialidade

SENHA_HASH_FAKE = "$argon2id$v=19$m=65536,t=3,p=4$fake"


def _sufixo() -> str:
    return uuid.uuid4().hex[:8]


async def criar_usuario(
    sessao: AsyncSession,
    *,
    papel: Papel = Papel.PACIENTE,
    email: str | None = None,
    nome: str = "Fulano de Tal",
    timezone: str = "America/Sao_Paulo",
) -> Usuario:
    usuario = Usuario(
        email=email or f"{papel.value.lower()}-{_sufixo()}@teste.br",
        senha_hash=SENHA_HASH_FAKE,
        papel=papel,
        nome_completo=nome,
        timezone=timezone,
    )
    sessao.add(usuario)
    await sessao.flush()
    return usuario


async def criar_especialidade(
    sessao: AsyncSession, *, slug: str | None = None, nome: str = "Ansiedade"
) -> Especialidade:
    esp = Especialidade(slug=slug or f"esp-{_sufixo()}", nome=nome)
    sessao.add(esp)
    await sessao.flush()
    return esp


async def criar_sintoma(
    sessao: AsyncSession,
    *,
    slug: str | None = None,
    nome: str = "Insônia",
    descricao_leiga: str = "Não consigo dormir",
    bandeira_risco: bool = False,
) -> Sintoma:
    sintoma = Sintoma(
        slug=slug or f"sin-{_sufixo()}",
        nome=nome,
        descricao_leiga=descricao_leiga,
        bandeira_risco=bandeira_risco,
    )
    sessao.add(sintoma)
    await sessao.flush()
    return sintoma


async def ligar_sintoma_especialidade(
    sessao: AsyncSession, sintoma: Sintoma, especialidade: Especialidade, peso: int = 3
) -> SintomaEspecialidade:
    ligacao = SintomaEspecialidade(
        sintoma_id=sintoma.id, especialidade_id=especialidade.id, peso=peso
    )
    sessao.add(ligacao)
    await sessao.flush()
    return ligacao


async def criar_profissional(
    sessao: AsyncSession,
    *,
    nome: str = "Dra. Ana Souza",
    descricao: str | None = "Atendo adultos com foco em ansiedade.",
    registro_numero: str | None = None,
    aprovado: bool = True,
    email: str | None = None,
) -> PerfilProfissional:
    usuario = await criar_usuario(sessao, papel=Papel.PROFISSIONAL, nome=nome, email=email)
    perfil = PerfilProfissional(
        usuario_id=usuario.id,
        nome_exibicao=nome,
        conselho=Conselho.CRP,
        registro_numero=registro_numero or _sufixo(),
        registro_uf="SP",
        descricao=descricao,
        status_cadastro=StatusCadastro.APROVADO if aprovado else StatusCadastro.RASCUNHO,
    )
    sessao.add(perfil)
    await sessao.flush()
    return perfil


async def adicionar_especialidade(
    sessao: AsyncSession,
    profissional: PerfilProfissional,
    especialidade: Especialidade,
    *,
    ordem: int,
    preco_centavos: int = 15000,
) -> ProfissionalEspecialidade:
    ligacao = ProfissionalEspecialidade(
        profissional_id=profissional.usuario_id,
        especialidade_id=especialidade.id,
        ordem=ordem,
        preco_min_centavos=preco_centavos,
        preco_padrao_centavos=preco_centavos,
        preco_max_centavos=preco_centavos,
    )
    sessao.add(ligacao)
    await sessao.flush()
    return ligacao


async def criar_paciente(
    sessao: AsyncSession, *, nome: str = "João da Silva", email: str | None = None
) -> PerfilPaciente:
    usuario = await criar_usuario(sessao, papel=Papel.PACIENTE, nome=nome, email=email)
    perfil = PerfilPaciente(usuario_id=usuario.id)
    sessao.add(perfil)
    await sessao.flush()
    return perfil


async def criar_disponibilidade(
    sessao: AsyncSession,
    profissional: PerfilProfissional,
    *,
    dia_semana: int = 1,
    inicio_min: int = 540,  # 09:00
    fim_min: int = 720,  # 12:00
    vigencia_inicio: date | None = None,
) -> DisponibilidadeRecorrente:
    disp = DisponibilidadeRecorrente(
        profissional_id=profissional.usuario_id,
        dia_semana=dia_semana,
        inicio_min=inicio_min,
        fim_min=fim_min,
        vigencia_inicio=vigencia_inicio or date(2020, 1, 1),
    )
    sessao.add(disp)
    await sessao.flush()
    return disp


async def criar_agendamento(
    sessao: AsyncSession,
    profissional: PerfilProfissional,
    paciente: PerfilPaciente,
    especialidade: Especialidade,
    *,
    inicio: datetime | None = None,
    duracao_min: int = 50,
    status: StatusAgendamento = StatusAgendamento.CONFIRMADO,
    valor_centavos: int = 15000,
) -> Agendamento:
    inicio = inicio or (agora_utc() + timedelta(days=1))
    agendamento = Agendamento(
        paciente_id=paciente.usuario_id,
        profissional_id=profissional.usuario_id,
        especialidade_id=especialidade.id,
        inicio_utc=inicio,
        fim_utc=inicio + timedelta(minutes=duracao_min),
        duracao_min=duracao_min,
        status=status,
        valor_centavos=valor_centavos,
    )
    sessao.add(agendamento)
    await sessao.flush()
    return agendamento
