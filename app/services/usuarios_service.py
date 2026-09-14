"""Gestão administrativa de contas e papéis.

Quatro salvaguardas que valem explicar, porque todas nasceram da mesma pergunta:
*o que impede um administrador de, sem querer, trancar a plataforma?*

1. Papel **de sistema** não pode ser excluído nem renomeado.
2. Ninguém remove o **próprio** papel administrativo.
3. Não se apaga a **última** conta capaz de gerir usuários.
4. Desativar é preferível a excluir — conta com histórico de atendimento não
   some, porque o histórico precisa continuar auditável.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.erros import EmailJaCadastrado, ErroDominio, NaoAutorizado, NaoEncontrado
from app.core.logging import get_logger
from app.core.seguranca import gerar_hash_senha, validar_forca_senha
from app.models import (
    COLUNA_DA_OPERACAO,
    MODULOS_SISTEMA,
    OPERACOES_CRUD,
    Papel,
    Role,
    RolePermission,
    Usuario,
)
from app.services.auth_service import SenhaFraca

log = get_logger(__name__)


class PapelProtegido(ErroDominio):
    codigo = "papel_protegido"
    mensagem_padrao = "Este papel é do sistema e não pode ser alterado ou removido."


class UltimoAdministrador(ErroDominio):
    codigo = "ultimo_administrador"
    mensagem_padrao = (
        "Esta é a última conta capaz de gerir usuários. "
        "Dê o papel a outra pessoa antes de remover esta."
    )


class PapelEmUso(ErroDominio):
    codigo = "papel_em_uso"
    mensagem_padrao = "Há contas usando este papel. Mova-as antes de excluí-lo."


@dataclass(frozen=True, slots=True)
class DadosUsuarioAdmin:
    nome_completo: str
    email: str
    papel: Papel
    role_id: uuid.UUID | None = None
    senha: str | None = None
    ativo: bool = True


class UsuariosService:
    def __init__(self, sessao: AsyncSession) -> None:
        self.sessao = sessao

    # --- Papéis -------------------------------------------------------------

    async def listar_papeis(self) -> list[Role]:
        return list((await self.sessao.execute(select(Role).order_by(Role.nome))).scalars().all())

    async def buscar_papel(self, papel_id: uuid.UUID) -> Role:
        papel = await self.sessao.get(Role, papel_id)
        if papel is None:
            raise NaoEncontrado("Papel não encontrado.")
        return papel

    async def criar_papel(self, nome: str, descricao: str, permissoes: dict[str, set[str]]) -> Role:
        """``permissoes`` = ``{modulo: {"read", "update", ...}}``."""
        nome = nome.strip()
        if not nome:
            raise ErroDominio("Dê um nome ao papel.", campo="nome")

        papel = Role(nome=nome, descricao=descricao.strip() or None, sistema=False)
        self.sessao.add(papel)
        try:
            await self.sessao.flush()
        except IntegrityError as exc:
            await self.sessao.rollback()
            raise ErroDominio("Já existe um papel com esse nome.", campo="nome") from exc

        await self._aplicar_permissoes(papel, permissoes)
        log.info("admin.papel_criado", papel=nome)
        return papel

    async def atualizar_permissoes(self, papel: Role, permissoes: dict[str, set[str]]) -> Role:
        if papel.sistema:
            # O papel de sistema é a rede de segurança: se der para esvaziá-lo,
            # dá para trancar todo mundo do lado de fora.
            raise PapelProtegido()
        await self._aplicar_permissoes(papel, permissoes)
        log.info("admin.papel_atualizado", papel=papel.nome)
        return papel

    async def excluir_papel(self, papel: Role) -> None:
        if papel.sistema:
            raise PapelProtegido()

        em_uso = await self.sessao.scalar(
            select(func.count()).select_from(Usuario).where(Usuario.role_id == papel.id)
        )
        if em_uso:
            raise PapelEmUso(f"{em_uso} conta(s) usam este papel. Mova-as antes de excluí-lo.")

        await self.sessao.delete(papel)
        await self.sessao.flush()
        log.info("admin.papel_excluido", papel=papel.nome)

    async def _aplicar_permissoes(self, papel: Role, permissoes: dict[str, set[str]]) -> None:
        # Consulta explícita em vez de `papel.permissoes`: num papel recém-criado
        # o relacionamento ainda não foi carregado, e tocá-lo dispararia lazy
        # load fora do contexto async.
        linhas = (
            await self.sessao.execute(
                select(RolePermission).where(RolePermission.role_id == papel.id)
            )
        ).scalars()
        atuais = {p.modulo: p for p in linhas}

        for modulo in MODULOS_SISTEMA:
            operacoes = permissoes.get(modulo, set())
            linha = atuais.get(modulo)

            if not operacoes:
                # Sem operação marcada, remove a linha: ausência de linha já
                # significa "não pode" (ver Role.permite).
                if linha is not None:
                    await self.sessao.delete(linha)
                continue

            if linha is None:
                linha = RolePermission(role_id=papel.id, modulo=modulo)
                self.sessao.add(linha)

            for operacao in OPERACOES_CRUD:
                setattr(linha, COLUNA_DA_OPERACAO[operacao], operacao in operacoes)

        await self.sessao.flush()

    # --- Contas -------------------------------------------------------------

    async def listar_usuarios(
        self, *, papel: Papel | None = None, busca: str = "", limite: int = 100
    ) -> list[Usuario]:
        consulta = select(Usuario).order_by(Usuario.nome_completo).limit(limite)
        if papel is not None:
            consulta = consulta.where(Usuario.papel == papel)
        if busca.strip():
            termo = f"%{busca.strip().lower()}%"
            consulta = consulta.where(
                func.lower(Usuario.nome_completo).like(termo)
                | func.lower(Usuario.email).like(termo)
            )
        return list((await self.sessao.execute(consulta)).scalars().all())

    async def buscar_usuario(self, usuario_id: uuid.UUID) -> Usuario:
        usuario = await self.sessao.get(Usuario, usuario_id)
        if usuario is None:
            raise NaoEncontrado("Conta não encontrada.")
        return usuario

    async def criar_usuario(self, dados: DadosUsuarioAdmin) -> Usuario:
        if not dados.senha:
            raise ErroDominio("Defina uma senha inicial.", campo="senha")
        if (erro := validar_forca_senha(dados.senha)) is not None:
            raise SenhaFraca(erro, campo="senha")

        usuario = Usuario(
            email=dados.email.strip().lower(),
            senha_hash=gerar_hash_senha(dados.senha),
            papel=dados.papel,
            nome_completo=dados.nome_completo.strip(),
            role_id=dados.role_id,
            ativo=dados.ativo,
        )
        self.sessao.add(usuario)
        try:
            await self.sessao.flush()
        except IntegrityError as exc:
            await self.sessao.rollback()
            raise EmailJaCadastrado(campo="email") from exc

        log.info("admin.usuario_criado", usuario_id=str(usuario.id), papel=dados.papel.value)
        return usuario

    async def definir_papel_administrativo(
        self, alvo: Usuario, role_id: uuid.UUID | None, *, por: Usuario
    ) -> Usuario:
        if alvo.id == por.id and role_id is None:
            raise NaoAutorizado(
                "Você não pode remover o próprio papel administrativo. Peça a outro administrador."
            )
        if role_id is None and await self._eh_ultimo_gestor(alvo):
            raise UltimoAdministrador()

        alvo.role_id = role_id
        await self.sessao.flush()
        log.info(
            "admin.papel_atribuido",
            usuario_id=str(alvo.id),
            role_id=str(role_id) if role_id else None,
            por=str(por.id),
        )
        return alvo

    async def definir_ativo(self, alvo: Usuario, ativo: bool, *, por: Usuario) -> Usuario:
        """Desativar é o caminho normal — excluir apagaria histórico auditável."""
        if not ativo:
            if alvo.id == por.id:
                raise NaoAutorizado("Você não pode desativar a própria conta.")
            if await self._eh_ultimo_gestor(alvo):
                raise UltimoAdministrador()

        alvo.ativo = ativo
        await self.sessao.flush()

        if not ativo:
            # Desativar sem cortar a sessão em curso deixaria a pessoa
            # trabalhando até o cookie vencer.
            from app.services.auth_service import AuthService

            revogadas = await AuthService(self.sessao).revogar_todas(alvo.id)
            log.info(
                "admin.usuario_desativado",
                usuario_id=str(alvo.id),
                sessoes_revogadas=revogadas,
                por=str(por.id),
            )
        return alvo

    async def _eh_ultimo_gestor(self, alvo: Usuario) -> bool:
        """Se esta é a última conta que ainda consegue gerir usuários."""
        if alvo.role_id is None or not alvo.ativo:
            return False
        if not alvo.permite("usuarios", "update"):
            return False

        candidatos = list(
            (
                await self.sessao.execute(
                    select(Usuario).where(
                        Usuario.ativo.is_(True),
                        Usuario.role_id.is_not(None),
                        Usuario.id != alvo.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return not any(u.permite("usuarios", "update") for u in candidatos)
