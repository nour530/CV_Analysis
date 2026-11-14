"""add job applications

Revision ID: 4285bbd55aa7
Revises: 3c5cc24007e5
Create Date: 2026-11-14 21:28:11.314998

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4285bbd55aa7'
down_revision: Union[str, Sequence[str], None] = '3c5cc24007e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_applications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("location", sa.String(length=300), nullable=True),
        sa.Column("apply_url", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("resume_id", sa.String(length=36), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["resume_id"], ["cv_documents.id"],
            name=op.f("fk_job_applications_resume_id_cv_documents"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name=op.f("fk_job_applications_user_id_users"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_applications")),
        sa.UniqueConstraint("user_id", "job_id", name="uq_job_applications_user_job"),
    )
    with op.batch_alter_table("job_applications", schema=None) as batch_op:
        batch_op.create_index("ix_job_applications_user_updated", ["user_id", "updated_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_applications_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("job_applications", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_applications_user_id"))
        batch_op.drop_index("ix_job_applications_user_updated")
    op.drop_table("job_applications")
