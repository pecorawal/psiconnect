"""Dados de demonstração.

Cria um profissional pronto para atender (perfil completo, especialidades com
preço, agenda de seg a sex) e um paciente. Serve para a verificação clique a
clique da Fase 1 sem ter de preencher formulário nenhum.

Bloqueado em produção pelo ``__main__``.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.seguranca import gerar_hash_senha
from app.models import (
    Conselho,
    DisponibilidadeRecorrente,
    Especialidade,
    Papel,
    PerfilPaciente,
    PerfilProfissional,
    StatusCadastro,
    Usuario,
)
from app.models.perfil import ProfissionalEspecialidade

SENHA_DEMO = "psiconnect123"

EMAIL_PROFISSIONAL = "psi@demo.br"
EMAIL_PACIENTE = "pac@demo.br"

#: (slug, preço em centavos)
ESPECIALIDADES_DEMO = (
    ("ansiedade", 15000),
    ("depressao", 15000),
    ("burnout", 18000),
    ("sono", 15000),
    ("autoestima", 14000),
)

#: 0 = segunda .. 4 = sexta; manhã 09:00-12:00 e tarde 14:00-18:00.
JANELAS = ((540, 720), (840, 1080))


async def semear_demo(sessao: AsyncSession) -> dict[str, object]:
    ja_existe = await sessao.scalar(select(Usuario.id).where(Usuario.email == EMAIL_PROFISSIONAL))
    if ja_existe is not None:
        return {"status": "ja_existia"}

    senha_hash = gerar_hash_senha(SENHA_DEMO)

    # --- Profissional -------------------------------------------------------
    usuario_prof = Usuario(
        email=EMAIL_PROFISSIONAL,
        senha_hash=senha_hash,
        papel=Papel.PROFISSIONAL,
        nome_completo="Ana Beatriz Souza",
        telefone_e164="+5511990001111",
    )
    sessao.add(usuario_prof)
    await sessao.flush()

    perfil = PerfilProfissional(
        usuario_id=usuario_prof.id,
        nome_exibicao="Ana Beatriz Souza",
        conselho=Conselho.CRP,
        registro_numero="123456",
        registro_uf="SP",
        descricao=(
            "Psicóloga clínica com foco em ansiedade, esgotamento e dificuldades de sono. "
            "Atendo adultos em abordagem cognitivo-comportamental, com escuta acolhedora e "
            "objetivos combinados desde a primeira sessão."
        ),
        status_cadastro=StatusCadastro.APROVADO,
        foto_url=None,
    )
    sessao.add(perfil)
    await sessao.flush()

    slugs = [s for s, _ in ESPECIALIDADES_DEMO]
    encontradas = {
        slug: id_
        for slug, id_ in (
            await sessao.execute(
                select(Especialidade.slug, Especialidade.id).where(Especialidade.slug.in_(slugs))
            )
        ).all()
    }
    for ordem, (slug, preco) in enumerate(ESPECIALIDADES_DEMO, start=1):
        id_esp = encontradas.get(slug)
        if id_esp is None:
            continue
        sessao.add(
            ProfissionalEspecialidade(
                profissional_id=perfil.usuario_id,
                especialidade_id=id_esp,
                ordem=ordem,
                preco_min_centavos=int(preco * 0.8),
                preco_padrao_centavos=preco,
                preco_max_centavos=int(preco * 1.3),
            )
        )

    for dia in range(5):  # segunda a sexta
        for inicio, fim in JANELAS:
            sessao.add(
                DisponibilidadeRecorrente(
                    profissional_id=perfil.usuario_id,
                    dia_semana=dia,
                    inicio_min=inicio,
                    fim_min=fim,
                    vigencia_inicio=date(2020, 1, 1),
                )
            )

    # --- Paciente -----------------------------------------------------------
    usuario_pac = Usuario(
        email=EMAIL_PACIENTE,
        senha_hash=senha_hash,
        papel=Papel.PACIENTE,
        nome_completo="João Carlos Lima",
        telefone_e164="+5511990002222",
    )
    sessao.add(usuario_pac)
    await sessao.flush()
    sessao.add(PerfilPaciente(usuario_id=usuario_pac.id, data_nascimento=date(1990, 5, 20)))

    await sessao.flush()
    return {
        "status": "criado",
        "profissional": EMAIL_PROFISSIONAL,
        "paciente": EMAIL_PACIENTE,
        "senha": SENHA_DEMO,
        "especialidades": len(encontradas),
        "disponibilidades": 5 * len(JANELAS),
    }
