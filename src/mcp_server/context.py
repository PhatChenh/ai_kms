"""ContextInjectionEngine — the per-conversation decision engine.

Phase 3 fills in the methods (build response blocks, dedup, count, gate).
Phase 2 only needs the constructor so the server lifespan can instantiate it.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from core.result import Failure, Result, Success

if TYPE_CHECKING:
    from retrieval.reranker import SearchResult
    from vault.registry import ProjectRegistry

_log = logging.getLogger(__name__)


class ContextInjectionEngine:
    """Context injection decision engine.  Built in Phase 3."""

    def __init__(self) -> None:
        self._dedup_memory: dict[str, str] = {}  # content_hash -> filename

    # ======================================================================
    # Dedup memory (RED 1)
    # ======================================================================

    def is_already_provided(self, content_hash: str) -> tuple[bool, str]:
        """Check whether *content_hash* was already sent this conversation.

        Returns:
            ``(True, source_label)`` if the hash is in dedup memory,
            ``(False, "")`` otherwise.
        """
        label = self._dedup_memory.get(content_hash, "")
        return (label != "", label)

    def record_sent(self, content_hash: str, label: str) -> None:
        """Record that *content_hash* was sent, keyed by *label*.

        Args:
            content_hash: The SHA-256 hash of the file content sent.
            label: A human-readable identifier (e.g. ``"project:Alpha"``).
        """
        self._dedup_memory[content_hash] = label

    # ======================================================================
    # Public API — three build_*_response methods (RED 2, 5, 6)
    # ======================================================================

    def build_search_response(
        self,
        cards: list[SearchResult],
        registry: ProjectRegistry,
        query: str | None = None,
        include_context: bool = False,
    ) -> Result[list[dict]]:
        """Build a response for a search: context blocks first, result cards second.

        Concentration-gated: if the top domain's share of results is below
        the configured threshold, no context is attached.  At or above the
        threshold, the top few domain/project context files are injected.

        Args:
            cards:           Search result cards from the Search Coordinator.
            registry:        Live project→domain registry.
            query:           The original search query (for logging only).
            include_context: If ``True``, bypass dedup and force full injection.

        Returns:
            ``Success(list[dict])`` — each dict is a response block.
        """
        try:
            if not cards:
                return Success([])

            # ---- Step 1: build project→domain reverse map ---------------
            project_domain = self._build_project_domain_map(registry)

            # ---- Step 2: count concentration ----------------------------
            threshold = self._get_frequency_threshold()
            domain_share, top_domains = self._count_concentration(cards, project_domain)

            # ---- Step 3: gate -------------------------------------------
            if domain_share < threshold and not include_context:
                # Below threshold — return cards only
                return Success([{"type": "result_card", "data": c} for c in cards])

            # ---- Step 4: collect context for top domains ----------------
            cap = self._get_max_context_files()
            context_blocks = self._collect_context_for_domains(
                domain_names=set(top_domains),
                registry=registry,
                include_context=include_context,
                max_files=cap,
            )

            # ---- Step 5: assemble — context first, then cards -----------
            blocks: list[dict] = []
            blocks.extend(context_blocks)
            for card in cards:
                blocks.append({"type": "result_card", "data": card})

            return Success(blocks)

        except Exception as exc:
            _log.warning("build_search_response failed: %s", exc)
            return Failure(
                str(exc),
                recoverable=True,
                context={"query": query or ""},
            )

    def build_vault_info_response(
        self,
        registry: ProjectRegistry,
        db_path: Path | None = None,
    ) -> Result[list[dict]]:
        """Build a vault-overview response: domain/project listing, inbox
        stats, and vault-root CLAUDE.md.

        Args:
            registry: Live project→domain registry.
            db_path:  Optional DB path override (for tests).

        Returns:
            ``Success(list[dict])`` — context blocks describing the vault.
        """
        try:
            blocks: list[dict] = []

            # ---- Vault-overview text ------------------------------------
            domain_names = sorted(
                name for name in registry.groups if name != "Uncategorized"
            )
            all_projects = sorted(registry.all_project_names)

            overview_lines = [
                "# Vault Overview",
                "",
                "## Domains",
            ]
            for dn in domain_names:
                group = registry.groups[dn]
                pnames = [e.name for e in group.projects]
                if pnames:
                    overview_lines.append(f"- **{dn}**: {', '.join(pnames)}")
                else:
                    overview_lines.append(f"- **{dn}**: (no active projects)")

            overview_lines.append("")
            overview_lines.append("## All Projects")
            for pn in all_projects:
                overview_lines.append(f"- {pn}")

            overview_text = "\n".join(overview_lines)
            overview_hash = hashlib.sha256(overview_text.encode("utf-8")).hexdigest()

            blocks.append(
                {
                    "type": "context",
                    "source": "vault_overview",
                    "path": "registry://overview",
                    "content": overview_text,
                }
            )

            # Record the overview hash so it can be deduped later
            self.record_sent(overview_hash, "vault_overview")

            # ---- Inbox stats --------------------------------------------
            inbox_count, last_capture = self._get_inbox_stats(db_path)
            inbox_text = (
                f"# Inbox\n\n"
                f"- Unprocessed notes: {inbox_count}\n"
                f"- Last capture: {last_capture or 'N/A'}"
            )
            inbox_hash = hashlib.sha256(inbox_text.encode("utf-8")).hexdigest()

            blocks.append(
                {
                    "type": "context",
                    "source": "inbox_stats",
                    "path": "inbox://stats",
                    "content": inbox_text,
                }
            )

            self.record_sent(inbox_hash, "inbox_stats")

            # ---- Vault-root CLAUDE.md -----------------------------------
            root_content, root_hash = self._read_vault_root_context()
            if root_content is not None:
                blocks.append(
                    {
                        "type": "context",
                        "source": "vault_root",
                        "path": "CLAUDE.md",
                        "content": root_content,
                    }
                )
                self.record_sent(root_hash, "vault_root")

            return Success(blocks)

        except Exception as exc:
            _log.warning("build_vault_info_response failed: %s", exc)
            return Failure(
                str(exc),
                recoverable=True,
                context={},
            )

    def build_read_response(
        self,
        paths: list[Path],
        registry: ProjectRegistry | None = None,
        include_context: bool = False,
        db_path: Path | None = None,
    ) -> Result[list[dict]]:
        """Build a response for reading specific notes.

        Reads each note body via ``read_note`` and when *include_context* is
        ``True``, injects minority-domain context first (bypasses dedup to
        force full re-injection).  When ``False``, no domain context is
        injected.

        A binary-backed note (``note_type == "attachment-summary"``) returns
        its AI summary body, not raw bytes.

        Args:
            paths:           Absolute paths to notes to read.
            registry:        Optional project→domain registry for context
                             injection.  If ``None``, no domain context is
                             injected.
            include_context: If ``True``, inject domain context before notes
                             and bypass dedup (re-send already-provided files).
            db_path:         Optional DB path override.

        Returns:
            ``Success(list[dict])`` — context blocks (optional) + read_note blocks.
        """
        try:
            from vault.reader import read_note  # noqa: C0415

            blocks: list[dict] = []

            # ---- Optional: inject minority-domain context ---------------
            if include_context and registry is not None:
                # Determine which domains the requested notes belong to
                project_domain = self._build_project_domain_map(registry)
                note_domains: set[str] = set()
                for path in paths:
                    # Derive project from the path: Projects/<Project>/...
                    project_name = self._project_name_from_path(path, registry)
                    if project_name:
                        domain = project_domain.get(project_name)
                        if domain is not None:
                            note_domains.add(domain)

                if note_domains:
                    context_blocks = self._collect_context_for_domains(
                        domain_names=note_domains,
                        registry=registry,
                        include_context=include_context,
                    )
                    blocks.extend(context_blocks)

            # ---- Read each note -----------------------------------------
            for path in paths:
                match read_note(path):
                    case Success(note):
                        note_block = {
                            "type": "read_note",
                            "path": str(path),
                            "content": note.content,
                            "content_hash": note.content_hash,
                            "metadata": {
                                "title": note.metadata.title,
                                "type": (
                                    note.metadata.type
                                    if isinstance(note.metadata.type, list)
                                    else [note.metadata.type]
                                ),
                                "tags": note.metadata.tags,
                            },
                        }
                        blocks.append(note_block)
                    case Failure() as f:
                        blocks.append(
                            {
                                "type": "read_note",
                                "path": str(path),
                                "content": f"[Error reading note: {f.error}]",
                                "content_hash": "",
                                "metadata": {},
                                "error": f.error,
                            }
                        )

            return Success(blocks)

        except Exception as exc:
            _log.warning("build_read_response failed: %s", exc)
            return Failure(
                str(exc),
                recoverable=True,
                context={"paths": [str(p) for p in paths]},
            )

    # ======================================================================
    # Project→Domain reverse map builder (RED 3)
    # ======================================================================

    def _build_project_domain_map(
        self, registry: ProjectRegistry
    ) -> dict[str, str | None]:
        """Build a reverse lookup: project_name → domain_name.

        Projects in the ``"Uncategorized"`` pseudo-domain map to ``None``
        (no real domain).  Projects not found in any group are absent from
        the returned dict.

        Args:
            registry: The live ``ProjectRegistry``.

        Returns:
            ``{project_name: domain_name | None}`` — ``None`` means
            Uncategorized or unknown.
        """
        mapping: dict[str, str | None] = {}
        for domain_name, group in registry.groups.items():
            for entry in group.projects:
                if domain_name == "Uncategorized":
                    mapping[entry.name] = None
                else:
                    mapping[entry.name] = domain_name
        return mapping

    # ======================================================================
    # Concentration counting (RED 2)
    # ======================================================================

    def _count_concentration(
        self,
        cards: list,
        project_domain: dict[str, str | None],
    ) -> tuple[float, list[str]]:
        """Count how concentrated the search results are by domain.

        Args:
            cards:           Search result cards (each with
                             ``metadata["project"]``).
            project_domain:  Reverse map from ``_build_project_domain_map``.

        Returns:
            ``(top_domain_share, sorted_domains)`` where *top_domain_share*
            is the fraction of cards belonging to the single largest domain
            and *sorted_domains* lists domain names in descending frequency
            order (project-only results that lack a real domain contribute
            to project count but NOT to any domain's count).
        """
        total = len(cards)
        if total == 0:
            return 0.0, []

        # Count by domain (real domains only; Uncategorized/unknown excluded)
        domain_counts: dict[str, int] = {}
        for card in cards:
            project = card.metadata.get("project", "")
            if project:
                domain = project_domain.get(project)
                if domain is not None:  # real domain only
                    domain_counts[domain] = domain_counts.get(domain, 0) + 1

        if not domain_counts:
            return 0.0, []

        top_count = max(domain_counts.values())
        top_share = top_count / total

        # Sort domains by count descending, then alphabetically for ties
        sorted_domains = sorted(
            domain_counts.keys(),
            key=lambda d: (-domain_counts[d], d),
        )

        return top_share, sorted_domains

    # ======================================================================
    # Context file reading helpers (RED 2, 4, 7)
    # ======================================================================

    def _collect_context_for_domains(
        self,
        domain_names: set[str],
        registry: ProjectRegistry,
        include_context: bool,
        max_files: int | None = None,
    ) -> list[dict]:
        """Read CLAUDE.md + context.yaml for each domain and its projects.

        Returns a list of context blocks.  Files that have already been sent
        this conversation are replaced with a short note unless
        *include_context* is ``True``.

        Args:
            domain_names:    Domain names to collect context for.
            registry:        Live project→domain registry.
            include_context: If ``True``, force full re-injection even for
                             previously-sent files.
            max_files:       Maximum number of context blocks to return.
                             If ``None``, no cap is applied.

        Returns:
            A list of ``{"type": "context", ...}`` blocks.
        """
        from core.config import CONFIG  # noqa: C0415

        include_yaml = CONFIG.main.mcp.context_injection.include_context_yaml
        blocks: list[dict] = []

        for domain_name in sorted(domain_names):
            group = registry.groups.get(domain_name)
            if group is None:
                continue

            # Domain-level context.yaml
            if include_yaml and group.domain_path is not None:
                context_yaml_path = group.domain_path / "context.yaml"
                block = self._read_and_dedup_context_file(
                    context_yaml_path,
                    source=f"domain:{domain_name}",
                    include_context=include_context,
                )
                if block is not None:
                    blocks.append(block)
                    if max_files is not None and len(blocks) >= max_files:
                        return blocks

            # Project-level CLAUDE.md for each project in this domain
            for entry in group.projects:
                claude_path = entry.path / "CLAUDE.md"
                block = self._read_and_dedup_context_file(
                    claude_path,
                    source=f"project:{entry.name}",
                    include_context=include_context,
                )
                if block is not None:
                    blocks.append(block)
                    if max_files is not None and len(blocks) >= max_files:
                        return blocks

        return blocks

    def _read_and_dedup_context_file(
        self,
        path: Path,
        source: str,
        include_context: bool,
    ) -> dict | None:
        """Read a single context file, apply dedup, return a block or None.

        Args:
            path:            Absolute path to the context file.
            source:          Source label (e.g. ``"project:Alpha"``).
            include_context: If ``True``, force full re-injection.

        Returns:
            A context block dict, or ``None`` if the file doesn't exist or
            was deduped (and include_context is False).
        """
        content, content_hash = self._read_context_file(path, source)
        if content is None:
            return None  # file doesn't exist

        # Dedup check
        if not include_context:
            already, label = self.is_already_provided(content_hash)
            if already:
                # Return a short note instead of full content
                return {
                    "type": "context",
                    "source": source,
                    "path": str(path),
                    "content": (
                        f"[Context for {source} was already provided "
                        f"earlier in this conversation.]"
                    ),
                }

        # Record and return full block
        self.record_sent(content_hash, source)
        return {
            "type": "context",
            "source": source,
            "path": str(path),
            "content": content,
        }

    def _read_context_file(
        self, path: Path, source_label: str
    ) -> tuple[str | None, str]:
        """Read a context file from disk and return (content, content_hash).

        Handles both CLAUDE.md and context.yaml files.  Returns
        ``(None, "")`` if the file does not exist (graceful degrade).

        Args:
            path:         Absolute path to the file.
            source_label: Human-readable label for logging (not used in
                          the returned value, but available for subclasses).

        Returns:
            ``(content, sha256_hex_hash)`` or ``(None, "")``.
        """
        try:
            if not path.is_file():
                return (None, "")
            content = path.read_text(encoding="utf-8")
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            return (content, content_hash)
        except (OSError, UnicodeDecodeError) as exc:
            _log.debug("Cannot read context file %s: %s", path, exc)
            return (None, "")

    # ======================================================================
    # Vault-info helpers (RED 5)
    # ======================================================================

    def _read_vault_root_context(self) -> tuple[str | None, str]:
        """Read the vault-root ``CLAUDE.md``.

        Returns:
            ``(content, content_hash)`` or ``(None, "")`` if missing.
        """
        from core.config import CONFIG  # noqa: C0415

        root = CONFIG.main.vault.root
        claude_path = root / "CLAUDE.md"
        return self._read_context_file(claude_path, "vault_root")

    def _get_inbox_stats(self, db_path: Path | None = None) -> tuple[int, str]:
        """Get inbox note count and most recent capture time from the catalog.

        Args:
            db_path: Optional DB path override.

        Returns:
            ``(inbox_count, last_capture_iso_string)``.
        """
        try:
            from storage.db import get_connection  # noqa: C0415

            with get_connection(db_path, readonly=True) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), MAX(updated_at) FROM documents "
                    "WHERE vault_path LIKE 'inbox/%'"
                ).fetchone()
                count = row[0] if row else 0
                last_updated = row[1] or "" if row else ""
                return count, last_updated
        except Exception:
            return 0, ""

    # ======================================================================
    # Config helpers (C-06 — no float literal in engine if/elif)
    # ======================================================================

    def _get_frequency_threshold(self) -> float:
        """Read the frequency threshold from config (C-06 compliant)."""
        from core.config import CONFIG  # noqa: C0415

        return CONFIG.main.mcp.context_injection.frequency_threshold

    def _get_max_context_files(self) -> int:
        """Read the max context files cap from config (C-06 compliant)."""
        from core.config import CONFIG  # noqa: C0415

        return CONFIG.main.mcp.context_injection.max_context_files

    # ======================================================================
    # Path helpers (RED 6)
    # ======================================================================

    @staticmethod
    def _project_name_from_path(path: Path, registry: ProjectRegistry) -> str | None:
        """Heuristic: extract a project name from a vault path.

        Looks for ``Projects/<Name>/...`` in the path and checks if
        *Name* is a known project in *registry*.

        Args:
            path:     Absolute path to a vault note.
            registry: Live ``ProjectRegistry`` to validate against.

        Returns:
            The project name, or ``None`` if it cannot be determined.
        """
        all_known = set(registry.all_project_names)
        parts = path.parts
        # Find "Projects" in the path parts, the next part is the project name
        for i, part in enumerate(parts):
            if part == "Projects" and i + 1 < len(parts):
                candidate = parts[i + 1]
                if candidate in all_known:
                    return candidate
        return None

    # ======================================================================
    # End of ContextInjectionEngine
    # ======================================================================
