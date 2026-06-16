# AI Instructions — KMS MCP Server

How to use the KMS vault tools correctly when assisting the user.

## Tool inventory

Five tools are available:

| Tool | Purpose |
|---|---|
| `kms_vault_info` | Discover the knowledge base: entity map grouped by dimension, orientation facts, inbox stats. |
| `kms_search` | Search facts and documents. Returns orientation context + fact results + document cards. |
| `kms_inspect` | Drill into documents by integer id. Three modes: summary, text, file. |
| `kms_write` | Save a new insight from the conversation into the knowledge system. |
| `kms_correct` | Fix an existing knowledge entry by id. Confirm-first workflow. |

## Discovery workflow

Always follow this sequence — never skip ahead:

1. **`kms_vault_info`** — Call FIRST. Tells you what projects and domains exist, how many items are in the inbox, and current global context. Never guess the vault structure.
2. **`kms_search`** — Search for relevant documents. Returns **cards** (title, id, snippet, score) plus any context blocks. Cards give you enough to decide what is relevant. Use filters (`project`, `since`, `until`, `location`, `max_results`) to narrow. Iterative refinement is expected: broad first search, then narrow.
3. **`kms_inspect`** — Drill into documents selected by integer id. Defaults to **summary** mode (always available). Use **text** mode for the full body (opt-in; may degrade for large or binary documents). Use **file** mode to get the vault path (laptop-dependent; only when the user needs it for local access).

The old `kms_read` / `kms_move` workflow no longer exists. Documents are referenced by integer id, not vault paths.

## Facts vs summaries

The knowledge system stores two kinds of content:

- **Facts** — Targeted, extracted insights with entity, dimension, and tag. Stored as structured knowledge entries. You view and correct these via `kms_inspect` (they appear in summary mode) and `kms_correct`.
- **Summaries** — General 5-section digests of ingested documents (Overview, Key Points, Entities, Timeline, Gaps). These are the default `kms_inspect summary` output for documents.

## Correct vs write routing

Choosing the right tool for adding or fixing knowledge is critical:

- **`kms_correct`** — Fix an **existing** fact by its integer entry id. Operations: `edit_fact`, `change_tag`, `change_entity`, `promote`, `un_retire`, `retire`. Always confirm with the user before applying a correction. `retire` requires a `reason`.
- **`kms_write`** — Add a **new** insight from the conversation. Use this when the user shares a novel observation, decision, or piece of knowledge that should be persisted. Be **proactive** (suggest saving when you spot an insight) and **transparent** (tell the user you are saving it and share the returned document id).

## Inspect modes

`kms_inspect` supports three modes, selected by the `mode` parameter:

| Mode | Behaviour | When to use |
|---|---|---|
| `summary` | Returns the AI-generated 5-section digest. Always available. This is the **default**. | After search, to understand what a document contains. |
| `text` | Returns the full extracted body text. May degrade for binaries (see caveat below). | When the summary isn't enough detail. Opt-in. |
| `file` | Returns the vault filesystem path. Laptop-dependent — only works on the machine running the daemon. | When the user needs to open or edit the file locally. |

### Binary note caveat

When you call `kms_inspect` with `mode="text"` on a binary document (PDF, DOCX, image, etc.), the server returns the AI-generated **vision description** of that binary, not the raw bytes. There is no way to retrieve the original binary bytes through the MCP tools. Summaries (`mode="summary"`) are always a safe default for binaries.

## Reference model

- Documents and knowledge entries are referenced by **integer ids**, not vault paths.
- `kms_search` result cards include `id` fields — pass these to `kms_inspect`.
- `kms_correct` requires the entry's integer `entry_id`.
- `kms_write` returns a `document_id` on success.

## Identity dedup

The server tracks which fact entry IDs and document IDs you have already seen this conversation. You will not receive the same fact or document twice. You do not need to track this yourself.

## Refinement is expected

Your first search may be broad to discover what exists. Narrow with filters on the second call. This is the intended usage pattern — the server is designed for iterative refinement.
