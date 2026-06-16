# AgentBase Deployment Guide

How to stand up a single-tenant KMS deployment and connect a tester's
Claude Desktop to it.

---

## Status (2026-06-16, updated — new machine) — read this first if picking up

| Step | Status |
|---|---|
| Local Docker build + `/health` verify | ✅ DONE (rebuilt on new machine, `--platform linux/amd64`) |
| IAM service account | ✅ Re-saved on new machine (`.greennode.json`) |
| Push image to GreenNode Container Registry | ✅ DONE — `vcr.vngcloud.vn/111480-abp111749/kms-image:latest` |
| Create/Update AgentBase Runtime | ✅ RESOLVED — see below |
| Verify `/health` on real gateway | ✅ DONE — `{"status":"ok"}`, HTTP 200 |
| Hand off to tester | ⚠️ BLOCKED on secret rotation — see Security note below |

**Blocker resolution:** original `runtime-c6507401-2dff-42a3-b295-965940cbf195`
(`kms-runtime`) and the two unrelated runtimes
(`Attention-Leak-Hunter-V1.2`, `leader-attention-leak-hunter-v1-1-1`)
are **still stuck in `status: ERROR`** as of this session — the
underlying platform issue on `create` was never confirmed fixed.
What worked instead: **`runtime.sh update <existing-id>`** on the
already-existing (broken) `kms-runtime` runtime, pushing a fresh image
+ env as version 2. That flipped runtime status to `ACTIVE` and the
`DEFAULT` endpoint rolled to version 2, `status: ACTIVE`, `/health`
returns `{"status":"ok"}` — despite `currentReplicaCount: 0` reported
by the API (appears to be a lagging/cosmetic metric, not literal —
health checks pass). **`runtime.sh create` for a brand-new name was
never retried** — unknown if that path is still broken. If starting a
genuinely new runtime, try `update` on an existing dead one first if
one is available, before trusting `create`.

```
runtime-c6507401-2dff-42a3-b295-965940cbf195   (kms-runtime — fixed via `update`, now ACTIVE)
runtime-9ddfa2b7-627c-4b11-a0a9-77b4a1a4f4dd   (unrelated, still ERROR, not touched)
runtime-0e224f8c-6575-412e-b278-e4e953a38856   (unrelated, still ERROR, not touched)
```

**⚠️ Security note (2026-06-16):** `runtime.sh versions <id>` was piped
through `redact_response.sh` per this guide's own caution below, but
the redaction script only matches generic field names
(`secretKey`/`key`/`password`) — it did **not** catch custom env var
names (`LLM_API_KEY`, `KMS_DAEMON_API_KEY`), so both leaked in
plaintext into an AI session transcript. **Action required before
handoff:** rotate `KMS_DAEMON_API_KEY` (generate fresh, push as a new
runtime version) and consider rotating `LLM_API_KEY` (GreenNode AIP
key) since it was also exposed. Consider fixing `redact_response.sh`
to also match known env var name patterns, not just generic key names.

---

## Part 1 — Builder: stand up the deployment

You are provisioning one KMS runtime for one tester. Each deployment is
fully isolated (own database, own vault, own API key).

### 1.1 Before you build — sync config

The Dockerfile (repo root) copies the **root-level `config/` directory**,
not `src/config/`. These two can drift out of sync (they did, once).
Sync before every build:

```bash
cp src/config/config.yaml config/config.yaml
```

### 1.2 What you need before you start

* Docker installed locally (image built and tested on the dev machine
  before pushing).
* An **IAM service account** for the tester — gates inbound MCP
  traffic at the platform gateway (not in KMS code). Check with:
  ```bash
  bash .claude/skills/agentbase/scripts/check_credentials.sh iam
  ```
  If missing, see `.claude/skills/agentbase/references/auth-setup.md`
  for the 3-option setup flow.
