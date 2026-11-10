# Data Contracts

Pydantic models live under `src/kenwer_c3/domain/`. ORM tables live under `src/kenwer_c3/db/models.py`.

## Identity

| Contract | Key fields |
|----------|------------|
| Cognitive identity scope | `tenant_id`, `cognitive_identity_id`, optional `fleet_id` / `drone_id` / `platform_type` |
| Principal (auth) | `tenant_id`, `role`, optional bound `cognitive_identity_id` |

## Persistent memory

**`PersistentFactCreate`**

- `cognitive_identity_id`, `fact_key`, `category`, `value_json`
- `source_system`, optional `source_event_id`, `provenance`
- `allow_controlled_update` (default `false`) — required for versioned supersession when values differ

**`PersistentFactRead`**

- Includes `version`, `status` (`active` \| `superseded` \| `contradicted` \| `withdrawn`), validity window, provenance

## Episodic memory

**`MissionEpisodeCreate`**

- `external_mission_id`, `mission_type`, objectives/plan/site/platform contexts
- Optional nested expectation to freeze at create time

**`FreezeExpectationRequest` / `ExpectationSnapshotRead`**

- `criteria_json`, `metric_baselines`, `captured_at`, `is_sealed=true` after freeze
- Immutable after seal

**`MissionEventIngest`**

- `event_idempotency_key` (required), `event_type`, `payload`, `recorded_at`, `source_system`
- Optional `proposed_persistent_fact_key` — recorded in provenance only; does **not** mutate persistent memory

**`EpisodeFinalizeRequest`**

- `actual_outcomes`, `missing_data` (explicit missing evidence)

## Consolidated memory

**`BeliefRead` / `BeliefVersionRead`**

- Returned only when `approval_status == approved`
- Includes `applicability`, `confidence`, `safety_impact`, `provenance`, `limitations`, `published_at`

## Learning artifacts (schema present; services in later phases)

`DiscrepancyReport`, `AttributionHypothesis`, `CandidateLesson`, `BeliefProposal`, `ValidationDecision` — ORM tables exist; API/services arrive in Phases 3–5.

## Invariants

1. Body `tenant_id` is ignored for authorization; principal tenant wins.
2. Sealed expectations cannot be rewritten.
3. Duplicate event keys are idempotent successes, not errors.
4. Unapproved beliefs are not exposed on consolidated read APIs.
