"""Política de acesso administrativo, em um lugar só.

Adotado do Pectec Nexos: em vez de decorar endpoint por endpoint, a política
vive num mapa central de **prefixo de rota → módulo**, e o método HTTP define a
operação CRUD. Fica fácil auditar "quem pode o quê" lendo um arquivo.

Duas diferenças em relação ao original, deliberadas:

1. **Dependência do FastAPI, não middleware do Starlette.** O middleware do
   pectec precisa abrir a própria sessão de banco e validar o token na mão,
   porque roda fora da injeção de dependência. Aqui a checagem é uma
   dependência, então reaproveita a sessão da requisição e o usuário já
   resolvido — menos código e menos uma conexão por requisição.

2. **Negar por omissão.** Lá, rota não mapeada exige apenas login. Aqui, tudo
   sob ``/admin`` exige permissão explícita: numa plataforma de saúde, um
   endpoint administrativo novo não pode nascer aberto porque alguém esqueceu
   de mapeá-lo.
"""

from __future__ import annotations

from fastapi import Request

from app.core.erros import NaoAutenticado, NaoAutorizado
from app.core.logging import get_logger
from app.models import Papel, Usuario

log = get_logger(__name__)

#: Prefixo de rota -> módulo de permissão. Ordem importa: o mais específico
#: primeiro, porque a resolução para no primeiro que casar.
PREFIXO_PARA_MODULO: tuple[tuple[str, str], ...] = (
    ("/admin/usuarios", "usuarios"),
    ("/admin/papeis", "usuarios"),
    ("/admin/profissionais", "profissionais"),
    ("/admin/pacientes", "pacientes"),
    ("/admin/agendamentos", "agendamentos"),
    ("/admin/financeiro", "financeiro"),
    ("/admin/taxonomias", "taxonomias"),
    ("/admin/parametros", "configuracoes"),
    ("/admin/termos", "termos"),
    ("/admin/auditoria", "auditoria"),
    ("/admin/documentos", "documentos"),
)

METODO_PARA_OPERACAO = {
    "GET": "read",
    "HEAD": "read",
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}

#: Prefixo cuja política é administrativa. Fora dele valem as regras de
#: domínio (paciente vê o dele, profissional vê os atendimentos dele).
PREFIXO_ADMINISTRATIVO = "/admin"


def resolver_modulo(caminho: str) -> str | None:
    for prefixo, modulo in PREFIXO_PARA_MODULO:
        if caminho == prefixo or caminho.startswith(prefixo + "/"):
            return modulo
    return None


def exige_politica_administrativa(caminho: str) -> bool:
    return caminho == PREFIXO_ADMINISTRATIVO or caminho.startswith(PREFIXO_ADMINISTRATIVO + "/")


def autorizar(usuario: Usuario | None, request: Request) -> None:
    """Aplica a política administrativa. Levanta se não pode.

    Chamado pela dependência ``requer_permissao`` montada nas rotas ``/admin``.
    """
    caminho = request.url.path
    if not exige_politica_administrativa(caminho):
        return

    if usuario is None:
        raise NaoAutenticado()

    modulo = resolver_modulo(caminho)
    if modulo is None:
        # Endpoint administrativo sem módulo mapeado: nega. Esquecer de mapear
        # tem de doer em desenvolvimento, não virar brecha em produção.
        log.error("permissao.rota_administrativa_sem_modulo", caminho=caminho)
        raise NaoAutorizado("Esta área ainda não tem política de acesso definida. Avise o suporte.")

    operacao = METODO_PARA_OPERACAO.get(request.method)
    if operacao is None:
        raise NaoAutorizado()

    if usuario.permite(modulo, operacao):
        return

    # ADMIN sem Role não é privilégio implícito: o papel dá acesso à área, a
    # Role diz o que pode fazer nela. Um admin recém-criado sem papel atribuído
    # não deve poder mexer em tudo por omissão.
    log.info(
        "permissao.negada",
        usuario_id=str(usuario.id),
        modulo=modulo,
        operacao=operacao,
        papel=usuario.papel.value,
    )
    raise NaoAutorizado(f"Seu papel não permite {_rotulo(operacao)} em “{modulo}”.")


def _rotulo(operacao: str) -> str:
    return {
        "create": "criar",
        "read": "consultar",
        "update": "alterar",
        "delete": "excluir",
    }.get(operacao, operacao)


def eh_staff(usuario: Usuario | None) -> bool:
    """Se a pessoa opera a plataforma (tem acesso à área administrativa)."""
    return usuario is not None and usuario.papel is Papel.ADMIN
