"""add job postings

Revision ID: 1ff890a45d26
Revises: 31a227204242
Create Date: 2026-11-14 15:42:57.834073

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ff890a45d26'
down_revision: Union[str, Sequence[str], None] = '31a227204242'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "job_postings",
        sa.Column("id", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_company", sa.String(length=120), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("version", sa.String(length=200), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("company", sa.String(length=200), nullable=False),
        sa.Column("company_logo_url", sa.String(length=1000), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("location", sa.String(length=300), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("country_code", sa.String(length=2), nullable=True),
        sa.Column("workplace", sa.String(length=20), nullable=False),
        sa.Column("employment_type", sa.String(length=60), nullable=True),
        sa.Column("experience_level", sa.String(length=60), nullable=True),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("salary_text", sa.String(length=120), nullable=True),
        sa.Column("apply_url", sa.String(length=1000), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("required_skills", sa.JSON(), nullable=False),
        sa.Column("preferred_skills", sa.JSON(), nullable=False),
        sa.Column("other_skills", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_postings")),
        sa.UniqueConstraint(
            "source", "source_company", "external_id", name="uq_job_postings_source_key"
        ),
    )
    with op.batch_alter_table("job_postings", schema=None) as batch_op:
        batch_op.create_index(
            "ix_job_postings_active_posted", ["is_active", "posted_at"], unique=False
        )
        batch_op.create_index(
            "ix_job_postings_source_company", ["source", "source_company"], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("job_postings", schema=None) as batch_op:
        batch_op.drop_index("ix_job_postings_source_company")
        batch_op.drop_index("ix_job_postings_active_posted")
    op.drop_table("job_postings")
