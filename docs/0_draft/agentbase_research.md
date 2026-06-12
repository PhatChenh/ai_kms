# AgentBase Research — What the Next Agent Needs to Know

_Created: 2026-06-12_
_Status: RESEARCH — gathered from VNG Cloud docs + GitHub skill files. Not a build plan._
_Audience: Next AI session doing design/spec/plan work for the cloud-native rearchitecture._
_Context: Read alongside `docs/0_draft/cloud_native_rearchitecture.md` (the architecture decision doc)._

---

## How to read this document

**Read in this order:**
1. §1 — Platform overview (2 min)
2. §2 — API surface and auth (3 min) — know this before any code
3. §3 — Runtime contract (3 min) — hard requirements for the container
4. §4 — Resource Gateway / MCP proxy (4 min) — how the MCP server is exposed
5. §5 — Deployment pipeline (3 min) — how Docker images become running agents
6. §6 — Identity and outbound auth (2 min) — how the agent calls external services
7. §7 — Memory service (2 min) — optional, but relevant to context injection
8. §8 — LLM / MaaS (1 min) — use AgentBase LLM instead of Anthropic API for cost control
9. §9 — Monitoring (1 min)
10. §10 — Python SDK (1 min)
11. **§11 — CRITICAL GAPS (read before designing)** — what is NOT documented, what is unknown
12. §12 — Source links

**Do NOT skip §11.** It contains the WebSocket gap and the SQLite persistence gap — two decisions the architecture doc assumed were solved but are not.

---

## 1. Platform Overview

AgentBase is VNG's enterprise infrastructure platform for AI agents. It solves: container lifecycle, credential security, tool access control, cost tracking, centralized logging. It is NOT a hosted LLM. It is NOT an agent framework. It is the infrastructure layer that agents run on.

**Two agent types:**
- **Custom Agent** — user Docker image, deployed via Container Registry, runs on port 8080
- **OpenClaw** — platform-templated Telegram/Zalo bots, no Docker required (not relevant to this project)

**Platform entry points:**
- Console: `https://aiplatform.console.vngcloud.vn`
- API: `https://agentbase.api.vngcloud.vn` (see §2 for service breakdown)
- GitHub skills: `https://github.com/vngcloud/greennode-agentbase-skills`

The project's deployment target is a **Custom Agent**. The MCP server (`src/mcp_server/`) runs inside the container and is exposed to Claude clients via the **Resource Gateway**.

---

## 2. API Surface and Authentication

### 2.1 Valid API Domains (whitelist)

Only these four domains are valid. Calls to anything else will be blocked:

| Domain | Purpose |
|---|---|
| `agentbase.api.vngcloud.vn` | AgentBase core services (identity, runtime, memory, policy, gateway, CR) |
| `aiplatform-hcm.api.vngcloud.vn` | AI Platform management (LLM API key management) |
| `maas-llm-aiplatform-hcm.api.vngcloud.vn/v1` | LLM inference — OpenAI-compatible endpoint |
| `iam.api.vngcloud.vn` | IAM token endpoint |

### 2.2 Service Base URLs

| Service | Base URL | Page indexing |
|---|---|---|
| Identity | `https://agentbase.api.vngcloud.vn/identity/api/v1` | 0-indexed |
| Runtime | `https://agentbase.api.vngcloud.vn/runtime` | 1-indexed |
| Memory | `https://agentbase.api.vngcloud.vn/memory` | 1-indexed |
| Policy | `https://agentbase.api.vngcloud.vn/policy/api/v1` | 1-indexed |
| MCP Gateway | `https://agentbase.api.vngcloud.vn/gateway/api/v1` | 1-indexed |
| Container Registry | `https://agentbase.api.vngcloud.vn/cr/api/v1` | 1-indexed |
| AI Platform mgmt | `https://aiplatform-hcm.api.vngcloud.vn` | 1-indexed |
| LLM inference | `https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1` | N/A |
| IAM token | `https://iam.api.vngcloud.vn/accounts-api/v2/auth/token` | N/A |

