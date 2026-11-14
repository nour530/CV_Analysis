"""add cv documents

Revision ID: 3c5cc24007e5
Revises: 491a8818143d
Create Date: 2026-11-14 21:09:04.294004

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3c5cc24007e5'
down_revision: Union[str, Sequence[str], None] = '491a8818143d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cv_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=False),
        sa.Column("template", sa.String(length=20), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column("ats_score", sa.Integer(), nullable=True),
        sa.Column("source_analysis_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_analysis_id"], ["cv_analyses.id"],
            name=op.f("fk_cv_documents_source_analysis_id_cv_analyses"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_cv_documents_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cv_documents")),
    )
    with op.batch_alter_table("cv_documents", schema=None) as batch_op:
        batch_op.create_index("ix_cv_documents_user_updated", ["user_id", "updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_cv_documents_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("cv_documents", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cv_documents_user_id"))
        batch_op.drop_index("ix_cv_documents_user_updated")
    op.drop_table("cv_documents")
