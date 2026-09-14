"""Constraints EXCLUDE, citext e a FK pendente de parametros_sistema

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-12

O que está aqui o ``--autogenerate`` não sabe produzir: constraints EXCLUDE,
tipos de extensão (citext) e índices funcionais. São **regras de negócio
garantidas pelo banco**, não detalhes de implementação:

* R6 (sem sobreposição de horário) vira `EXCLUDE USING gist`, que é a verdade
  final -- vale mesmo para um INSERT feito fora da aplicação;
* e-mail vira `citext`, para que "Joao@x.com" e "joao@x.com" não gerem
  duas contas.

Todas exigem `btree_gist`, criada na migration 0001: as constraints combinam
igualdade (`profissional_id WITH =`) com sobreposição de range (`WITH &&`), e
sem essa extensão o Postgres recusa o índice.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Estados que ocupam o horário. Precisa espelhar STATUS_OCUPAM_AGENDA em
#: app/models/enums.py -- se um lado mudar sem o outro, a constraint fica frouxa.
STATUS_OCUPAM = "'PENDENTE_PAGAMENTO','CONFIRMADO','EM_ANDAMENTO'"


def upgrade() -> None:
    # --- E-mail case-insensitive ------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "citext"')
    op.execute("ALTER TABLE usuarios ALTER COLUMN email TYPE citext")

    # --- FK que a 0001 deixou pendente ------------------------------------
    # `parametros_sistema` nasceu antes de `usuarios` existir.
    op.execute(
        """
        ALTER TABLE parametros_sistema
          ADD CONSTRAINT fk_parametros_sistema_atualizado_por_id_usuarios
          FOREIGN KEY (atualizado_por_id) REFERENCES usuarios(id) ON DELETE SET NULL
        """
    )

    # --- R6a: disponibilidades do mesmo profissional não se sobrepõem ------
    # int4range sobre minutos-do-dia; `TIME` não tem operador de range.
    op.execute(
        """
        ALTER TABLE disponibilidades_recorrentes
          ADD CONSTRAINT ex_disponibilidade_sem_sobreposicao
          EXCLUDE USING gist (
            profissional_id WITH =,
            dia_semana WITH =,
            int4range(inicio_min, fim_min, '[)') WITH &&
          ) WHERE (ativo)
        """
    )

    # --- R6b: o profissional não tem dois compromissos no mesmo horário ----
    op.execute(
        f"""
        ALTER TABLE agendamentos
          ADD CONSTRAINT ex_agendamento_profissional_sem_sobreposicao
          EXCLUDE USING gist (
            profissional_id WITH =,
            tstzrange(inicio_utc, fim_utc, '[)') WITH &&
          ) WHERE (status IN ({STATUS_OCUPAM}))
        """
    )

    # --- R6c: nem o paciente ----------------------------------------------
    op.execute(
        f"""
        ALTER TABLE agendamentos
          ADD CONSTRAINT ex_agendamento_paciente_sem_sobreposicao
          EXCLUDE USING gist (
            paciente_id WITH =,
            tstzrange(inicio_utc, fim_utc, '[)') WITH &&
          ) WHERE (status IN ({STATUS_OCUPAM}))
        """
    )

    # --- Só um termo vigente por tipo -------------------------------------
    op.execute(
        """
        CREATE UNIQUE INDEX uq_termo_vigente_por_tipo
          ON termos_versionados (tipo) WHERE (vigente_ate IS NULL)
        """
    )

    # --- Busca por nome, tolerante a acento -------------------------------
    # `unaccent()` é STABLE, não IMMUTABLE (o resultado depende do dicionário de
    # busca em vigor), e o Postgres recusa função não-imutável em expressão de
    # índice. O contorno documentado é fixar o dicionário explicitamente, o que
    # torna a chamada determinística.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION imutavel_unaccent(text)
        RETURNS text
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS
        $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$
        """
    )
    op.execute(
        """
        CREATE INDEX ix_perfis_profissional_nome_trgm
          ON perfis_profissional
          USING gin (lower(imutavel_unaccent(nome_exibicao)) gin_trgm_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_especialidades_nome_trgm
          ON especialidades USING gin (lower(imutavel_unaccent(nome)) gin_trgm_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_especialidades_nome_trgm")
    op.execute("DROP INDEX IF EXISTS ix_perfis_profissional_nome_trgm")
    op.execute("DROP FUNCTION IF EXISTS imutavel_unaccent(text)")
    op.execute("DROP INDEX IF EXISTS uq_termo_vigente_por_tipo")
    op.execute(
        "ALTER TABLE agendamentos DROP CONSTRAINT IF EXISTS "
        "ex_agendamento_paciente_sem_sobreposicao"
    )
    op.execute(
        "ALTER TABLE agendamentos DROP CONSTRAINT IF EXISTS "
        "ex_agendamento_profissional_sem_sobreposicao"
    )
    op.execute(
        "ALTER TABLE disponibilidades_recorrentes DROP CONSTRAINT IF EXISTS "
        "ex_disponibilidade_sem_sobreposicao"
    )
    op.execute(
        "ALTER TABLE parametros_sistema DROP CONSTRAINT IF EXISTS "
        "fk_parametros_sistema_atualizado_por_id_usuarios"
    )
    op.execute("ALTER TABLE usuarios ALTER COLUMN email TYPE varchar(255)")
