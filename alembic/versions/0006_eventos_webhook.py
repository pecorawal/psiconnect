"""Idempotência de webhook e vínculo pagamento -> agendamento.

`eventos_webhook` é o que impede um reenvio do provedor creditar a sessão duas
vezes. A UNIQUE (provedor, evento_id_externo) é a garantia real -- checagem em
Python perde a corrida quando dois reenvios chegam juntos.

`pagamentos.agendamento_origem_id` fecha uma lacuna: até aqui, um Pix pago
minutos depois não tinha como ser ligado de volta ao horário reservado, e o
fluxo Pix nunca se completava.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-13 16:40:45.039001+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('eventos_webhook',
    sa.Column('provedor', sa.String(length=30), nullable=False),
    sa.Column('evento_id_externo', sa.String(length=120), nullable=False),
    sa.Column('tipo', sa.String(length=60), nullable=False),
    sa.Column('provedor_pagamento_id', sa.String(length=100), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('processado_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('erro', sa.Text(), nullable=True),
    sa.Column('tentativas', sa.Integer(), server_default='0', nullable=False),
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('atualizado_em', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_eventos_webhook')),
    sa.UniqueConstraint('provedor', 'evento_id_externo', name='uq_evento_webhook')
    )
    op.create_index('ix_eventos_webhook_pagamento', 'eventos_webhook', ['provedor_pagamento_id'], unique=False)
    op.create_index('ix_eventos_webhook_pendentes', 'eventos_webhook', ['criado_em'], unique=False, postgresql_where=sa.text('processado_em IS NULL'))
    op.add_column('pagamentos', sa.Column('agendamento_origem_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_pagamentos_agendamento_origem_id_agendamentos'), 'pagamentos', 'agendamentos', ['agendamento_origem_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_pagamentos_agendamento_origem_id_agendamentos'), 'pagamentos', type_='foreignkey')
    op.drop_column('pagamentos', 'agendamento_origem_id')
    op.drop_index('ix_eventos_webhook_pendentes', table_name='eventos_webhook', postgresql_where=sa.text('processado_em IS NULL'))
    op.drop_index('ix_eventos_webhook_pagamento', table_name='eventos_webhook')
    op.drop_table('eventos_webhook')
