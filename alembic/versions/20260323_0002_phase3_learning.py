"""Phase 3: expectation immutability trigger, plan version, learning job guards.

Revision ID: 20260323_0002
Revises: 20260323_0001
Create Date: 2026-03-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260323_0002"
down_revision: Union[str, None] = "20260323_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "expectation_snapshots",
        sa.Column("mission_plan_version", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "expectation_snapshots",
        sa.Column(
            "approved_plan_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.add_column(
        "learning_jobs",
        sa.Column("celery_task_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "learning_jobs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "learning_jobs",
        sa.Column(
            "idempotency_key",
            sa.String(length=255),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_learning_jobs_idempotency",
        "learning_jobs",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    # At most one queued/running evaluation per episode
    op.create_index(
        "uq_learning_jobs_active_episode",
        "learning_jobs",
        ["tenant_id", "episode_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )

    op.add_column(
        "discrepancy_reports",
        sa.Column("result_status", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "discrepancy_reports",
        sa.Column("unit", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "discrepancy_reports",
        sa.Column("comparison_operator", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "discrepancy_reports",
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(
        "discrepancy_reports",
        sa.Column(
            "measurement_quality",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    # Hard immutability for sealed expectation content
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_sealed_expectation_mutation()
        RETURNS trigger AS $$
        BEGIN
          IF OLD.is_sealed IS TRUE THEN
            IF NEW.criteria_json IS DISTINCT FROM OLD.criteria_json
               OR NEW.metric_baselines IS DISTINCT FROM OLD.metric_baselines
               OR NEW.source_system IS DISTINCT FROM OLD.source_system
               OR NEW.source_event_id IS DISTINCT FROM OLD.source_event_id
               OR NEW.captured_at IS DISTINCT FROM OLD.captured_at
               OR NEW.mission_plan_version IS DISTINCT FROM OLD.mission_plan_version
               OR NEW.approved_plan_snapshot IS DISTINCT FROM OLD.approved_plan_snapshot
               OR NEW.is_sealed IS DISTINCT FROM OLD.is_sealed
               OR NEW.episode_id IS DISTINCT FROM OLD.episode_id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.cognitive_identity_id IS DISTINCT FROM OLD.cognitive_identity_id
            THEN
              RAISE EXCEPTION
                'sealed expectation snapshots are immutable after pre-execution approval';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_expectation_immutable ON expectation_snapshots;
        CREATE TRIGGER trg_expectation_immutable
        BEFORE UPDATE ON expectation_snapshots
        FOR EACH ROW
        EXECUTE PROCEDURE prevent_sealed_expectation_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_expectation_immutable ON expectation_snapshots;")
    op.execute("DROP FUNCTION IF EXISTS prevent_sealed_expectation_mutation();")
    op.drop_column("discrepancy_reports", "measurement_quality")
    op.drop_column("discrepancy_reports", "evidence_refs")
    op.drop_column("discrepancy_reports", "comparison_operator")
    op.drop_column("discrepancy_reports", "unit")
    op.drop_column("discrepancy_reports", "result_status")
    op.drop_index("uq_learning_jobs_active_episode", table_name="learning_jobs")
    op.drop_index("ix_learning_jobs_idempotency", table_name="learning_jobs")
    op.drop_column("learning_jobs", "idempotency_key")
    op.drop_column("learning_jobs", "attempt_count")
    op.drop_column("learning_jobs", "celery_task_id")
    op.drop_column("expectation_snapshots", "approved_plan_snapshot")
    op.drop_column("expectation_snapshots", "mission_plan_version")
