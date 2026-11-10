"""Persistent memory contracts (B1)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from kenwer_c3.domain.common import DomainModel, Provenance


class FactStatus(str, Enum):
    active = "active"
    superseded = "superseded"
    contradicted = "contradicted"
    withdrawn = "withdrawn"


class PersistentFactCreate(DomainModel):
    cognitive_identity_id: UUID
    fact_key: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=128)
    value_json: dict[str, Any]
    unit: str | None = None
    description: str | None = None
    source_system: str = Field(min_length=1, max_length=128)
    source_event_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    valid_from: datetime | None = None
    # If true and value conflicts with active fact, mark prior contradicted and create new version
    allow_controlled_update: bool = False


class PersistentFactRead(DomainModel):
    id: UUID
    tenant_id: UUID
    cognitive_identity_id: UUID
    fact_key: str
    category: str
    value_json: dict[str, Any]
    unit: str | None
    description: str | None
    version: int
    status: FactStatus
    valid_from: datetime
    valid_to: datetime | None
    provenance: dict[str, Any]
    source_system: str
    source_event_id: str | None
    created_at: datetime
    updated_at: datetime


class PersistentFactWriteResult(DomainModel):
    fact: PersistentFactRead
    created: bool
    superseded_fact_id: UUID | None = None
    contradiction_detected: bool = False
    message: str
