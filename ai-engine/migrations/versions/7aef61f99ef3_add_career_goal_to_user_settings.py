"""add career goal to user settings

Revision ID: 7aef61f99ef3
Revises: 4285bbd55aa7
Create Date: 2026-11-15 19:41:07.111041

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7aef61f99ef3'
down_revision: Union[str, Sequence[str], None] = '4285bbd55aa7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("user_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("target_domain", sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column("target_role", sa.String(length=160), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("user_settings", schema=None) as batch_op:
        batch_op.drop_column("target_role")
        batch_op.drop_column("target_domain")
