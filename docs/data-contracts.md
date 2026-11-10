# Data Contracts

Pydantic models: `src/kenwer_c3/domain/`. ORM: `src/kenwer_c3/db/models.py`.

## Identity

| Contract | Key fields |
|----------|------------|
| Cognitive identity scope | `tenant_id`, `cognitive_identity_id`, optional `fleet_id` / `drone_id` / `platform_type` |
| Principal (auth) | `tenant_id`, `role`, optional bound `cognitive_identity_id` |

## Persistent memory

**`PersistentFactCreate` / `PersistentFactRead`**

- Versioned facts with `status` (`active` \| `superseded` \| `contradicted` \| `withdrawn`)
- `allow_controlled_update` required to supersede differing values
- Provenance + `source_system` required

## Episodic memory

**`MissionEpisodeCreate`**, **`FreezeExpectationRequest`**, **`MissionEventIngest`**, **`EpisodeFinalizeRequest`**

- Sealed expectations immutable after freeze
- Event ingest idempotent on `(tenant_id, episode_id, event_idempotency_key)`

## Consolidated memory

**`BeliefRead` / `BeliefVersionRead`**

- Retrieval returns **approved** only
- `confidence` + `confidence_basis` + optional `evidence_support_label`
- Numeric model scores are uncalibrated

## Learning artifacts

| Artifact | Produced by | Published? |
|----------|-------------|------------|
| `DiscrepancyReport` | C3 | No |
| `AttributionHypothesis` | C4 | No |
| `CandidateLesson` | C5 | No |
| `BeliefProposal` | C6 | No (pending review) |
| `BeliefVersion` approved | Publication service after validation + review | Yes |
| `ValidationDecision` | Review/publication | Audit |

## Retrieval

**`CognitiveRetrievalRequest` / `CognitiveContextResponse`**

- Memory types: persistent, consolidated, optional episodic
- `no_applicable_knowledge`, `knowledge_version`, `filters_applied`, `limitations`

## Review

**`ProposalReviewRequest`**: `APPROVE` \| `REJECT` \| `DEFER`  
**`BeliefRollbackRequest`**: restore prior version without deleting history

## Invariants

1. Body `tenant_id` is ignored for authorization; principal tenant wins.
2. Sealed expectations cannot be rewritten.
3. Duplicate event keys are idempotent successes.
4. Unapproved beliefs are never exposed as active consolidated knowledge.
5. C4–C5 never mutate published belief content / current version / approval state.
6. Similarity ranks candidates only - never alone decides agreement or contradiction.
