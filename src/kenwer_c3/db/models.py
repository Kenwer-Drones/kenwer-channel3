"""ORM models for Channel 3.

Architectural invariants encoded here:
- Tenant + cognitive-identity scoping on all memory tables
- Three physically separate memory families (persistent / episodic / consolidated)
- Immutable pre-mission expectation snapshots (no UPDATE of content columns)
- Append-only mission events with idempotency keys
- Belief publication only via versioned rows + validation decisions (no silent overwrite)
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from kenwer_c3.db.base import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ApiKeyRole(str, enum.Enum):
    channel2_ingest = "channel2_ingest"
    channel1_retrieve = "channel1_retrieve"
    reviewer = "reviewer"
    admin = "admin"


class EpisodeLifecycleState(str, enum.Enum):
    planned = "planned"
    active = "active"
    finalized = "finalized"
    aborted = "aborted"


class FactStatus(str, enum.Enum):
    active = "active"
    superseded = "superseded"
    contradicted = "contradicted"
    withdrawn = "withdrawn"


class BeliefApprovalStatus(str, enum.Enum):
    draft = "draft"
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    deferred = "deferred"
    superseded = "superseded"
    rolled_back = "rolled_back"


class BeliefProposalOperation(str, enum.Enum):
    create = "CREATE"
    reinforce = "REINFORCE"
    refine = "REFINE"
    revise = "REVISE"
    challenge = "CHALLENGE"


class SafetyImpact(str, enum.Enum):
    none = "none"
    operational = "operational"
    safety_relevant = "safety_relevant"
    policy_adjacent = "policy_adjacent"


class LearningJobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    failed_soft = "failed_soft"


class ValidationAction(str, enum.Enum):
    approve = "approve"
    reject = "reject"
    defer = "defer"


class MemoryFamily(str, enum.Enum):
    """Logical memory family — kept as an enum for routing/audit clarity."""

    persistent = "persistent"
    episodic = "episodic"
    consolidated = "consolidated"


# ---------------------------------------------------------------------------
# Identity / auth / audit
# ---------------------------------------------------------------------------


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    identities: Mapped[list[CognitiveIdentity]] = relationship(back_populates="tenant")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="tenant")


class CognitiveIdentity(Base):
    """Scoped cognitive subject (fleet/drone/persona) within a tenant."""

    __tablename__ = "cognitive_identities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_cognitive_identity_tenant_slug"),
        Index("ix_cognitive_identities_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    fleet_id: Mapped[Optional[str]] = mapped_column(String(128))
    drone_id: Mapped[Optional[str]] = mapped_column(String(128))
    platform_type: Mapped[Optional[str]] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="identities")


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key_prefix", name="uq_api_keys_tenant_prefix"),
        Index("ix_api_keys_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    key_prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[ApiKeyRole] = mapped_column(
        Enum(ApiKeyRole, name="api_key_role", native_enum=False), nullable=False
    )
    cognitive_identity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="SET NULL"),
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tenant: Mapped[Tenant] = relationship(back_populates="api_keys")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_logs_action", "action"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[Optional[str]] = mapped_column(String(128))
    memory_family: Mapped[Optional[MemoryFamily]] = mapped_column(
        Enum(MemoryFamily, name="memory_family", native_enum=False)
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# B1 — Persistent memory
# ---------------------------------------------------------------------------


class PersistentFact(Base):
    """Durable verified operational fact (capabilities, site, preferences)."""

    __tablename__ = "persistent_facts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "cognitive_identity_id",
            "fact_key",
            "version",
            name="uq_persistent_fact_key_version",
        ),
        Index(
            "ix_persistent_facts_scope_key",
            "tenant_id",
            "cognitive_identity_id",
            "fact_key",
        ),
        Index("ix_persistent_facts_status", "status"),
        CheckConstraint("version >= 1", name="ck_persistent_facts_version_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    fact_key: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(64))
    description: Mapped[Optional[str]] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[FactStatus] = mapped_column(
        Enum(FactStatus, name="fact_status", native_enum=False),
        nullable=False,
        default=FactStatus.active,
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[Optional[str]] = mapped_column(String(128))
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(384))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# B2 — Episodic memory
# ---------------------------------------------------------------------------


class MissionEpisode(Base):
    __tablename__ = "mission_episodes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "external_mission_id", name="uq_mission_episode_external"
        ),
        Index(
            "ix_mission_episodes_identity_state",
            "tenant_id",
            "cognitive_identity_id",
            "lifecycle_state",
        ),
        Index("ix_mission_episodes_mission_type", "mission_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_mission_id: Mapped[str] = mapped_column(String(128), nullable=False)
    mission_type: Mapped[str] = mapped_column(String(128), nullable=False)
    objectives: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    approved_plan: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    site_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    platform_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    lifecycle_state: Mapped[EpisodeLifecycleState] = mapped_column(
        Enum(EpisodeLifecycleState, name="episode_lifecycle_state", native_enum=False),
        nullable=False,
        default=EpisodeLifecycleState.planned,
    )
    actual_outcomes: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    missing_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    expectation: Mapped[Optional[ExpectationSnapshot]] = relationship(
        back_populates="episode", uselist=False
    )
    events: Mapped[list[MissionEvent]] = relationship(back_populates="episode")
    evidence_artifacts: Mapped[list[EvidenceArtifact]] = relationship(
        back_populates="episode"
    )


class ExpectationSnapshot(Base):
    """Immutable pre-mission success criteria.

    Content columns must never be updated after insert. Application services
    enforce immutability; DB has no content-update path by convention.
    """

    __tablename__ = "expectation_snapshots"
    __table_args__ = (
        UniqueConstraint("episode_id", name="uq_expectation_snapshot_episode"),
        Index("ix_expectation_snapshots_tenant", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Frozen payload — never mutate after insert (DB trigger enforces when is_sealed)
    criteria_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metric_baselines: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    mission_plan_version: Mapped[Optional[str]] = mapped_column(String(128))
    approved_plan_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[Optional[str]] = mapped_column(String(128))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Soft lock marker: once true, services refuse any write; trigger hard-blocks UPDATEs
    is_sealed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    episode: Mapped[MissionEpisode] = relationship(back_populates="expectation")


class MissionEvent(Base):
    """Append-only timestamped mission event / observation / decision."""

    __tablename__ = "mission_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "episode_id",
            "event_idempotency_key",
            name="uq_mission_event_idempotency",
        ),
        Index(
            "ix_mission_events_episode_time",
            "tenant_id",
            "episode_id",
            "recorded_at",
        ),
        Index("ix_mission_events_type", "event_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sequence_no: Mapped[Optional[int]] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    source_quality: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    episode: Mapped[MissionEpisode] = relationship(back_populates="events")


class EvidenceArtifact(Base):
    __tablename__ = "evidence_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "episode_id",
            "artifact_key",
            name="uq_evidence_artifact_key",
        ),
        Index("ix_evidence_artifacts_episode", "tenant_id", "episode_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_key: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_system: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    episode: Mapped[MissionEpisode] = relationship(back_populates="evidence_artifacts")


# ---------------------------------------------------------------------------
# B3 — Consolidated memory (beliefs + versions; publication gated)
# ---------------------------------------------------------------------------


class Belief(Base):
    """Logical belief identity. Published content lives on BeliefVersion."""

    __tablename__ = "beliefs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "cognitive_identity_id",
            "belief_key",
            name="uq_belief_scope_key",
        ),
        Index("ix_beliefs_tenant_identity", "tenant_id", "cognitive_identity_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    belief_key: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    versions: Mapped[list[BeliefVersion]] = relationship(
        back_populates="belief",
        foreign_keys="BeliefVersion.belief_id",
    )


class BeliefVersion(Base):
    __tablename__ = "belief_versions"
    __table_args__ = (
        UniqueConstraint(
            "belief_id", "version", name="uq_belief_version_number"
        ),
        Index(
            "ix_belief_versions_status",
            "tenant_id",
            "approval_status",
        ),
        Index("ix_belief_versions_safety", "safety_impact"),
        CheckConstraint("version >= 1", name="ck_belief_versions_version_positive"),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_belief_versions_confidence_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    belief_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("beliefs.id", ondelete="CASCADE"), nullable=False
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    applicability: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    approval_status: Mapped[BeliefApprovalStatus] = mapped_column(
        Enum(BeliefApprovalStatus, name="belief_approval_status", native_enum=False),
        nullable=False,
        default=BeliefApprovalStatus.proposed,
    )
    safety_impact: Mapped[SafetyImpact] = mapped_column(
        Enum(SafetyImpact, name="safety_impact", native_enum=False),
        nullable=False,
        default=SafetyImpact.operational,
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    limitations: Mapped[Optional[str]] = mapped_column(Text)
    supersedes_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("belief_versions.id", ondelete="SET NULL")
    )
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(384))
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    belief: Mapped[Belief] = relationship(
        back_populates="versions",
        foreign_keys=[belief_id],
    )
    evidence_links: Mapped[list[BeliefEvidenceLink]] = relationship(
        back_populates="belief_version"
    )


class BeliefEvidenceLink(Base):
    __tablename__ = "belief_evidence_links"
    __table_args__ = (
        UniqueConstraint(
            "belief_version_id",
            "evidence_artifact_id",
            "relation",
            name="uq_belief_evidence_link",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    belief_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("belief_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_artifact_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_artifacts.id", ondelete="SET NULL"),
    )
    episode_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_episodes.id", ondelete="SET NULL"),
    )
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    # supporting | contradictory | contextual
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    belief_version: Mapped[BeliefVersion] = relationship(back_populates="evidence_links")


class ValidationDecision(Base):
    __tablename__ = "validation_decisions"
    __table_args__ = (
        Index("ix_validation_decisions_belief_version", "belief_version_id"),
        Index("ix_validation_decisions_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    belief_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("belief_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[ValidationAction] = mapped_column(
        Enum(ValidationAction, name="validation_action", native_enum=False),
        nullable=False,
    )
    reviewer_key_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    rationale: Mapped[Optional[str]] = mapped_column(Text)
    checks_passed: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Learning pipeline artifacts (not consolidated memory)
# ---------------------------------------------------------------------------


class LearningJob(Base):
    __tablename__ = "learning_jobs"
    __table_args__ = (
        Index("ix_learning_jobs_episode", "tenant_id", "episode_id"),
        Index("ix_learning_jobs_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    cognitive_identity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cognitive_identities.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[LearningJobStatus] = mapped_column(
        Enum(LearningJobStatus, name="learning_job_status", native_enum=False),
        nullable=False,
        default=LearningJobStatus.queued,
    )
    current_stage: Mapped[Optional[str]] = mapped_column(String(32))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    stage_artifacts: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class DiscrepancyReport(Base):
    __tablename__ = "discrepancy_reports"
    __table_args__ = (Index("ix_discrepancy_reports_job", "learning_job_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    learning_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    expected_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observed_value: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    discrepancy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_status: Mapped[Optional[str]] = mapped_column(String(32))
    unit: Mapped[Optional[str]] = mapped_column(String(64))
    comparison_operator: Mapped[Optional[str]] = mapped_column(String(32))
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    measurement_quality: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    uncertainty: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    missing_evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AttributionHypothesis(Base):
    __tablename__ = "attribution_hypotheses"
    __table_args__ = (Index("ix_attribution_hypotheses_job", "learning_job_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    learning_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    discrepancy_report_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("discrepancy_reports.id", ondelete="SET NULL"),
    )
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    competing_group_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    is_hypothesis_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    model_id: Mapped[Optional[str]] = mapped_column(String(128))
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64))
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CandidateLesson(Base):
    __tablename__ = "candidate_lessons"
    __table_args__ = (Index("ix_candidate_lessons_job", "learning_job_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    learning_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    episode_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mission_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    applicability: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    safety_impact: Mapped[SafetyImpact] = mapped_column(
        Enum(SafetyImpact, name="safety_impact", native_enum=False),
        nullable=False,
        default=SafetyImpact.operational,
    )
    is_hypothesis_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    model_id: Mapped[Optional[str]] = mapped_column(String(128))
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BeliefProposal(Base):
    """C6 reconciliation output — never auto-published into consolidated memory."""

    __tablename__ = "belief_proposals"
    __table_args__ = (
        Index("ix_belief_proposals_job", "learning_job_id"),
        Index("ix_belief_proposals_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    learning_job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("learning_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_lesson_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_lessons.id", ondelete="SET NULL"),
    )
    target_belief_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("beliefs.id", ondelete="SET NULL"),
    )
    operation: Mapped[BeliefProposalOperation] = mapped_column(
        Enum(
            BeliefProposalOperation,
            name="belief_proposal_operation",
            native_enum=False,
        ),
        nullable=False,
    )
    proposed_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    contradiction_notes: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_review"
    )
    resulting_belief_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("belief_versions.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
