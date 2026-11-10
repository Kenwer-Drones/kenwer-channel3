"""Phase 5: belief proposal review fields and confidence basis labeling.

Revision ID: 20260323_0003
Revises: 20260323_0002
Create Date: 2026-03-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260323_0003"
down_revision: Union[str, None] = "20260323_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "belief_versions",
        sa.Column(
            "confidence_basis",
            sa.String(length=64),
            nullable=False,
            server_default="uncalibrated_model",
        ),
    )
    op.add_column(
        "belief_versions",
        sa.Column("evidence_support_label", sa.String(length=32), nullable=True),
    )

    op.add_column(
        "belief_proposals",
        sa.Column("cognitive_identity_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "belief_proposals",
        sa.Column(
            "validation_report",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "belief_proposals",
        sa.Column("reviewer_key_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "belief_proposals",
        sa.Column("review_rationale", sa.Text(), nullable=True),
    )
    op.add_column(
        "belief_proposals",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "belief_proposals",
        sa.Column(
            "flags_existing_for_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index(
        "ix_belief_proposals_tenant_status",
        "belief_proposals",
        ["tenant_id", "status"],
    )

    op.add_column(
        "validation_decisions",
        sa.Column(
            "belief_proposal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("belief_proposals.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("validation_decisions", "belief_proposal_id")
    op.drop_index("ix_belief_proposals_tenant_status", table_name="belief_proposals")
    op.drop_column("belief_proposals", "flags_existing_for_review")
    op.drop_column("belief_proposals", "reviewed_at")
    op.drop_column("belief_proposals", "review_rationale")
    op.drop_column("belief_proposals", "reviewer_key_id")
    op.drop_column("belief_proposals", "validation_report")
    op.drop_column("belief_proposals", "cognitive_identity_id")
    op.drop_column("belief_versions", "evidence_support_label")
    op.drop_column("belief_versions", "confidence_basis")
