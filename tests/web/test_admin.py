"""Área administrativa: política central de permissões na prática."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.sessao_web import COOKIE_CSRF
from app.models import MODULOS_SISTEMA, Papel, Role, RolePermission, Usuario
from tests import fabricas as f
from tests.web.test_profissional import logar

pytestmark = [pytest.mark.web, pytest.mark.db]


def cliente(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://teste", follow_redirects=False
    )


async def criar_papel(sessao: AsyncSession, nome: str, permissoes: dict[str, set[str]]) -> Role:
    import uuid

    papel = Role(nome=f"{nome}-{uuid.uuid4().hex[:6]}")
    sessao.add(papel)
    await sessao.flush()
    for modulo, ops in permissoes.items():
        sessao.add(
            RolePermission(
                role_id=papel.id,
                modulo=modulo,
                pode_criar="create" in ops,
                pode_ler="read" in ops,
                pode_atualizar="update" in ops,
                pode_deletar="delete" in ops,
            )
        )
    await sessao.flush()
    return papel


async def criar_admin(sessao: AsyncSession, papel: Role | None = None) -> Usuario:
    usuario = await f.criar_usuario(sessao, papel=Papel.ADMIN, nome="Admin Teste")
    usuario.role_id = papel.id if papel else None
    await sessao.flush()
    return usuario


class TestAcesso:
    async def test_paciente_nao_entra_na_area_administrativa(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        paciente = await f.criar_paciente(sessao)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, paciente.usuario_id)
            r = await c.get("/admin/usuarios")
        assert r.status_code == 403

    async def test_admin_sem_papel_nao_acessa_nada(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Ser ADMIN dá acesso à área; a Role diz o que pode fazer nela.

        Um admin recém-criado sem papel atribuído não deve poder mexer em tudo
        por omissão.
        """
        admin = await criar_admin(sessao, papel=None)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            r = await c.get("/admin/usuarios")
        assert r.status_code == 403

    async def test_papel_com_leitura_acessa(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        papel = await criar_papel(sessao, "Leitor", {"usuarios": {"read"}})
        admin = await criar_admin(sessao, papel)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            r = await c.get("/admin/usuarios")
        assert r.status_code == 200

    async def test_leitura_nao_da_escrita(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """O método HTTP define a operação: POST exige `create`."""
        papel = await criar_papel(sessao, "Leitor", {"usuarios": {"read"}})
        admin = await criar_admin(sessao, papel)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            headers = {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}
            r = await c.post(
                "/admin/usuarios",
                data={
                    "nome_completo": "Novo Admin",
                    "email": "novo.admin@teste.br",
                    "senha": "senha-forte-2026",
                    "papel": "ADMIN",
                },
                headers=headers,
            )
        assert r.status_code == 403

    async def test_permissao_de_um_modulo_nao_vaza_para_outro(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """Quem pode ver contas não passa a ver documentos."""
        papel = await criar_papel(sessao, "SoUsuarios", {"usuarios": {"read", "update"}})
        admin = await criar_admin(sessao, papel)
        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            assert (await c.get("/admin/usuarios")).status_code == 200
            assert (await c.get("/admin/parametros")).status_code == 403
            assert (await c.get("/admin/profissionais")).status_code == 403


class TestGestaoDePapeis:
    async def test_cria_papel_e_ajusta_permissoes(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        papel = await criar_papel(
            sessao, "Gestor", {"usuarios": {"create", "read", "update", "delete"}}
        )
        admin = await criar_admin(sessao, papel)

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            headers = {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}
            r = await c.post(
                "/admin/papeis",
                data={"nome": "Recepção", "descricao": "Atendimento inicial"},
                headers=headers,
            )
            assert r.status_code == 303

            novo = await sessao.scalar(
                select(Role).where(Role.nome == "Recepção").options(selectinload(Role.permissoes))
            )
            assert novo is not None
            # Nasce sem permissão nenhuma: negar por omissão.
            assert novo.permissoes == []

            r = await c.patch(
                f"/admin/papeis/{novo.id}/permissoes",
                data={"perm_agendamentos_read": "1", "perm_agendamentos_update": "1"},
                headers=headers,
            )
            assert r.status_code == 303

            # Captura o id ANTES de expirar: ler um atributo de objeto expirado
            # dispara refresh síncrono e estoura MissingGreenlet.
            papel_id = novo.id
            sessao.expire_all()
            novo = await sessao.scalar(
                select(Role).where(Role.id == papel_id).options(selectinload(Role.permissoes))
            )
            assert novo is not None
            assert novo.permite("agendamentos", "read")
            assert novo.permite("agendamentos", "update")
            assert not novo.permite("agendamentos", "delete")
            assert not novo.permite("financeiro", "read")

    async def test_papel_de_sistema_nao_pode_ser_alterado(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        """A rede de segurança: se der para esvaziar o papel de sistema, dá para
        trancar todo mundo do lado de fora."""
        papel = await criar_papel(sessao, "Gestor", {"usuarios": {"read", "update"}})
        admin = await criar_admin(sessao, papel)

        sistema = Role(nome="AdminSistemaTeste", sistema=True)
        sessao.add(sistema)
        await sessao.flush()

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            headers = {"X-CSRF-Token": c.cookies[COOKIE_CSRF]}
            r = await c.patch(
                f"/admin/papeis/{sistema.id}/permissoes",
                data={"perm_usuarios_read": "1"},
                headers=headers,
            )
        assert r.status_code == 422
        assert "sistema" in r.text.lower()


class TestSalvaguardas:
    async def test_nao_remove_o_proprio_papel(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        papel = await criar_papel(sessao, "Gestor", {"usuarios": {"read", "update"}})
        admin = await criar_admin(sessao, papel)

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            r = await c.patch(
                f"/admin/usuarios/{admin.id}",
                data={"role_id": "", "ativo": "1"},
                headers={"X-CSRF-Token": c.cookies[COOKIE_CSRF]},
            )
        assert r.status_code == 403
        assert "próprio papel" in r.text

    async def test_nao_desativa_a_propria_conta(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        papel = await criar_papel(sessao, "Gestor", {"usuarios": {"read", "update"}})
        admin = await criar_admin(sessao, papel)

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            r = await c.patch(
                f"/admin/usuarios/{admin.id}",
                data={"role_id": str(papel.id), "ativo": ""},
                headers={"X-CSRF-Token": c.cookies[COOKIE_CSRF]},
            )
        assert r.status_code == 403


class TestParametros:
    async def test_altera_a_comissao(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        from app.models import ChaveParametro, ParametroSistema
        from app.services.parametros_service import ParametrosService

        papel = await criar_papel(sessao, "Config", {"configuracoes": {"read", "update"}})
        admin = await criar_admin(sessao, papel)

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            r = await c.patch(
                f"/admin/parametros/{ChaveParametro.COMISSAO_PERCENTUAL}",
                data={"valor": "15"},
                headers={"X-CSRF-Token": c.cookies[COOKIE_CSRF]},
            )
        assert r.status_code == 303

        parametro = await sessao.get(ParametroSistema, ChaveParametro.COMISSAO_PERCENTUAL)
        assert parametro is not None
        assert float(parametro.valor) == 15.0
        assert parametro.atualizado_por_id == admin.id
        ParametrosService.invalidar_cache()

    async def test_valor_nao_numerico_e_recusado(
        self, app: FastAPI, sessao: AsyncSession, settings: Settings
    ) -> None:
        from app.models import ChaveParametro

        papel = await criar_papel(sessao, "Config", {"configuracoes": {"read", "update"}})
        admin = await criar_admin(sessao, papel)

        async with cliente(app) as c:
            await logar(c, app, sessao, settings, admin.id)
            r = await c.patch(
                f"/admin/parametros/{ChaveParametro.LIMITE_HORAS_DIA}",
                data={"valor": "muitas"},
                headers={"X-CSRF-Token": c.cookies[COOKIE_CSRF]},
            )
        assert r.status_code == 422


class TestCoberturaDaPolitica:
    async def test_todo_modulo_tem_rota_ou_esta_documentado(self) -> None:
        """Módulo sem prefixo mapeado é permissão que não protege nada.

        Não é erro — pode ser área ainda não construída —, mas convém saber
        quais são.
        """
        from app.core.permissoes import PREFIXO_PARA_MODULO

        mapeados = {modulo for _, modulo in PREFIXO_PARA_MODULO}
        sem_rota = set(MODULOS_SISTEMA) - mapeados
        # Estes ainda não têm tela; quando ganharem, o mapa precisa ser atualizado.
        assert sem_rota == set(), f"módulos sem rota mapeada: {sorted(sem_rota)}"
