"""Taxonomia de sintomas e o mapa sintoma → especialidade.

Regra de redação: **o texto é do paciente, não do manual**. Ninguém chega
dizendo "tenho TAG"; chega dizendo "não consigo desligar a cabeça". O
``descricao_leiga`` é o que aparece na tela; o ``nome`` é o rótulo curto.

``bandeira_risco=True`` marca sintomas que disparam o protocolo de crise:
destaque do CVV e, a partir da Fase 3, alerta ao profissional.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Especialidade, Sintoma, SintomaEspecialidade


class Def(NamedTuple):
    slug: str
    nome: str
    descricao_leiga: str
    categoria: str
    #: (slug_da_especialidade, peso 1..5)
    especialidades: tuple[tuple[str, int], ...]
    bandeira_risco: bool = False


SINTOMAS: tuple[Def, ...] = (
    # --- Humor --------------------------------------------------------------
    Def(
        "tristeza-persistente",
        "Tristeza que não passa",
        "Estou triste na maior parte dos dias e não sei bem por quê",
        "humor",
        (("depressao", 5), ("luto", 3), ("autoestima", 2)),
    ),
    Def(
        "sem-vontade",
        "Perdi a vontade de tudo",
        "Coisas de que eu gostava não me animam mais",
        "humor",
        (("depressao", 5), ("burnout", 3)),
    ),
    Def(
        "irritabilidade",
        "Irrito-me com facilidade",
        "Qualquer coisa me tira do sério",
        "humor",
        (("ansiedade", 3), ("burnout", 3), ("transtorno-bipolar", 3), ("tdah", 2)),
    ),
    Def(
        "oscilacao-humor",
        "Meu humor muda muito",
        "Passo de muito animado para muito para baixo",
        "humor",
        (("transtorno-bipolar", 5), ("depressao", 2)),
    ),
    # --- Ansiedade ----------------------------------------------------------
    Def(
        "preocupacao-excessiva",
        "Não consigo desligar a cabeça",
        "Fico o tempo todo pensando no que pode dar errado",
        "ansiedade",
        (("ansiedade", 5), ("toc", 2)),
    ),
    Def(
        "coracao-acelerado",
        "Meu coração dispara",
        "Sinto o coração acelerar, falta de ar, tremor",
        "ansiedade",
        (("panico", 5), ("ansiedade", 4)),
    ),
    Def(
        "medo-especifico",
        "Tenho um medo que me limita",
        "Evito situações ou lugares por causa de um medo específico",
        "ansiedade",
        (("fobias", 5), ("ansiedade", 3), ("panico", 2)),
    ),
    Def(
        "pensamentos-repetitivos",
        "Pensamentos que não param",
        "Penso a mesma coisa sem parar, ou preciso repetir rituais",
        "ansiedade",
        (("toc", 5), ("ansiedade", 3)),
    ),
    # --- Sono e corpo -------------------------------------------------------
    Def(
        "insonia",
        "Não consigo dormir",
        "Demoro para pegar no sono ou acordo várias vezes",
        "corpo",
        (("sono", 5), ("ansiedade", 4), ("depressao", 3)),
    ),
    Def(
        "cansaco-constante",
        "Estou sempre exausto",
        "Acordo cansado mesmo depois de dormir",
        "corpo",
        (("burnout", 4), ("depressao", 4), ("sono", 3)),
    ),
    Def(
        "dor-sem-causa",
        "Sinto dores sem explicação",
        "Tenho dores que os exames não explicam",
        "corpo",
        (("dor-cronica", 5), ("ansiedade", 3)),
    ),
    Def(
        "relacao-comida",
        "Minha relação com comida me sofre",
        "Como demais, de menos, ou penso o tempo todo no meu corpo",
        "corpo",
        (("transtornos-alimentares", 5), ("autoestima", 3)),
    ),
    # --- Trabalho e rotina --------------------------------------------------
    Def(
        "esgotamento-trabalho",
        "O trabalho me esgotou",
        "Sinto que não aguento mais o meu trabalho",
        "rotina",
        (("burnout", 5), ("estresse-ocupacional", 4), ("depressao", 2)),
    ),
    Def(
        "dificuldade-concentracao",
        "Não consigo me concentrar",
        "Perco o foco com facilidade e deixo tarefas pela metade",
        "rotina",
        (("tdah", 5), ("ansiedade", 3), ("depressao", 2)),
    ),
    Def(
        "procrastinacao",
        "Adio tudo o que preciso fazer",
        "Sei o que tenho que fazer, mas não começo",
        "rotina",
        (("tdah", 4), ("ansiedade", 3), ("depressao", 2)),
    ),
    Def(
        "indecisao-carreira",
        "Não sei que rumo dar à carreira",
        "Estou perdido sobre o que quero profissionalmente",
        "rotina",
        (("orientacao-vocacional", 5), ("autoestima", 2)),
    ),
    # --- Relações -----------------------------------------------------------
    Def(
        "conflito-relacionamento",
        "Meu relacionamento está difícil",
        "Brigamos muito ou perdemos a conexão",
        "relacoes",
        (("terapia-casal", 5), ("terapia-familiar", 2)),
    ),
    Def(
        "conflito-familiar",
        "Minha família me adoece",
        "As relações em casa me fazem mal",
        "relacoes",
        (("terapia-familiar", 5), ("terapia-casal", 2)),
    ),
    Def(
        "solidao",
        "Sinto-me sozinho",
        "Mesmo perto de pessoas, sinto que ninguém me entende",
        "relacoes",
        (("depressao", 3), ("autoestima", 3)),
    ),
    Def(
        "dificuldade-sexual",
        "Minha vida sexual me incomoda",
        "Tenho dificuldades ou insatisfação na intimidade",
        "relacoes",
        (("sexualidade", 5), ("terapia-casal", 3)),
    ),
    Def(
        "questoes-identidade",
        "Questiono minha identidade",
        "Tenho dúvidas sobre minha identidade de gênero ou orientação",
        "relacoes",
        (("identidade-genero", 5), ("autoestima", 2)),
    ),
    # --- Eventos ------------------------------------------------------------
    Def(
        "perda-recente",
        "Perdi alguém",
        "Estou lidando com a morte ou a ausência de alguém importante",
        "eventos",
        (("luto", 5), ("depressao", 3)),
    ),
    Def(
        "evento-traumatico",
        "Passei por algo muito difícil",
        "Revivo uma situação traumática ou evito tudo que a lembre",
        "eventos",
        (("trauma-tept", 5), ("ansiedade", 3)),
    ),
    Def(
        "violencia",
        "Sofri ou sofro violência",
        "Vivo ou vivi situações de violência ou ameaça",
        "eventos",
        (("violencia-domestica", 5), ("trauma-tept", 4)),
        bandeira_risco=True,
    ),
    Def(
        "maternidade-dificil",
        "A maternidade está pesada",
        "Gestação, pós-parto ou criar meus filhos tem sido muito difícil",
        "eventos",
        (("maternidade-parentalidade", 5), ("depressao", 3)),
    ),
    # --- Uso de substâncias --------------------------------------------------
    Def(
        "uso-substancias",
        "Bebo ou uso substâncias demais",
        "Sinto que não consigo controlar o quanto uso",
        "substancias",
        (("dependencia-quimica", 5), ("ansiedade", 2)),
    ),
    # --- Autoimagem ---------------------------------------------------------
    Def(
        "autocritica",
        "Sou muito duro comigo",
        "Sinto que nunca sou bom o bastante",
        "autoimagem",
        (("autoestima", 5), ("depressao", 3), ("ansiedade", 2)),
    ),
    # --- RISCO --------------------------------------------------------------
    Def(
        "ideacao-suicida",
        "Penso em não estar mais aqui",
        "Tenho pensamentos de morte ou de me machucar",
        "risco",
        (("depressao", 5), ("trauma-tept", 3)),
        bandeira_risco=True,
    ),
    Def(
        "automutilacao",
        "Machuco a mim mesmo",
        "Tenho me machucado de propósito",
        "risco",
        (("depressao", 4), ("trauma-tept", 3)),
        bandeira_risco=True,
    ),
)


async def semear_sintomas(sessao: AsyncSession) -> tuple[int, int]:
    """Devolve ``(sintomas_novos, ligacoes_novas)``."""
    existentes = set((await sessao.execute(select(Sintoma.slug))).scalars().all())
    especialidades = {
        slug: id_
        for slug, id_ in (await sessao.execute(select(Especialidade.slug, Especialidade.id))).all()
    }

    novos = 0
    ligacoes = 0
    for ordem, d in enumerate(SINTOMAS, start=1):
        if d.slug in existentes:
            continue
        sintoma = Sintoma(
            slug=d.slug,
            nome=d.nome,
            descricao_leiga=d.descricao_leiga,
            categoria=d.categoria,
            bandeira_risco=d.bandeira_risco,
            ordem_exibicao=ordem,
        )
        sessao.add(sintoma)
        await sessao.flush()
        novos += 1

        for slug_esp, peso in d.especialidades:
            id_esp = especialidades.get(slug_esp)
            if id_esp is None:
                # Seed de especialidades tem de rodar antes; melhor falhar alto
                # do que criar um sintoma que não leva a profissional nenhum.
                raise RuntimeError(
                    f"Especialidade '{slug_esp}' não existe (sintoma '{d.slug}'). "
                    "Rode semear_especialidades primeiro."
                )
            sessao.add(
                SintomaEspecialidade(sintoma_id=sintoma.id, especialidade_id=id_esp, peso=peso)
            )
            ligacoes += 1

    await sessao.flush()
    return novos, ligacoes
