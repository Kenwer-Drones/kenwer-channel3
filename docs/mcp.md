# Kenwer Channel 3 MCP Server

Uses the **official Python MCP SDK** (`mcp` 2.x `MCPServer`).

## Run

```bash
export KENWER_MCP_API_KEY=dev-retrieve-key   # or reviewer/admin
export DATABASE_URL=postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3
python -m kenwer_c3.mcp.server
# or: kenwer-c3-mcp
```

Transport: **stdio**.

## Tool groups

| Group | Tools | Roles |
|-------|-------|-------|
| Memory retrieval | `retrieve_cognitive_context`, `list_approved_beliefs`, `list_persistent_facts` | retrieve / reviewer / admin |
| Mission evidence | `get_mission_episode`, `list_mission_events` | retrieve / ingest / reviewer / admin |
| Offline evaluation | `submit_learning_job`, `get_learning_job_status` | ingest/admin submit; broader status read |
| Knowledge review | `submit_knowledge_review` | reviewer / admin only |

## Guards

- Same API-key RBAC and tenant/identity isolation as REST
- Read-only default; review cannot bypass validation
- No MAVLink / flight-control tools
- Review path uses `BeliefPublicationService` only

## Testing

Real client sessions: `tests/test_phase6_mcp.py` (`ClientSession` + `stdio_client`).
