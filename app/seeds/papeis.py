"""Papéis administrativos padrão.

Três papéis cobrem a operação inicial, e o admin pode criar outros pelo painel.
A diferença entre eles é deliberada e vale explicar:

* **Administrador** — acesso completo, inclusive aos parâmetros de sistema
  (comissão, limites) e aos documentos de identificação.
* **Suporte** — atende usuário: consulta contas e agendamentos, remarca e
  cancela. **Não** vê documentos nem mexe em configuração ou dinheiro.
* **Financeiro** — pagamentos, repasses e notas fiscais. **Não** acessa conta de
  paciente nem documento.

O corte comum a Suporte e Financeiro é o mesmo: **ninguém que não precise vê
documento de identificação**, e nenhum papel operacional lê conteúdo clínico —
isso não é sequer um módulo, porque não deve existir tela administrativa para
transcrição de sessão.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MODULOS_SISTEMA, Role, RolePermission


class Permissao(NamedTuple):
    modulo: str
    criar: bool = False
    ler: bool = False
    atualizar: bool = False
    deletar: bool = False


class DefinicaoPapel(NamedTuple):
    nome: str
    descricao: str
    sistema: bool
    permissoes: tuple[Permissao, ...]


def _todas() -> tuple[Permissao, ...]:
    return tuple(
        Permissao(modulo, criar=True, ler=True, atualizar=True, deletar=True)
        for modulo in MODULOS_SISTEMA
    )


PAPEIS: tuple[DefinicaoPapel, ...] = (
    DefinicaoPapel(
        nome="Administrador",
        descricao="Acesso completo à plataforma, incluindo parâmetros e documentos.",
        # Papel de sistema: a API recusa excluí-lo, para que ninguém remova o
        # último acesso administrativo por engano.
        sistema=True,
        permissoes=_todas(),
    ),
    DefinicaoPapel(
        nome="Suporte",
        descricao="Atende usuários: consulta contas e agendamentos, remarca e cancela.",
        sistema=False,
        permissoes=(
            Permissao("pacientes", ler=True, atualizar=True),
            Permissao("profissionais", ler=True),
            Permissao("agendamentos", ler=True, atualizar=True, deletar=True),
            Permissao("taxonomias", ler=True),
            # Sem "documentos", sem "configuracoes", sem "financeiro":
            # quem atende no suporte não precisa ver RG nem mexer na comissão.
        ),
    ),
    DefinicaoPapel(
        nome="Financeiro",
        descricao="Pagamentos, repasses e notas fiscais.",
        sistema=False,
        permissoes=(
            Permissao("financeiro", criar=True, ler=True, atualizar=True),
            Permissao("profissionais", ler=True),
            Permissao("agendamentos", ler=True),
        ),
    ),
)


async def semear_papeis(sessao: AsyncSession) -> int:
    """Cria os papéis que ainda não existem. Não altera permissões já ajustadas."""
    existentes = set((await sessao.execute(select(Role.nome))).scalars().all())
    novos = 0

    for definicao in PAPEIS:
        if definicao.nome in existentes:
            continue
        papel = Role(nome=definicao.nome, descricao=definicao.descricao, sistema=definicao.sistema)
        sessao.add(papel)
        await sessao.flush()

        for permissao in definicao.permissoes:
            sessao.add(
                RolePermission(
                    role_id=papel.id,
                    modulo=permissao.modulo,
                    pode_criar=permissao.criar,
                    pode_ler=permissao.ler,
                    pode_atualizar=permissao.atualizar,
                    pode_deletar=permissao.deletar,
                )
            )
        novos += 1

    await sessao.flush()
    return novos