* A **daemon API key** — generate fresh per deployment, ≥32 chars
  (`openssl rand -hex 32`). This is `KMS_DAEMON_API_KEY`.
* `LLM_API_KEY` already present in `.env` for the GreenNode AIP
  endpoint (provider config lives in `src/config/openai_compat`).
  Note: `ANTHROPIC_API_KEY` is **not needed** for this deployment —
  `providers:` in `config.yaml` routes every task through `openai`
  (GreenNode/DeepSeek), so `ClaudeProvider` is never instantiated.
  Only add it back if you revert a task's provider to `claude`.
* *(Optional)* S3-compatible blob storage credentials for binary
  attachment support.

### 1.3 Build and verify locally

```bash
docker build -t kms-image:latest .
docker rm -f kms-test 2>/dev/null  # if a prior test container exists
docker run -d -p 8080:8080 --name kms-test \
  -e LLM_API_KEY="$(grep ^LLM_API_KEY .env | cut -d= -f2-)" \
  -e KMS_DAEMON_API_KEY="$(openssl rand -hex 32)" \
  -e VAULT_ROOT=/data/vault \
  -e KMS_DB_PATH=/data/kb.db \
  -e env=prod \
  kms-image:latest
curl -s http://localhost:8080/health   # expect {"status":"ok"}
```

### 1.4 Push to the GreenNode Container Registry (CR)

Each account has one pre-provisioned repo + one credential pair.

```bash
bash .claude/skills/agentbase/scripts/cr.sh repo get          # confirm repo name + registryUrl
bash .claude/skills/agentbase/scripts/cr.sh credentials docker-login
docker tag kms-image:latest <registryUrl>/<repoName>/kms-image:latest
docker push <registryUrl>/<repoName>/kms-image:latest
```

(This deployment: `vcr.vngcloud.vn/111480-abp111749/kms-image:latest`.)

### 1.5 Create the AgentBase Runtime

```bash
bash .claude/skills/agentbase/scripts/runtime.sh flavors        # list available flavors
```

Build an env file (never put secrets on the command line):

```bash
mkdir -p .agentbase
cat > .agentbase/runtime.env <<EOF
LLM_API_KEY=$(grep ^LLM_API_KEY .env | cut -d= -f2-)
KMS_DAEMON_API_KEY=$(openssl rand -hex 32)
VAULT_ROOT=/data/vault
KMS_DB_PATH=/data/kb.db
env=prod
EOF
```

Create the runtime (single tester → `runtime-s2-general-2x4` flavor is
enough):

```bash
bash .claude/skills/agentbase/scripts/runtime.sh create \
  --name kms-runtime \
  --image <registryUrl>/<repoName>/kms-image:latest \
  --flavor runtime-s2-general-2x4 \
  --env-file .agentbase/runtime.env \
  --from-cr \
  --min-replicas 1 --max-replicas 1
```

**Caution:** `runtime.sh versions <id>` echoes back the env vars you
set, **including secret values, in plaintext**. Pipe through
`redact_response.sh` if you need to inspect a runtime's config, or
avoid calling `versions` unless necessary.

### 1.6 Verify deployment

```bash
bash .claude/skills/agentbase/scripts/runtime.sh get <runtime-id>
bash .claude/skills/agentbase/scripts/runtime.sh endpoints list <runtime-id>
```

Wait for `status: ACTIVE` and `currentReplicaCount >= 1` on the
`DEFAULT` endpoint, then:

```bash
curl https://<endpoint-url>/health
```

Expected response: `{"status":"ok"}` (HTTP 200).

If stuck in `CREATING` or flips to `ERROR`:
- `runtime.sh logs <runtime-id> --limit 100`
- `runtime.sh endpoints events <runtime-id> <endpoint-id>`
- Console: `https://aiplatform.console.vngcloud.vn/agent-runtime?tab=runtime` → runtime detail → **Monitor** tab (Metrics / Log / Events)
- If all of the above are empty/no-data and this happens across
  *different* runtimes/images on the account, it's a platform-side
  issue — escalate to GreenNode support rather than retrying blind.