**Pagination differs by service:**
- Identity (Spring-style): `.content`, `.totalElements`, `.totalPages`, `.number` (0-indexed)
- Runtime/Memory/AI Platform: `.listData`, `.totalItem`, `.totalPage`, `.page` (1-indexed)
- MCP Gateway: `.items`, `.pagination.page`, `.pagination.totalItems`, `.pagination.hasMore`

### 2.3 IAM Authentication Setup

All API calls require a bearer token from an IAM service account. Setup:

**Step 1 — Create IAM service account:**
Go to `https://iam.console.vngcloud.vn/service-accounts`. Create account (e.g., `agentbase-dev`). Copy Client Secret immediately — shown only once.

**Step 2 — Attach policies:**
- `AgentBaseFullAccess`
- `vcrFullAccess`
- `AiPlatformFullAccess`

**Step 3 — Store credentials (two options):**

Option A — environment variables (Priority 1):
```bash
export GREENNODE_CLIENT_ID="<client-id>"
export GREENNODE_CLIENT_SECRET="<client-secret>"
```

Option B — `.greennode.json` file (Priority 2):
```json
{
  "client_id": "<client-id>",
  "client_secret": "<client-secret>"
}
```

**Step 4 — Get bearer token:**
```bash
TOKEN=$(bash .claude/skills/agentbase/scripts/get_token.sh)
```
Token is cached in `.agentbase/token_cache` with JWT expiry validation. On 401, use `--force` flag to bypass cache.

All API calls: `Authorization: Bearer $TOKEN`

### 2.4 Runtime Auto-Injection

When deployed on AgentBase, the platform auto-injects these env vars into the container — no manual setup needed:

