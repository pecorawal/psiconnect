"""Política de acesso administrativo — esquema Role/RolePermission."""

from __future__ import annotations

import pytest

from app.core.permissoes import (
    METODO_PARA_OPERACAO,
    PREFIXO_PARA_MODULO,
    exige_politica_administrativa,
    resolver_modulo,
)
from app.models import MODULOS_SISTEMA, Role, RolePermission

pytestmark = pytest.mark.unit


class TestMapaDePrefixos:
    def test_todo_prefixo_aponta_para_modulo_conhecido(self) -> None:
        """Um typo no mapa deixaria a rota sem política — e o `autorizar`
        negaria tudo. Melhor pegar aqui."""
        for prefixo, modulo in PREFIXO_PARA_MODULO:
            assert modulo in MODULOS_SISTEMA, f"{prefixo} aponta para módulo inexistente"

    def test_prefixos_sao_administrativos(self) -> None:
        for prefixo, _ in PREFIXO_PARA_MODULO:
            assert exige_politica_administrativa(prefixo)

    @pytest.mark.parametrize(
        ("caminho", "esperado"),
        [
            ("/admin/usuarios", "usuarios"),
            ("/admin/usuarios/123", "usuarios"),
            ("/admin/parametros", "configuracoes"),
            ("/admin/financeiro/repasses", "financeiro"),
            ("/admin/inexistente", None),
        ],
    )
    def test_resolucao(self, caminho: str, esperado: str | None) -> None:
        assert resolver_modulo(caminho) == esperado

    def test_rota_de_dominio_nao_e_administrativa(self) -> None:
        """As rotas de paciente e profissional seguem regras de domínio, não
        CRUD por módulo."""
        for caminho in ("/paciente/sintomas", "/profissional/agenda", "/sessao/x/lobby"):
            assert not exige_politica_administrativa(caminho)

    def test_metodos_cobrem_o_crud(self) -> None:
        assert set(METODO_PARA_OPERACAO.values()) == {"create", "read", "update", "delete"}


class TestRolePermite:
    def _papel(self, **permissoes: tuple[bool, bool, bool, bool]) -> Role:
        papel = Role(nome="Teste")
        papel.permissoes = [
            RolePermission(
                modulo=modulo,
                pode_criar=c,
                pode_ler=r,
                pode_atualizar=u,
                pode_deletar=d,
            )
            for modulo, (c, r, u, d) in permissoes.items()
        ]
        return papel

    def test_permite_o_que_esta_marcado(self) -> None:
        papel = self._papel(agendamentos=(False, True, True, False))
        assert papel.permite("agendamentos", "read")
        assert papel.permite("agendamentos", "update")
        assert not papel.permite("agendamentos", "create")
        assert not papel.permite("agendamentos", "delete")

    def test_modulo_sem_linha_e_negado(self) -> None:
        """Negar por omissão: um módulo novo não nasce liberado."""
        papel = self._papel(agendamentos=(True, True, True, True))
        assert not papel.permite("financeiro", "read")

    def test_operacao_desconhecida_e_negada(self) -> None:
        papel = self._papel(agendamentos=(True, True, True, True))
        assert not papel.permite("agendamentos", "exportar")

    def test_papel_vazio_nao_permite_nada(self) -> None:
        papel = Role(nome="Vazio")
        papel.permissoes = []
        for modulo in MODULOS_SISTEMA:
            for operacao in ("create", "read", "update", "delete"):
                assert not papel.permite(modulo, operacao)
