"""Taxonomia de especialidades.

O requisito original diz que "no site devem constar todas as especialidades de
trabalho de um psicólogo" e que o profissional escolhe até 5 nas quais tem
domínio. Esta lista cobre as áreas mais procuradas; o admin pode ampliá-la sem
deploy.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Especialidade


class Def(NamedTuple):
    slug: str
    nome: str
    categoria: str
    descricao: str


ESPECIALIDADES: tuple[Def, ...] = (
    # --- Quadros mais procurados -------------------------------------------
    Def(
        "ansiedade",
        "Ansiedade",
        "quadros",
        "Preocupação excessiva, tensão constante, crises de ansiedade.",
    ),
    Def(
        "depressao",
        "Depressão",
        "quadros",
        "Tristeza persistente, perda de interesse, falta de energia.",
    ),
    Def(
        "panico",
        "Síndrome do pânico",
        "quadros",
        "Crises súbitas de medo intenso com sintomas físicos.",
    ),
    Def("toc", "TOC", "quadros", "Pensamentos intrusivos e rituais difíceis de controlar."),
    Def("tdah", "TDAH", "quadros", "Desatenção, impulsividade e dificuldade de organização."),
    Def("tea", "Autismo (TEA)", "quadros", "Avaliação e acompanhamento no espectro autista."),
    Def(
        "transtorno-bipolar",
        "Transtorno bipolar",
        "quadros",
        "Alternância entre episódios de humor elevado e depressivo.",
    ),
    Def(
        "trauma-tept",
        "Trauma e TEPT",
        "quadros",
        "Vivências traumáticas, revivescências, hipervigilância.",
    ),
    Def("fobias", "Fobias", "quadros", "Medos específicos e intensos que limitam a rotina."),
    Def(
        "transtornos-alimentares",
        "Transtornos alimentares",
        "quadros",
        "Relação sofrida com comida, corpo e peso.",
    ),
    Def(
        "dependencia-quimica",
        "Dependência química",
        "quadros",
        "Uso problemático de álcool e outras substâncias.",
    ),
    Def("sono", "Distúrbios do sono", "quadros", "Insônia, sono não reparador, ritmo desregulado."),
    # --- Momentos de vida ---------------------------------------------------
    Def("luto", "Luto", "momentos", "Elaboração de perdas e despedidas."),
    Def("burnout", "Burnout", "momentos", "Esgotamento profundo ligado ao trabalho."),
    Def(
        "estresse-ocupacional",
        "Estresse no trabalho",
        "momentos",
        "Pressão, conflitos e sofrimento no ambiente profissional.",
    ),
    Def(
        "maternidade-parentalidade",
        "Maternidade e parentalidade",
        "momentos",
        "Gestação, puerpério, desafios de criar filhos.",
    ),
    Def(
        "orientacao-vocacional",
        "Orientação vocacional",
        "momentos",
        "Escolha e transição de carreira.",
    ),
    Def("autoestima", "Autoestima", "momentos", "Autocrítica intensa, insegurança, autoimagem."),
    Def(
        "dor-cronica", "Dor crônica", "momentos", "Impacto emocional de dor e doenças persistentes."
    ),
    # --- Relações -----------------------------------------------------------
    Def(
        "terapia-casal",
        "Terapia de casal",
        "relacoes",
        "Conflitos, comunicação e crises no relacionamento.",
    ),
    Def("terapia-familiar", "Terapia familiar", "relacoes", "Dinâmicas e conflitos familiares."),
    Def("sexualidade", "Sexualidade", "relacoes", "Questões de desejo, intimidade e vida sexual."),
    Def(
        "identidade-genero",
        "Identidade de gênero",
        "relacoes",
        "Acolhimento a pessoas LGBTQIA+ e questões de identidade.",
    ),
    Def(
        "violencia-domestica",
        "Violência doméstica",
        "relacoes",
        "Apoio a quem vive ou viveu situações de violência.",
    ),
    # --- Públicos -----------------------------------------------------------
    Def("infantil", "Psicologia infantil", "publicos", "Atendimento de crianças."),
    Def("adolescente", "Adolescentes", "publicos", "Atendimento na adolescência."),
    Def("idoso", "Pessoa idosa", "publicos", "Atendimento na terceira idade."),
    # --- Abordagens ---------------------------------------------------------
    Def(
        "tcc",
        "Terapia cognitivo-comportamental",
        "abordagens",
        "Abordagem focada em pensamentos, emoções e comportamento.",
    ),
    Def("psicanalise", "Psicanálise", "abordagens", "Abordagem de investigação do inconsciente."),
    Def("neuropsicologia", "Neuropsicologia", "abordagens", "Avaliação de funções cognitivas."),
)


async def semear_especialidades(sessao: AsyncSession) -> int:
    existentes = set((await sessao.execute(select(Especialidade.slug))).scalars().all())
    novos = 0
    for ordem, d in enumerate(ESPECIALIDADES, start=1):
        if d.slug in existentes:
            continue
        sessao.add(
            Especialidade(
                slug=d.slug,
                nome=d.nome,
                categoria=d.categoria,
                descricao=d.descricao,
                ordem_exibicao=ordem,
            )
        )
        novos += 1
    await sessao.flush()
    return novos