- `GREENNODE_CLIENT_ID`
- `GREENNODE_CLIENT_SECRET`
- `GREENNODE_AGENT_IDENTITY` (agent identity ID)
- `GREENNODE_ENDPOINT_URL` (agent's own endpoint URL)

The Python SDK reads these automatically.

---

## 3. Runtime Contract (HARD REQUIREMENTS for the Container)

These are platform-enforced. Container that violates either will not become `ACTIVE`.

### 3.1 Port

**Container MUST listen on port 8080.** Platform routes all traffic here. No flexibility.

### 3.2 Health Check

**Expose `GET /health` returning HTTP 200 when ready.** Platform uses this to mark the runtime `ACTIVE`. Slow health check = slow startup. Failed health check = runtime stuck in `CREATING`.

### 3.3 Auto-Injected Environment Variables

Platform injects at container start (see §2.4). Container code reads them via SDK or directly from `os.environ`.

### 3.4 Request Headers (SDK Convention, Not Platform Requirement)

When using the `greennode-agentbase` Python SDK, these headers carry conversation context:

| Header | Purpose |
|---|---|
| `X-GreenNode-AgentBase-Session-Id` | Session context (required for memory/checkpointing) |
| `X-GreenNode-AgentBase-User-Id` | User context (required for memory features) |
| `X-GreenNode-AgentBase-Request-Id` | Request tracking (auto-generated if omitted) |
| `X-GreenNode-AgentBase-Custom-*` | Custom data forwarding |

FastMCP (the MCP server) runs on port 8080 and handles `/health` + MCP JSON-RPC routes. The Resource Gateway sits in front of it.

### 3.5 Shutdown and Resource Limits

Not explicitly documented. No documented graceful shutdown hook or container resource caps. Assume standard OCI container behavior.

---

## 4. Resource Gateway (MCP Proxy)

This is how the MCP server inside the container is exposed to Claude clients.

**Architecture:**
```
Claude Desktop / claude.ai
        │  MCP JSON-RPC
        ▼
Resource Gateway (managed proxy)
  - Inbound auth (NONE / IAM / JWT)
  - Policy enforcement (tools/call only)
  - Secret injection
        │
        ▼
Custom Agent container (port 8080)
  - FastMCP server
  - /health
```

### 4.1 Gateway Concepts

- **Gateway** — named proxy, 3–40 chars, lowercase alphanumeric + dashes only
- **Network Mode** — `PUBLIC` (internet-reachable) or `PRIVATE` (VPC subnet). **Cannot change after creation.**
- **Flavor** — compute sizing for the gateway itself
- **Replicas** — 1–10. Set at creation, cannot patch.
- **Targets** — upstream MCP servers (max 50 per gateway). Each target has a URL + outbound auth.
- **Inbound Auth** — how callers authenticate TO the gateway
- **Policy Group** — optional auth rules. **CRITICAL: gateway without policy group blocks ALL `tools/call` with 403.** `tools/list` always allowed.

### 4.2 Gateway Operations

Base URL: `https://agentbase.api.vngcloud.vn/gateway/api/v1`

| Operation | Method | Endpoint |
|---|---|---|
| List flavors | GET | `/flavors?resourceType=GATEWAY` |
| Create gateway | POST | `/gateways` |
| Get gateway | GET | `/gateways/{name}` |
| Update gateway | PATCH | `/gateways/{name}` |
| Delete gateway | DELETE | `/gateways/{name}` |
| List gateways | GET | `/gateways?page=1&pageSize=50` |

**Async operations:** POST, PATCH, DELETE return 202 Accepted. Poll `GET /gateways/{name}` until `state` is `ACTIVE`.

**Sealed fields (cannot patch — must delete and recreate):** `name`, `networkMode`, `vpcId`, `subnetId`, `flavorId`, `replicas`.

**Optimistic concurrency:** Use `ETag` header → `If-Match` on PATCH. Expect 412 if stale.

### 4.3 Inbound Auth Modes

| Mode | When to use |
|---|---|
| `NONE` | No caller auth. Anyone who reaches the endpoint can call. |
| `IAM` | Callers use VNG Cloud IAM bearer tokens. Zero config needed. |
| `JWT` | OIDC tokens (e.g., Okta, Auth0). Config: `discoveryUrl` or `jwks`, `principalClaim` (default: `sub`), `allowedAudiences`, `allowedClients`, `allowedScopes`, `customClaims`. |

For the MCP server accessible by Claude Desktop: `NONE` is simplest for MVP. `IAM` is appropriate for team/enterprise use.

### 4.4 Outbound Auth (Gateway → MCP Target)

| Type | When to use |
|---|---|
| `NONE` | MCP server needs no credential |
| `APIKEY` | Inject API key header. 2LO = shared key for all calls. 3LO = per-user key resolved from Identity. |
| `OAUTH` | 2LO = client credentials (M2M). 3LO = authorization code (user consent). |

Secrets are stored in Identity service by `providerName`. Gateway references by name only — never embeds raw secrets.

### 4.5 Policy Group

Policies use ALLOW/DENY rules on `tools/call` actions. Without a policy group attached, all `tools/call` → 403. `tools/list` is always allowed.

Policy action format: `target__method` (e.g., `kms__kms_search`). Use `"*"` for all tools.

**For MVP with no access control:** attach a policy group with a single ALLOW rule for `*` actions. Do NOT leave gateway with no policy group.

---

## 5. Deployment Pipeline

### 5.1 Container Registry

One pre-provisioned registry per user/org. URL format: `vcr.vngcloud.vn/{repoName}/{imageName}:{tag}`

Docker login:
```bash
bash .claude/skills/agentbase/scripts/cr.sh credentials docker-login
```
Secret fetched in-memory. No file written.

Rotate credentials:
```bash
bash .claude/skills/agentbase/scripts/cr.sh credentials docker-login --reset
```

Verify login: `docker pull vcr.vngcloud.vn/{repoName}/nonexistent:test 2>&1`
- "not found" = authenticated
- "unauthorized" = not authenticated

### 5.2 Docker Build Requirements

- Platform: `linux/amd64` (required for AgentBase compatibility)
- Port 8080 must be exposed
- `GET /health` must return 200

Minimal Dockerfile:
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8080
CMD ["python", "main.py"]
```

### 5.3 Runtime Create / Update

**Create:**
```bash
bash .claude/skills/agentbase/scripts/runtime.sh create \
  --name "<runtime-name>" \
  --image "vcr.vngcloud.vn/{repo}/{name}:{tag}" \
  --flavor "<flavor>" \
  --env-file <path-to-env-file> \
  [--min-replicas 1] \
  [--max-replicas 3] \
  [--cpu-scale 50] \
  [--mem-scale 50] \
  --from-cr
```

**Update (new image version):**
```bash
bash .claude/skills/agentbase/scripts/runtime.sh update $RUNTIME_ID \
  --image "vcr.vngcloud.vn/{repo}/{name}:{new-tag}" \
  --flavor "<flavor>"
```

**CRITICAL VPC update warning:** If runtime is VPC mode, MUST re-pass `--network-mode VPC --vpc-id ... --subnet-id ...` on every update. Omitting reverts runtime to PUBLIC mode.

### 5.4 Runtime Lifecycle

States: `CREATING → ACTIVE`, or `UPDATING`, `STOPPING`, `STOPPED`, `ERROR`, `DELETING`.

- `STOPPED` state costs no compute. Config and endpoints are preserved.
- Rollback: update runtime with previous image tag.
- Each image update creates an immutable **Version**. DEFAULT endpoint auto-routes to latest version. Additional endpoints can be pinned to specific versions (canary/rollback).

### 5.5 Runtime Operations

Base URL: `https://agentbase.api.vngcloud.vn/runtime`

| Operation | Method | Endpoint |
|---|---|---|
| List runtimes | GET | `/agent-runtimes?page=1&size=10` |
| Get runtime | GET | `/agent-runtimes/{id}` |
| Delete runtime | DELETE | `/agent-runtimes/{id}` |
| List endpoints | GET | `/agent-runtimes/{id}/endpoints` |
| Create endpoint | POST | `/agent-runtimes/{id}/endpoints` |
| List versions | GET | `/agent-runtimes/{id}/versions` |
| Reset service account | PATCH | `/agent-runtimes/{id}/reset-service-account` |
| List flavors | GET | `/flavors` |

### 5.6 Environment File for Runtime

The `--env-file` is a plain key=value file. Do NOT include the auto-injected vars (`GREENNODE_CLIENT_ID`, etc.) — platform injects those. Include only application-specific config:

```env
ANTHROPIC_API_KEY=sk-ant-...
SQLITE_PATH=/tmp/kb.db
LOG_LEVEL=INFO
```

---

## 6. Identity Service and Outbound Auth

When the agent needs to call external services (Anthropic API, S3, etc.), credentials are stored in the Identity service and injected at call time. The agent never hardcodes secrets.

Base URL: `https://agentbase.api.vngcloud.vn/identity/api/v1`
Console: `https://aiplatform.console.vngcloud.vn/access-control`

### 6.1 Auth Provider Types

| Type | Use case |
|---|---|
| API Key Provider (`/outbound-auth/api-key-providers`) | Static API keys (Anthropic API, S3 keys, etc.) |
| Delegated API Key Provider (`/outbound-auth/delegated-api-key-providers`) | Per-user API keys |
| OAuth2 Provider (`/outbound-auth/oauth2-providers`) | OAuth2 flows |

### 6.2 Identity Operations

```
POST /agent-identities              # create
GET  /agent-identities              # list (0-indexed pages)
GET  /agent-identities/{id}         # get
PATCH /agent-identities/{id}        # update
DELETE /agent-identities/{id}       # delete
```

The `GREENNODE_AGENT_IDENTITY` env var (auto-injected) is the identity ID. The SDK uses it to retrieve secrets at runtime.

---

## 7. Memory Service (Optional for MVP)

Enables conversation history and long-term semantic facts across sessions.

Base URL: `https://agentbase.api.vngcloud.vn/memory`
Console: `https://aiplatform.console.vngcloud.vn/memory`

### 7.1 Key Concepts

| Component | Lifetime | Description |
|---|---|---|
| Memory | Permanent | Container for events and records |
| Event | Expires (configurable) | Single conversation turn |
| Actor | Created on first event | Participant (user or agent) |
| Session | Created on first event | Conversation thread |
| Memory Record | Permanent | Extracted long-term fact |

### 7.2 Long-Term Memory Strategies (LTMS)

Three strategies for fact extraction:
- `SEMANTIC` — general fact extraction
- `USER_PREFERENCE` — user habits/preferences
- `CUSTOM` — user-defined prompt. **Critical: using `SEMANTIC` or `USER_PREFERENCE` with a custom prompt silently ignores the prompt.**

### 7.3 Key Operations

```
POST /memories                                                              # create memory
GET  /memories?page=1&size=10                                               # list
POST /memories/{id}/actors/{actorId}/sessions/{sessionId}/events            # add event
POST /memories/{id}/memory-records:search                                   # semantic search
POST /memories/{id}/memory-records:generate-from-session                    # extract facts
```

**For the KMS project:** Memory service is NOT needed for MVP. The knowledge base DB is the memory. Consider for future: conversation context across Claude Desktop sessions.

---

## 8. LLM / MaaS (Model-as-a-Service)

AgentBase provides a managed LLM endpoint with OpenAI-compatible API. Recommended over direct Anthropic API for cost control on AgentBase.

**Endpoint:** `https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1`

**Python usage:**
```python
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_GREENNODE_API_KEY",         # from AgentBase AI Platform key management
    base_url="https://maas-llm-aiplatform-hcm.api.vngcloud.vn/v1",
)

response = client.chat.completions.create(
    model="<model-path>",                     # use `path` field from model detail, not `code`
    messages=[{"role": "user", "content": "..."}],
)
```

**Model selection:** Use the `path` field from model detail response, not `code`. If `path` is missing, fall back to `code`.

**API key management:**
```bash
aip.sh api-keys create --name <key-name>    # async — poll until ACTIVE
aip.sh api-keys list
aip.sh api-keys delete <name>              # async
```

Key names: 5–50 chars, lowercase letters/digits/hyphens only.

**For the KMS project:** The existing `llm/provider.py` config abstraction can point to this endpoint by changing `base_url` and `api_key` in `config.yaml`. No code change needed — only config.

---

## 9. Monitoring

Base URL: `https://agentbase.api.vngcloud.vn/runtime` (same service)

**Runtime logs:**
```bash
bash .claude/skills/agentbase/scripts/runtime.sh logs $RUNTIME_ID \
  [--limit 500] \
  [--query "keyword"] \
  [--from <offset>]
```

**Endpoint logs:** same pattern with endpoint ID.

**Metrics:** CPU and RAM per runtime, with time-range filters.

**Events:** Infrastructure-level events (image pull failures, OOM, scheduling issues). Check when endpoint is not ACTIVE but logs are empty.

**Debugging decision tree:**
1. Non-responsive agent → check runtime status → check logs for Python tracebacks or health-check failures
2. Error responses (4xx/5xx) → correlate HTTP status with log patterns
3. Performance issues → CPU/RAM metrics vs external bottlenecks

---

## 10. Python SDK

**Package:** `greennode-agentbase`

**Install:** `pip install greennode-agentbase`

**Key classes:**
- `GreenNodeAgentBaseApp` — main app wrapper
- `IdentityClient` — credential retrieval
- `MemoryClient` — memory store operations
- `IAMCredentials` — auth decorators

**Framework bridge (LangGraph):** `greennode-agent-bridge[langgraph]` — provides `AgentBaseMemoryEvents` for checkpoint saving.

**SDK handles automatically:** reading auto-injected env vars (`GREENNODE_CLIENT_ID`, etc.), bearer token refresh, session/user header extraction.

For the KMS project: SDK is needed if using AgentBase Memory Service or Identity credential retrieval. For basic Custom Agent deployment (MCP server + health endpoint), plain FastMCP + uvicorn is sufficient without the SDK.

---

## 11. GAPS AND DECISIONS

These are things the `cloud_native_rearchitecture.md` decision doc assumed were open, now resolved.

### 11.1 Daemon Command Push — DECIDED: REST Polling

**Background:** AgentBase does NOT natively support WebSocket or SSE push from the platform. The MCP Gateway docs explicitly state no WebSocket/SSE protocol support. The container (user Docker image) could expose these, but that introduces statefulness that conflicts with stateless container design and breaks with multiple replicas.

**Decision: Option A — REST Polling.**

How it works:
- Daemon runs a loop every ~10–30 seconds: `GET <agent-endpoint>/pending-commands`
- AgentBase container exposes that endpoint, reads pending commands from DB, returns them
- Daemon executes the command (e.g., move file), confirms back via `POST <agent-endpoint>/command-ack`
- Commands stored in DB until daemon picks them up and acknowledges

Why polling wins over SSE:
- Works with any number of container replicas (any replica can answer a poll from DB)
- No persistent connection to manage
- NAT-friendly (daemon initiates all outbound connections)
- Trivial to debug
- Acceptable latency for file move operations (10–30s delay is fine)

**Implication for design:** The container needs two new REST endpoints beyond the MCP tools: `GET /pending-commands` and `POST /command-ack`. These are internal daemon-facing APIs, not MCP tools.

### 11.2 SQLite Persistence — DECIDED: Litestream + VNG Object Storage

**Background:** AgentBase containers have no persistent disk. Container restart = all local data lost. The SQLite DB must live outside the container.

**What VNG Object Storage is and is NOT:** It is an S3-compatible file store (like a USB drive in the cloud). Good for storing backup files. NOT a database — cannot run SQL queries against it directly. It is used only to hold SQLite backup files.

**Decision: Litestream + VNG Object Storage.**

How it works:
- Litestream runs as a background process inside the container alongside the main app
- Every SQLite write is automatically streamed to VNG Object Storage in near-realtime (seconds delay)
- On container restart, Litestream downloads the latest backup and restores the DB before the app starts
- App code sees zero difference — reads/writes SQLite as normal

Why not PostgreSQL: would require rewriting all storage code. High effort, no benefit for a personal vault DB (<100 MB).

**VNG Object Storage:** S3-compatible. Needs separate setup (bucket creation, access keys). Investigate at `https://vngcloud.vn/storage/object-storage`. Not covered in depth in this research.

**Implication for design:** Dockerfile needs Litestream binary + startup script that (1) restores DB from object storage, (2) starts Litestream replication, (3) starts the main app. Shutdown should flush Litestream before exit.

### 11.3 Daemon Authentication to AgentBase — OPEN

**Problem:** Daemon calls AgentBase APIs (upload content, poll commands). Needs IAM credentials.

**What was found:** IAM service account (`GREENNODE_CLIENT_ID` + `GREENNODE_CLIENT_SECRET`) is how external code authenticates. Daemon needs these credentials on the user's machine.

**Not yet decided:** Whether daemon uses same IAM service account as the container (simpler, less secure) or its own dedicated account. Design phase must decide.

### 11.4 Multi-Replica State — DECIDED: max-replicas 1 for MVP

**Background:** Multiple container replicas share the DB (via Litestream) but not in-memory state. REST polling eliminates the in-memory coordination problem — any replica answers a poll from DB. Still, running multiple replicas adds operational complexity for zero benefit at current scale.

**Decision: `--max-replicas 1` for MVP.** One container, no coordination issues. Revisit only when query load requires horizontal scaling.

---

## 12. Deployment Checklist for This Project

Translating arch doc §14 to AgentBase-specific steps:

1. Create IAM service account (`agentbase-kms` or similar). Attach `AgentBaseFullAccess`, `vcrFullAccess`, `AiPlatformFullAccess`.
2. Update `CONSTRAINTS.md` C-01 before writing any code (arch doc §10).
3. Write `Dockerfile` for the KMS container: Python 3.12, install `src/`, expose port 8080, health endpoint at `/health`.
4. Add container entry point: start FastMCP on port 8080, WAL checkpoint, health endpoint.
5. Build image: `docker build --platform linux/amd64 -t vcr.vngcloud.vn/{repo}/ai-kms:{tag} .`
6. Push to AgentBase Container Registry.
7. Create AgentBase Custom Agent runtime with `runtime.sh create`.
8. Create Resource Gateway with target pointing to runtime endpoint.
9. Attach policy group to gateway (even a permissive ALLOW-all for MVP).
10. **Daemon command push: REST polling** (decided §11.1). Container needs `GET /pending-commands` + `POST /command-ack` endpoints.
11. **DB persistence: Litestream + VNG Object Storage** (decided §11.2). Dockerfile startup = restore DB → start Litestream → start app.
12. **Replicas: `--max-replicas 1`** for MVP (decided §11.4). No coordination complexity.
13. Decide §11.3 (daemon IAM account — shared vs dedicated) before packaging daemon.

---

## 13. Console URLs Reference

| Console | URL |
|---|---|
| AgentBase main | `https://aiplatform.console.vngcloud.vn` |
| Agent Runtime | `https://aiplatform.console.vngcloud.vn/agent-runtime?tab=runtime` |
| Access Control (Identity) | `https://aiplatform.console.vngcloud.vn/access-control` |
| Memory | `https://aiplatform.console.vngcloud.vn/memory` |
| MCP Gateway | `https://aiplatform.console.vngcloud.vn/mcp-gateway` |
| AI Platform LLM models | `https://aiplatform.console.vngcloud.vn/models` |
| IAM service accounts | `https://iam.console.vngcloud.vn/service-accounts` |

---

## 14. Source Links (Read Further)

**VNG Cloud docs:**
- AgentBase overview: `https://docs.vngcloud.vn/vng-cloud-document/vn/ai-stack/agent-base`
- Agent Runtime: `https://docs.vngcloud.vn/vng-cloud-document/vn/ai-stack/agent-base/agent-runtime.md`
- MCP Gateway: `https://docs.vngcloud.vn/vng-cloud-document/vn/ai-stack/agent-base/mcp-governance/mcp-gateway.md`
- Query pattern: `GET https://docs.vngcloud.vn/vng-cloud-document/vn/ai-stack/agent-base/{page}.md?ask=<question>` (GitBook query interface)

**GitHub skill files (primary technical reference):**
- Skills repo root: `https://github.com/vngcloud/greennode-agentbase-skills`
- Platform reference: `.claude/skills/agentbase/SKILL.md`
- Deploy reference: `.claude/skills/agentbase-deploy/SKILL.md`
- Gateway reference: `.claude/skills/agentbase-gateway/SKILL.md`
- Identity reference: `.claude/skills/agentbase-identity/SKILL.md`
- Memory reference: `.claude/skills/agentbase-memory/SKILL.md`
- LLM reference: `.claude/skills/agentbase-llm/SKILL.md`
- Monitor reference: `.claude/skills/agentbase-monitor/SKILL.md`
- Wizard (full lifecycle): `.claude/skills/agentbase-wizard/SKILL.md`
- Policy reference: `.claude/skills/agentbase-policy/SKILL.md`

**Sub-reference files (inside skills repo):**
- Runtime contract: `.claude/skills/agentbase/references/runtime-contract.md`
- Auth setup: `.claude/skills/agentbase/references/auth-setup.md`
- API endpoints: `.claude/skills/agentbase/references/endpoints.md`
- Gateway inbound auth: `.claude/skills/agentbase-gateway/references/inbound-auth.md`
- Gateway outbound auth: `.claude/skills/agentbase-gateway/references/outbound-auth.md`
- Gateway operations: `.claude/skills/agentbase-gateway/references/gateway-ops.md`
- Deploy runtime ops: `.claude/skills/agentbase-deploy/references/runtime-ops.md`
- CR ops: `.claude/skills/agentbase-deploy/references/cr-ops.md`

**Install skills locally (enables `/agentbase`, `/agentbase-deploy`, etc. as slash commands):**
```bash
git clone https://github.com/vngcloud/greennode-agentbase-skills.git
mkdir -p ~/.claude/skills
cp -r greennode-agentbase-skills/.claude/skills/* ~/.claude/skills/
```
Then use `/agentbase-wizard` to get step-by-step guided deployment.
