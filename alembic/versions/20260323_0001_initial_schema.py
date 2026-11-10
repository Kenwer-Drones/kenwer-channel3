"""Initial Channel 3 schema: identity, three memories, learning artifacts.

Revision ID: 20260323_0001
Revises:
Create Date: 2026-03-23

Creates:
- tenants, cognitive_identities, api_keys, audit_logs
- persistent_facts (B1)
- mission_episodes, expectation_snapshots (immutable), mission_events (append-only),
  evidence_artifacts (B2)
- beliefs, belief_versions, belief_evidence_links, validation_decisions (B3)
- learning_jobs, discrepancy_reports, attribution_hypotheses, candidate_lessons,
  belief_proposals
- pgvector extension + vector(384) columns on facts and belief versions
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260323_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "cognitive_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("fleet_id", sa.String(128)),
        sa.Column("drone_id", sa.String(128)),
        sa.Column("platform_type", sa.String(128)),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_cognitive_identity_tenant_slug"),
    )
    op.create_index(
        "ix_cognitive_identities_tenant", "cognitive_identities", ["tenant_id"]
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_prefix", sa.String(32), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="SET NULL"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "key_prefix", name="uq_api_keys_tenant_prefix"),
    )
    op.create_index("ix_api_keys_tenant", "api_keys", ["tenant_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_key_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource_type", sa.String(128), nullable=False),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("memory_family", sa.String(32)),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_audit_logs_tenant_created", "audit_logs", ["tenant_id", "created_at"]
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    # --- B1 Persistent ---
    op.create_table(
        "persistent_facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fact_key", sa.String(255), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column("unit", sa.String(64)),
        sa.Column("description", sa.Text()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column(
            "valid_from",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_system", sa.String(128), nullable=False),
        sa.Column("source_event_id", sa.String(128)),
        sa.Column("embedding", Vector(384)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_persistent_facts_version_positive"),
        sa.UniqueConstraint(
            "tenant_id",
            "cognitive_identity_id",
            "fact_key",
            "version",
            name="uq_persistent_fact_key_version",
        ),
    )
    op.create_index(
        "ix_persistent_facts_scope_key",
        "persistent_facts",
        ["tenant_id", "cognitive_identity_id", "fact_key"],
    )
    op.create_index("ix_persistent_facts_status", "persistent_facts", ["status"])

    # --- B2 Episodic ---
    op.create_table(
        "mission_episodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_mission_id", sa.String(128), nullable=False),
        sa.Column("mission_type", sa.String(128), nullable=False),
        sa.Column(
            "objectives",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "approved_plan",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "site_context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "platform_context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "lifecycle_state",
            sa.String(32),
            nullable=False,
            server_default="planned",
        ),
        sa.Column("actual_outcomes", postgresql.JSONB()),
        sa.Column(
            "missing_data",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "external_mission_id", name="uq_mission_episode_external"
        ),
    )
    op.create_index(
        "ix_mission_episodes_identity_state",
        "mission_episodes",
        ["tenant_id", "cognitive_identity_id", "lifecycle_state"],
    )
    op.create_index(
        "ix_mission_episodes_mission_type", "mission_episodes", ["mission_type"]
    )

    op.create_table(
        "expectation_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("criteria_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metric_baselines",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_system", sa.String(128), nullable=False),
        sa.Column("source_event_id", sa.String(128)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "is_sealed", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.UniqueConstraint("episode_id", name="uq_expectation_snapshot_episode"),
    )
    op.create_index(
        "ix_expectation_snapshots_tenant", "expectation_snapshots", ["tenant_id"]
    )

    op.create_table(
        "mission_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_idempotency_key", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("sequence_no", sa.Integer()),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_system", sa.String(128), nullable=False),
        sa.Column("source_quality", postgresql.JSONB()),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "episode_id",
            "event_idempotency_key",
            name="uq_mission_event_idempotency",
        ),
    )
    op.create_index(
        "ix_mission_events_episode_time",
        "mission_events",
        ["tenant_id", "episode_id", "recorded_at"],
    )
    op.create_index("ix_mission_events_type", "mission_events", ["event_type"])

    op.create_table(
        "evidence_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artifact_key", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_system", sa.String(128), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "episode_id",
            "artifact_key",
            name="uq_evidence_artifact_key",
        ),
    )
    op.create_index(
        "ix_evidence_artifacts_episode",
        "evidence_artifacts",
        ["tenant_id", "episode_id"],
    )

    # --- B3 Consolidated ---
    op.create_table(
        "beliefs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("belief_key", sa.String(255), nullable=False),
        sa.Column("current_version_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "cognitive_identity_id",
            "belief_key",
            name="uq_belief_scope_key",
        ),
    )
    op.create_index(
        "ix_beliefs_tenant_identity",
        "beliefs",
        ["tenant_id", "cognitive_identity_id"],
    )

    op.create_table(
        "belief_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "belief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("beliefs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("value_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "applicability",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "approval_status",
            sa.String(32),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column(
            "safety_impact",
            sa.String(32),
            nullable=False,
            server_default="operational",
        ),
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("limitations", sa.Text()),
        sa.Column(
            "supersedes_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("belief_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("embedding", Vector(384)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name="ck_belief_versions_version_positive"),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_belief_versions_confidence_range",
        ),
        sa.UniqueConstraint("belief_id", "version", name="uq_belief_version_number"),
    )
    op.create_index(
        "ix_belief_versions_status",
        "belief_versions",
        ["tenant_id", "approval_status"],
    )
    op.create_index("ix_belief_versions_safety", "belief_versions", ["safety_impact"])

    op.create_table(
        "belief_evidence_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "belief_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("belief_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evidence_artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence_artifacts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_episodes.id", ondelete="SET NULL"),
        ),
        sa.Column("relation", sa.String(64), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "belief_version_id",
            "evidence_artifact_id",
            "relation",
            name="uq_belief_evidence_link",
        ),
    )

    op.create_table(
        "validation_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "belief_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("belief_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reviewer_key_id", postgresql.UUID(as_uuid=True)),
        sa.Column("rationale", sa.Text()),
        sa.Column(
            "checks_passed",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_validation_decisions_belief_version",
        "validation_decisions",
        ["belief_version_id"],
    )
    op.create_index(
        "ix_validation_decisions_tenant_created",
        "validation_decisions",
        ["tenant_id", "created_at"],
    )

    # --- Learning pipeline (not consolidated memory) ---
    op.create_table(
        "learning_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "cognitive_identity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("current_stage", sa.String(32)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "stage_artifacts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_learning_jobs_episode", "learning_jobs", ["tenant_id", "episode_id"]
    )
    op.create_index("ix_learning_jobs_status", "learning_jobs", ["status"])

    op.create_table(
        "discrepancy_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "learning_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("expected_value", postgresql.JSONB(), nullable=False),
        sa.Column("observed_value", postgresql.JSONB(), nullable=False),
        sa.Column("discrepancy", postgresql.JSONB(), nullable=False),
        sa.Column(
            "uncertainty",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "missing_evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_discrepancy_reports_job", "discrepancy_reports", ["learning_job_id"]
    )

    op.create_table(
        "attribution_hypotheses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "learning_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "discrepancy_report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("discrepancy_reports.id", ondelete="SET NULL"),
        ),
        sa.Column("hypothesis", sa.Text(), nullable=False),
        sa.Column("competing_group_id", postgresql.UUID(as_uuid=True)),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "is_hypothesis_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("model_id", sa.String(128)),
        sa.Column("prompt_hash", sa.String(64)),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_attribution_hypotheses_job",
        "attribution_hypotheses",
        ["learning_job_id"],
    )

    op.create_table(
        "candidate_lessons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "learning_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "episode_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("mission_episodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column(
            "applicability",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "safety_impact",
            sa.String(32),
            nullable=False,
            server_default="operational",
        ),
        sa.Column(
            "is_hypothesis_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("model_id", sa.String(128)),
        sa.Column("prompt_hash", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_candidate_lessons_job", "candidate_lessons", ["learning_job_id"]
    )

    op.create_table(
        "belief_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "learning_job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("learning_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_lesson_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_lessons.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "target_belief_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("beliefs.id", ondelete="SET NULL"),
        ),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("proposed_payload", postgresql.JSONB(), nullable=False),
        sa.Column("contradiction_notes", sa.Text()),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="pending_review"
        ),
        sa.Column(
            "resulting_belief_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("belief_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_belief_proposals_job", "belief_proposals", ["learning_job_id"]
    )
    op.create_index("ix_belief_proposals_status", "belief_proposals", ["status"])


def downgrade() -> None:
    op.drop_table("belief_proposals")
    op.drop_table("candidate_lessons")
    op.drop_table("attribution_hypotheses")
    op.drop_table("discrepancy_reports")
    op.drop_table("learning_jobs")
    op.drop_table("validation_decisions")
    op.drop_table("belief_evidence_links")
    op.drop_table("belief_versions")
    op.drop_table("beliefs")
    op.drop_table("evidence_artifacts")
    op.drop_table("mission_events")
    op.drop_table("expectation_snapshots")
    op.drop_table("mission_episodes")
    op.drop_table("persistent_facts")
    op.drop_table("audit_logs")
    op.drop_table("api_keys")
    op.drop_table("cognitive_identities")
    op.drop_table("tenants")
    op.execute("DROP EXTENSION IF EXISTS vector")
