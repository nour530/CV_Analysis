"""add cv text layout and ats report

Revision ID: 491a8818143d
Revises: 1ff890a45d26
Create Date: 2026-11-14 20:33:01.952838

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '491a8818143d'
down_revision: Union[str, Sequence[str], None] = '1ff890a45d26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("cv_analyses", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resume_text", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("layout_json", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("ats_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("cv_analyses", schema=None) as batch_op:
        batch_op.drop_column("ats_json")
        batch_op.drop_column("layout_json")
        batch_op.drop_column("resume_text")
