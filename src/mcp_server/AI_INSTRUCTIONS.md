# AI Instructions — KMS MCP Server

How to use the KMS vault tools correctly when assisting the user.

## Core workflow

### 1. Discover first — never assume
Always call `kms_vault_info` before searching. It tells you what projects and domains exist, how many items are in the inbox, and the global context. Never guess the vault structure.

### 2. Two-step retrieval: search then read
- `kms_search` returns **cards** (title, path, snippet, score). Cards give you enough to decide what is relevant.
- `kms_read` returns **full note bodies**. Call it after search to get the complete content of the notes you want.

Both tools accept multiple paths — read everything you need in one call.

### 3. Context-before-content
When `kms_search` or `kms_read` returns context blocks, present those **first** in your response. Context blocks describe the vault landscape (projects, domains, recent activity). Content (result cards, note bodies) comes after.

### 4. Hash-dedup is automatic
The server tracks which context hashes you have already seen this conversation. You will not receive the same context block twice. You do not need to track this yourself.

### 5. Structured filters on search
Use the filter params on `kms_search` to narrow results:
- `project` — limit to one project folder
- `since` / `until` — date range (any format the vault understands)
- `location` — free-text location filter

### 6. Refinement is expected
Your first search may be broad to discover what exists. Narrow with filters on the second call. This is the intended usage pattern — the server is designed for iterative refinement.

### 7. Broad queries skip context
Queries that match many documents (low frequency signal) automatically skip context injection. Use `include_context=true` on `kms_search` or `kms_read` to force context re-injection when you need it.

### 8. Binary source text via kms_inspect
For PDFs, DOCX, images, and other binary files:
- `kms_read` returns the AI-generated **summary** (the `.md` sibling)
- `kms_inspect` returns the **original extracted text** (no AI call)

Pass either the binary path or its sibling `.md` summary path — both work.

### 9. Filing notes with kms_move
Use `kms_move` to file a note into a project or domain:
- `src` — current vault path of the note
- `dest_name` — name of the project or domain
- `dest_kind` — `"project"` or `"domain"`

The tool updates frontmatter labels, reindexes, and coordinates with the vault watcher to prevent the move from being undone.

## Tool summary

| Tool | When to call |
|---|---|
| `kms_vault_info` | First — before any search |
| `kms_search` | Find relevant notes by query |
| `kms_read` | Get full note bodies after search |
| `kms_inspect` | Get raw text from a binary source |
| `kms_move` | File a note into a project or domain |
