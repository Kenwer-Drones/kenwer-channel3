# Reconciliation similarity - limitations and policy

## Token Jaccard (temporary lexical fallback)

C6 reconciliation still uses token Jaccard overlap on claim text as a **lexical
fallback** when comparing candidate lessons to published beliefs.

### Limitations

- Sensitive to wording and tokenization; paraphrases score low even when meaning matches.
- Blind to negation / modality (“may correlate” vs “does not correlate”).
- Does not encode platform/site/fleet semantics - those must come from applicability fields.
- Overlap scores are **not** calibrated probabilities of agreement.

## Semantic candidate retrieval

Where embeddings exist (or can be derived deterministically for local ranking),
Channel 3 may use cosine similarity **only** to rank candidate beliefs/facts
**after** deterministic filters:

1. Tenant isolation
2. Cognitive-identity scope
3. Approval / validity status (approved + active only for consolidated)
4. Platform, fleet, site, mission, environment applicability (hard mismatch rejects)

## Non-negotiable rule

**Never use similarity alone to determine agreement or contradiction.**

Agreement / contradiction / CHALLENGE decisions require structured applicability
comparison plus human review for publication. Semantic scores are ranking hints
for candidate sets, not truth values.