### 1.7 Hand off to the tester

Give the tester three things:

1. **The gateway endpoint URL** — the runtime's `DEFAULT` endpoint URL.
2. **Their IAM credential** — whatever the platform gateway requires
   (API key, token, client cert). This is how Claude Desktop
   authenticates to the gateway.
3. **The daemon API key** — the value you set as `KMS_DAEMON_API_KEY`
   in `.agentbase/runtime.env`.

---

## Part 2 — Tester: connect Claude Desktop

You are the tester. You have received three items from your builder:
a gateway URL, an IAM credential, and a daemon API key.

### 2.1 What you need

* **Claude Desktop** installed on your laptop.
* The **KMS daemon** installed on your laptop (see the separate daemon
  install guide).
* The three items from your builder.

### 2.2 Configure the daemon

Run the daemon setup wizard once:

```
kms-daemon setup
```

It will prompt you for:
* **Vault path** — the folder on your laptop where your notes live.
* **AgentBase endpoint URL** — the gateway URL from your builder.
* **API key** — the `KMS_DAEMON_API_KEY` from your builder.

The wizard stores the API key in your operating system's secure
credential store (macOS Keychain or Windows Credential Manager).
It never writes secrets to a plaintext file.

### 2.3 Start the daemon

```
kms-daemon start
```

The daemon watches your vault folder and syncs notes to the cloud.
Keep it running in the background while you use Claude Desktop.

### 2.4 Connect Claude Desktop to the gateway

In Claude Desktop, add a new MCP server:

1. Open **Settings → MCP Servers**.
2. Click **Add Server**.
3. Enter the gateway URL from your builder as the **Streamable HTTP
   endpoint**.
4. Enter your IAM credential (the platform gateway authenticates you —
   KMS does not see this credential).
5. Save.

Claude Desktop will connect to the gateway and discover the five KMS
tools automatically.

### 2.5 Verify the five tools work

Start a new conversation in Claude Desktop and try each tool:

| Tool | Quick test |
|---|---|
| `kms_vault_info` | Ask: "What's in my vault?" — should return projects, domains, inbox count. |
| `kms_search` | Ask: "Search for anything in my vault." — should return result cards. |
| `kms_inspect` | After a search result, ask: "Inspect that document." — should return the summary. |
| `kms_write` | Say: "Save this insight: the Q3 roadmap targets October." — should return a document id. |
| `kms_correct` | After inspecting a fact, ask: "Fix that fact's tag to 'confirmed'." — should confirm the correction. |

If all five tools work, your deployment is healthy.

### 2.6 Troubleshooting

| Symptom | Check |
|---|---|
| Claude Desktop cannot connect | Gateway URL is correct. IAM credential is valid. Network allows outbound HTTPS. |
| `kms_vault_info` returns empty | Your vault may be empty — this is normal for a fresh deployment. Try `kms_write` to add something. |
| Daemon sync fails | Daemon is running (`kms-daemon status`). API key matches the builder's `KMS_DAEMON_API_KEY`. Endpoint URL in daemon config is correct. |
| `/health` returns an error | Ask your builder to check the container logs. |

---

## Important notes

* **Single-tenant:** each deployment serves exactly one tester. Do not
  share a deployment across multiple people — vaults, databases, and
  API keys are not isolated per-user within one container.
* **No secrets in this document:** all keys/IDs shown are either
  placeholders or non-secret identifiers (runtime IDs, registry path).
  Real secrets are never embedded in guides or committed to version
  control.
* **IAM vs daemon key:** IAM authenticates Claude Desktop to the
  platform gateway (inbound MCP). The daemon API key authenticates the
  laptop daemon to KMS's `/api/*` routes (upload, event, state). They
  are separate credentials with different scopes.
