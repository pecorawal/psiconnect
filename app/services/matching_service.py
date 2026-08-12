"""Matching: dos sintomas do paciente aos profissionais.

O paciente não chega dizendo "quero TCC para TAG" — chega dizendo "não consigo
dormir" e "meu coração dispara". O caminho é:

    sintomas relatados
      → especialidades, via SintomaEspecialidade.peso (1..5)
      → profissionais que atendem essas especialidades
      → ranqueados por aderência

A pontuação é intencionalmente simples e explicável. Um profissional que atende
a especialidade mais indicada para o sintoma mais intenso do paciente aparece
primeiro; ninguém precisa de um modelo para justificar isso.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tempo import agora_utc
from app.models import (
    Especialidade,
    PacienteSintoma,
    PerfilPaciente,
    PerfilProfissional,
    Sintoma,
    SintomaEspecialidade,
    StatusCadastro,
)
from app.models.perfil import ProfissionalEspecialidade


@dataclass(frozen=True, slots=True)
class ProfissionalRankeado:
    perfil: PerfilProfissional
    pontuacao: float
    #: Especialidades do profissional que casam com os sintomas relatados.
    especialidades_casadas: list[Especialidade] = field(default_factory=list)
    preco_a_partir_de_centavos: int = 0

    @property
    def aderencia_percentual(self) -> int:
        """Para a barra de "compatibilidade" na UI."""
        return min(100, round(self.pontuacao))


class MatchingService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    # --- Sintomas do paciente ----------------------------------------------

    async def listar_sintomas(self) -> list[Sintoma]:
        return list(
            (
                await self.sessao.execute(
                    select(Sintoma)
                    .where(Sintoma.ativo.is_(True))
                    .order_by(Sintoma.categoria, Sintoma.ordem_exibicao)
                )
            )
            .scalars()
            .all()
        )

    async def sintomas_do_paciente(self, paciente_id: uuid.UUID) -> list[PacienteSintoma]:
        return list(
            (
                await self.sessao.execute(
                    select(PacienteSintoma).where(PacienteSintoma.paciente_id == paciente_id)
                )
            )
            .scalars()
            .all()
        )

    async def registrar_sintomas(
        self, paciente: PerfilPaciente, sintomas: dict[uuid.UUID, int]
    ) -> list[PacienteSintoma]:
        """Substitui os sintomas relatados.

        **Dado sensível de saúde (LGPD art. 11)**: gravado só com consentimento
        específico, dado no cadastro, e apagável pelo titular.
        """
        atuais = await self.sintomas_do_paciente(paciente.usuario_id)
        for registro in atuais:
            await self.sessao.delete(registro)
        await self.sessao.flush()

        agora = agora_utc()
        novos = [
            PacienteSintoma(
                paciente_id=paciente.usuario_id,
                sintoma_id=sintoma_id,
                intensidade=max(1, min(5, intensidade)),
                registrado_em=agora,
            )
            for sintoma_id, intensidade in sintomas.items()
        ]
        self.sessao.add_all(novos)
        await self.sessao.flush()
        return novos

    async def tem_sintoma_de_risco(self, paciente_id: uuid.UUID) -> bool:
        """Dispara o protocolo de crise: destaque do CVV e (Fase 3) alerta ao
        profissional."""
        encontrado = await self.sessao.scalar(
            select(PacienteSintoma.sintoma_id)
            .join(Sintoma, PacienteSintoma.sintoma_id == Sintoma.id)
            .where(
                PacienteSintoma.paciente_id == paciente_id,
                Sintoma.bandeira_risco.is_(True),
            )
            .limit(1)
        )
        return encontrado is not None

    # --- Ranking ------------------------------------------------------------

    async def especialidades_indicadas(self, paciente_id: uuid.UUID) -> dict[uuid.UUID, float]:
        """``{especialidade_id: relevância}`` a partir dos sintomas relatados.

        A relevância soma ``peso × intensidade`` sobre todos os sintomas: uma
        especialidade muito indicada (peso 5) para um sintoma muito intenso
        (intensidade 5) pesa 25; um casamento fraco e leve pesa 1.
        """
        linhas = (
            await self.sessao.execute(
                select(
                    SintomaEspecialidade.especialidade_id,
                    func.sum(SintomaEspecialidade.peso * PacienteSintoma.intensidade),
                )
                .join(
                    PacienteSintoma,
                    PacienteSintoma.sintoma_id == SintomaEspecialidade.sintoma_id,
                )
                .where(PacienteSintoma.paciente_id == paciente_id)
                .group_by(SintomaEspecialidade.especialidade_id)
            )
        ).all()
        return {esp_id: float(peso or 0) for esp_id, peso in linhas}

    async def profissionais_para(
        self, paciente_id: uuid.UUID, *, limite: int = 20
    ) -> list[ProfissionalRankeado]:
        indicadas = await self.especialidades_indicadas(paciente_id)
        if not indicadas:
            return []

        maximo = max(indicadas.values()) or 1.0

        linhas = (
            await self.sessao.execute(
                select(ProfissionalEspecialidade, PerfilProfissional, Especialidade)
                .join(
                    PerfilProfissional,
                    ProfissionalEspecialidade.profissional_id == PerfilProfissional.usuario_id,
                )
                .join(
                    Especialidade,
                    ProfissionalEspecialidade.especialidade_id == Especialidade.id,
                )
                .where(
                    ProfissionalEspecialidade.especialidade_id.in_(indicadas.keys()),
                    ProfissionalEspecialidade.ativo.is_(True),
                    # Só quem passou pela verificação de registro no conselho
                    # aparece para o paciente.
                    PerfilProfissional.status_cadastro == StatusCadastro.APROVADO,
                )
            )
        ).all()

        agregado: dict[uuid.UUID, ProfissionalRankeado] = {}
        for ligacao, perfil, especialidade in linhas:
            relevancia = indicadas.get(ligacao.especialidade_id, 0.0)
            # Normaliza para 0..100 pela especialidade mais relevante do paciente.
            contribuicao = (relevancia / maximo) * 100

            atual = agregado.get(perfil.usuario_id)
            if atual is None:
                agregado[perfil.usuario_id] = ProfissionalRankeado(
                    perfil=perfil,
                    pontuacao=contribuicao,
                    especialidades_casadas=[especialidade],
                    preco_a_partir_de_centavos=ligacao.preco_padrao_centavos,
                )
            else:
                agregado[perfil.usuario_id] = ProfissionalRankeado(
                    perfil=atual.perfil,
                    # Casamentos adicionais contam menos: atender 5 especialidades
                    # relacionadas não deve superar quem é muito forte na
                    # principal.
                    pontuacao=atual.pontuacao + contribuicao * 0.25,
                    especialidades_casadas=[*atual.especialidades_casadas, especialidade],
                    preco_a_partir_de_centavos=min(
                        atual.preco_a_partir_de_centavos, ligacao.preco_padrao_centavos
                    ),
                )

        ranking = sorted(agregado.values(), key=lambda r: (-r.pontuacao, r.perfil.nome_exibicao))
        return ranking[:limite]

    async def buscar_profissional(self, profissional_id: uuid.UUID) -> PerfilProfissional | None:
        return await self.sessao.get(PerfilProfissional, profissional_id)

    async def melhor_especialidade(
        self, paciente_id: uuid.UUID, profissional_id: uuid.UUID
    ) -> ProfissionalEspecialidade | None:
        """Qual especialidade do profissional melhor atende este paciente.

        Marcar sempre "a primeira especialidade do profissional" seria errado:
        alguém que atende ansiedade, luto e casal deve receber a consulta na
        área que corresponde ao que o paciente relatou — é isso que define o
        preço cobrado e o que aparece no registro do atendimento.
        """
        indicadas = await self.especialidades_indicadas(paciente_id)

        ofertadas = list(
            (
                await self.sessao.execute(
                    select(ProfissionalEspecialidade).where(
                        ProfissionalEspecialidade.profissional_id == profissional_id,
                        ProfissionalEspecialidade.ativo.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not ofertadas:
            return None

        # Sem sintomas relatados (ou sem interseção), cai na especialidade
        # principal do profissional -- a de menor `ordem`.
        return max(
            ofertadas,
            key=lambda oe: (indicadas.get(oe.especialidade_id, 0.0), -oe.ordem),
        )
