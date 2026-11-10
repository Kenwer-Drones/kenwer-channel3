# Channel 1 integration

Channel 1 plans and validates missions. Channel 3 supplies **approved** cognitive context only.

## Sequence

1. Authenticate with a `channel1_retrieve` (or admin/reviewer) API key
2. `POST /v1/retrieve/cognitive-context` with identity, mission/platform/site/fleet/environment, optional query text
3. Consume `persistent_facts` + `approved_beliefs` (+ optional episodic historical context)
4. Apply Channel 1’s own validation — Channel 3 retrieval **never** issues flight commands

## Guarantees

- Only `approval_status=approved` belief versions are returned as active knowledge
- Proposed / rejected / superseded / rolled_back versions are excluded
- Deterministic tenant + applicability filters run **before** semantic ranking
- Responses include `confidence_basis`, optional `evidence_support_label`, evidence refs, limitations, `knowledge_version`
- Empty applicable set → `no_applicable_knowledge=true`

## MCP equivalent

- `retrieve_cognitive_context`
- `list_approved_beliefs`
- `list_persistent_facts`

Same RBAC as REST.
