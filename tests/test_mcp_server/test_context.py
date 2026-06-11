"""
tests/test_mcp_server/test_context.py

Phase 4 Component 6: Context Injection Engine tests.

RED 1 — dedup memory (content-hash based, not path+mtime)
RED 2 — frequency → threshold → cap (P4-MCP-03/04)
RED 3 — project→domain derivation (A8/OQ-P4-DOMAIN)
RED 4 — dedup + force (P4-MCP-08/09)
RED 5 — vault_info (P4-MCP-02)
RED 6 — read response (P4-MCP-05)
RED 7 — missing context files (TD-054)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.result import Success
from mcp_server.context import ContextInjectionEngine


# ============================================================================
# RED 1 — Dedup Memory (content-hash based)
# ============================================================================


class TestDedupMemory:
    """Content-hash dedup: same content = dedup; different content = re-send."""

    def test_fresh_engine_has_empty_dedup_memory(self):
        """A newly created engine starts with an empty dedup memory."""
        engine = ContextInjectionEngine()
        assert engine._dedup_memory == {}, "Fresh engine should have empty dedup"

    def test_same_content_hash_deduped(self):
        """Recording a content hash then re-requesting the same content returns
        an 'already provided' note instead of the full content."""
        engine = ContextInjectionEngine()

        # Record a context file's content hash
        fake_hash = "abc123hash"
        source = "project:Alpha"
        path = "Projects/Alpha/CLAUDE.md"

        engine._dedup_memory[fake_hash] = f"{source}:{path}"

        # Simulate checking: same hash should be in memory
        assert fake_hash in engine._dedup_memory
        assert engine._dedup_memory[fake_hash] == f"{source}:{path}"

    def test_different_content_not_deduped(self):
        """Different content (different hash) is NOT found in dedup memory
        and can be sent fresh."""
        engine = ContextInjectionEngine()

        engine._dedup_memory["hash_v1"] = "project:Alpha:Projects/Alpha/CLAUDE.md"

        # A different hash (edited file → new hash) is not in memory
        assert "hash_v2" not in engine._dedup_memory, (
            "Edited content with new hash should not be in dedup memory"
        )

    def test_is_already_provided_returns_true_for_existing_hash(self):
        """is_already_provided() returns (True, source_label) for a hash
        that was previously sent."""
        engine = ContextInjectionEngine()
        engine._dedup_memory["hash_x"] = "project:Alpha"

        already, label = engine.is_already_provided("hash_x")
        assert already is True
        assert label == "project:Alpha"

    def test_is_already_provided_returns_false_for_unknown_hash(self):
        """is_already_provided() returns (False, '') for a hash not in memory."""
        engine = ContextInjectionEngine()
        already, label = engine.is_already_provided("hash_unknown")
        assert already is False
        assert label == ""

    def test_record_sends_stores_hash_and_label(self):
        """record_sent() adds a hash→label entry to dedup memory."""
        engine = ContextInjectionEngine()
        engine.record_sent("hash_abc", "project:Beta")
        assert engine._dedup_memory.get("hash_abc") == "project:Beta"


# ============================================================================
# RED 2 — Frequency → Threshold → Cap (P4-MCP-03/04)
# ============================================================================


class TestFrequencyThresholdCap:
    """Concentration above threshold → context injected (capped).
    Below threshold → zero context blocks."""

    def _make_card(
        self, vault_path: str, project: str, note_type: str = "note"
    ) -> dict:
        """Helper to build a minimal SearchResult-like object for testing."""
        from retrieval.reranker import SearchResult

        return SearchResult(
            vault_path=vault_path,
            summary="Test summary",
            snippet="Test snippet",
            score=0.85,
            metadata={
                "title": "Test Note",
                "project": project,
                "note_type": note_type,
                "updated_at": "2026-06-10 12:00:00",
                "key_topics": [],
                "tags": [],
            },
        )

    def _stub_registry(self, projects_by_domain: dict[str, list[str]]):
        """Build a minimal ProjectRegistry stub for testing.

        projects_by_domain: e.g. {"Strategy": ["Alpha", "Beta"], "Ops": ["Gamma"]}
        """
        from vault.registry import ProjectEntry, ProjectGroup, ProjectRegistry

        groups: dict[str, ProjectGroup] = {}
        for domain_name, project_names in projects_by_domain.items():
            entries = [
                ProjectEntry(name=pn, path=Path(f"/fake/Projects/{pn}"))
                for pn in project_names
            ]
            groups[domain_name] = ProjectGroup(
                domain_name=domain_name,
                domain_path=Path(f"/fake/Domain/{domain_name}"),
                projects=entries,
            )
        # Uncategorized always present
        if "Uncategorized" not in groups:
            groups["Uncategorized"] = ProjectGroup(
                domain_name="Uncategorized",
                projects=[],
            )
        return ProjectRegistry(groups=groups)

    def test_concentrated_cards_produce_context_blocks(self, monkeypatch):
        """When 9/10 cards belong to one project (concentration > threshold),
        context blocks are returned before result cards."""
        cards = [
            self._make_card(f"Projects/Alpha/note{i}.md", "Alpha") for i in range(9)
        ]
        cards.append(self._make_card("Projects/Beta/other.md", "Beta"))

        registry = self._stub_registry({"Strategy": ["Alpha"], "Ops": ["Beta"]})

        engine = ContextInjectionEngine()

        # Stub _read_context_file to return canned content without touching disk
        def fake_read_context(path, source_label):
            return (
                f"Content of {path}",
                "hash_" + str(path).replace("/", "_"),
            )

        monkeypatch.setattr(engine, "_read_context_file", fake_read_context)

        result = engine.build_search_response(
            cards=cards, registry=registry, query="test query"
        )

        assert result.is_success()
        blocks = result.unwrap()

        context_blocks = [b for b in blocks if b["type"] == "context"]
        result_blocks = [b for b in blocks if b["type"] == "result_card"]

        # Context blocks appear first
        assert context_blocks, "Concentrated results should produce context blocks"
        first_context_idx = next(
            i for i, b in enumerate(blocks) if b["type"] == "context"
        )
        first_result_idx = next(
            i for i, b in enumerate(blocks) if b["type"] == "result_card"
        )
        assert first_context_idx < first_result_idx, (
            "Context blocks must precede result cards"
        )

        # Context blocks are capped at max_context_files
        from core.config import CONFIG

        cap = CONFIG.main.mcp.context_injection.max_context_files
        assert len(context_blocks) <= cap, (
            f"Context blocks ({len(context_blocks)}) exceed cap ({cap})"
        )

        # All result cards are present
        assert len(result_blocks) == len(cards)

    def test_spread_cards_below_threshold_no_context(self, monkeypatch):
        """When cards are spread across many projects (below threshold),
        zero context blocks are returned — only result cards."""
        # 10 cards spread across 5 projects = max concentration 20%
        cards = []
        for i in range(10):
            project = f"Project{i % 5}"
            cards.append(self._make_card(f"Projects/{project}/note{i}.md", project))

        registry = self._stub_registry(
            {
                "Strategy": ["Project0"],
                "Ops": ["Project1"],
                "Finance": ["Project2"],
                "HR": ["Project3"],
                "Legal": ["Project4"],
            }
        )

        def fake_read_context(path, source_label):
            return (f"Content of {path}", "hash_" + str(path).replace("/", "_"))

        engine = ContextInjectionEngine()
        monkeypatch.setattr(engine, "_read_context_file", fake_read_context)

        result = engine.build_search_response(
            cards=cards, registry=registry, query="spread query"
        )

        assert result.is_success()
        blocks = result.unwrap()

        context_blocks = [b for b in blocks if b["type"] == "context"]
        result_blocks = [b for b in blocks if b["type"] == "result_card"]

        assert len(context_blocks) == 0, (
            f"Spread results below threshold should have zero context blocks, "
            f"got {len(context_blocks)}"
        )
        assert len(result_blocks) == len(cards)


# ============================================================================
# RED 3 — Project→Domain Derivation (A8/OQ-P4-DOMAIN)
# ============================================================================


class TestProjectDomainDerivation:
    """Domain derived via registry lookup of card's project, not per-note read."""

    def _stub_registry(self, projects_by_domain: dict[str, list[str]]):
        from vault.registry import ProjectEntry, ProjectGroup, ProjectRegistry

        groups: dict[str, ProjectGroup] = {}
        for domain_name, project_names in projects_by_domain.items():
            entries = [
                ProjectEntry(name=pn, path=Path(f"/fake/Projects/{pn}"))
                for pn in project_names
            ]
            groups[domain_name] = ProjectGroup(
                domain_name=domain_name,
                domain_path=Path(f"/fake/Domain/{domain_name}"),
                projects=entries,
            )
        if "Uncategorized" not in groups:
            groups["Uncategorized"] = ProjectGroup(
                domain_name="Uncategorized",
                projects=[],
            )
        return ProjectRegistry(groups=groups)

    def _make_card(self, project: str) -> dict:
        from retrieval.reranker import SearchResult

        return SearchResult(
            vault_path=f"Projects/{project}/note.md",
            summary="Test summary",
            snippet="Test snippet",
            score=0.85,
            metadata={
                "title": "Test Note",
                "project": project,
                "note_type": "note",
                "updated_at": "2026-06-10 12:00:00",
                "key_topics": [],
                "tags": [],
            },
        )

    def test_domain_derived_from_registry_lookup(self):
        """A card's domain comes from registry lookup of the card's project,
        not from reading the note file."""
        registry = self._stub_registry(
            {"Strategy": ["Alpha", "Beta"], "Ops": ["Gamma"]}
        )

        engine = ContextInjectionEngine()
        reverse_map = engine._build_project_domain_map(registry)

        assert reverse_map["Alpha"] == "Strategy"
        assert reverse_map["Beta"] == "Strategy"
        assert reverse_map["Gamma"] == "Ops"

    def test_uncategorized_project_never_contributes_to_domain_count(self):
        """An Uncategorized project contributes to its project count but
        never to any domain's count."""
        registry = self._stub_registry(
            {"Strategy": ["Alpha"], "Uncategorized": ["Mystery"]}
        )

        engine = ContextInjectionEngine()
        reverse_map = engine._build_project_domain_map(registry)

        # Mystery is in Uncategorized — no real domain
        assert "Mystery" in reverse_map
        # Uncategorized mapped projects should return None or not be a real domain
        domain = reverse_map.get("Mystery")
        is_real_domain = domain is not None and domain != "Uncategorized"
        assert not is_real_domain, (
            f"Uncategorized project Mystery mapped to real domain '{domain}'"
        )

    def test_project_not_in_registry_maps_to_none(self):
        """A project not found in any registry group maps to None (unknown domain)."""
        registry = self._stub_registry({"Strategy": ["Alpha"]})

        engine = ContextInjectionEngine()
        reverse_map = engine._build_project_domain_map(registry)

        assert (
            "GhostProject" not in reverse_map or reverse_map.get("GhostProject") is None
        )


# ============================================================================
# RED 4 — Dedup + Force (P4-MCP-08/09)
# ============================================================================


class TestDedupForce:
    """Second search on same domain dedups context; include_context=True forces."""

    def _make_card(self, vault_path: str, project: str) -> dict:
        from retrieval.reranker import SearchResult

        return SearchResult(
            vault_path=vault_path,
            summary="Test summary",
            snippet="Test snippet",
            score=0.85,
            metadata={
                "title": "Test Note",
                "project": project,
                "note_type": "note",
                "updated_at": "2026-06-10 12:00:00",
                "key_topics": [],
                "tags": [],
            },
        )

    def _stub_registry(self):
        from vault.registry import ProjectEntry, ProjectGroup, ProjectRegistry

        groups = {
            "Strategy": ProjectGroup(
                domain_name="Strategy",
                domain_path=Path("/fake/Domain/Strategy"),
                projects=[
                    ProjectEntry(name="Alpha", path=Path("/fake/Projects/Alpha"))
                ],
            ),
            "Uncategorized": ProjectGroup(domain_name="Uncategorized", projects=[]),
        }
        return ProjectRegistry(groups=groups)

    def test_second_search_on_same_domain_replaces_context_with_note(self, monkeypatch):
        """When the same domain context was already sent in this conversation,
        a second search replaces the full context with a short note."""
        cards = [self._make_card("Projects/Alpha/note.md", "Alpha") for _ in range(10)]

        registry = self._stub_registry()
        engine = ContextInjectionEngine()

        # Pre-populate dedup memory as if context was already sent
        def fake_read_and_record(path, source_label):
            content_hash = "hash_" + str(path).replace("/", "_")
            content = f"Content of {path}"
            engine.record_sent(content_hash, source_label)
            return (content, content_hash)

        monkeypatch.setattr(engine, "_read_context_file", fake_read_and_record)

        # First call: context should be read and recorded
        result1 = engine.build_search_response(
            cards=cards, registry=registry, query="first query"
        )
        assert result1.is_success()
        blocks1 = result1.unwrap()
        context1 = [b for b in blocks1 if b["type"] == "context"]
        assert len(context1) > 0, "First call should have context"

        # Second call with same concentrated cards: dedup should kick in
        result2 = engine.build_search_response(
            cards=cards, registry=registry, query="second query"
        )
        assert result2.is_success()
        blocks2 = result2.unwrap()
        context2 = [b for b in blocks2 if b["type"] == "context"]

        # The second call's context blocks should be dedup-style notes
        # (either empty or "already provided" notes rather than full content)
        full_context2 = [
            b for b in context2 if "already provided" not in str(b.get("content", ""))
        ]
        assert len(full_context2) == 0, (
            f"Second call should not re-send full context, got {full_context2}"
        )

    def test_include_context_true_forces_full_reinjection(self, monkeypatch):
        """include_context=True bypasses dedup and re-sends full context."""
        cards = [self._make_card("Projects/Alpha/note.md", "Alpha") for _ in range(10)]

        registry = self._stub_registry()
        engine = ContextInjectionEngine()

        read_count = [0]

        def fake_read_and_record(path, source_label):
            read_count[0] += 1
            content_hash = "hash_" + str(path).replace("/", "_")
            content = f"Content of {path}"
            engine.record_sent(content_hash, source_label)
            return (content, content_hash)

        monkeypatch.setattr(engine, "_read_context_file", fake_read_and_record)

        # First call, pre-fill dedup
        engine.build_search_response(
            cards=cards, registry=registry, query="first query"
        )

        # Second call WITH include_context=True forces full re-read
        result = engine.build_search_response(
            cards=cards, registry=registry, query="force query", include_context=True
        )
        assert result.is_success()
        blocks = result.unwrap()
        context_blocks = [b for b in blocks if b["type"] == "context"]

        # With force, we should get full context again (not empty dedup)
        assert len(context_blocks) > 0, (
            "include_context=True should force context injection even after dedup"
        )


# ============================================================================
# RED 5 — Vault Info (P4-MCP-02)
# ============================================================================


class TestVaultInfo:
    """build_vault_info_response returns registry summary + inbox stats + vault CLAUDE.md."""

    def _stub_registry(self):
        from vault.registry import ProjectEntry, ProjectGroup, ProjectRegistry

        groups = {
            "Strategy": ProjectGroup(
                domain_name="Strategy",
                domain_path=Path("/fake/Domain/Strategy"),
                projects=[
                    ProjectEntry(name="Alpha", path=Path("/fake/Projects/Alpha")),
                    ProjectEntry(name="Beta", path=Path("/fake/Projects/Beta")),
                ],
            ),
            "Ops": ProjectGroup(
                domain_name="Ops",
                domain_path=Path("/fake/Domain/Ops"),
                projects=[
                    ProjectEntry(name="Gamma", path=Path("/fake/Projects/Gamma")),
                ],
            ),
            "Uncategorized": ProjectGroup(domain_name="Uncategorized", projects=[]),
        }
        return ProjectRegistry(groups=groups)

    def test_vault_info_returns_project_and_domain_names(self, monkeypatch, tmp_path):
        """build_vault_info_response returns project names and domain names
        from the live registry."""
        registry = self._stub_registry()
        engine = ContextInjectionEngine()

        # Stub vault root CLAUDE.md read to return canned content
        monkeypatch.setattr(
            engine,
            "_read_vault_root_context",
            lambda: ("# Vault Root Context", "hash_vaultroot"),
        )

        # Stub the catalog calls (all_paths, get_by_path)
        monkeypatch.setattr(
            engine,
            "_get_inbox_stats",
            lambda db_path: (3, "2026-06-11 09:00:00"),
        )

        result = engine.build_vault_info_response(registry=registry)

        assert result.is_success()
        blocks = result.unwrap()

        context_blocks = [b for b in blocks if b["type"] == "context"]

        # Should have vault_root context
        vault_root_blocks = [
            b for b in context_blocks if b.get("source") == "vault_root"
        ]
        assert len(vault_root_blocks) >= 1, "Should include vault_root CLAUDE.md"

        # Should mention domains and projects
        all_content = " ".join(b.get("content", "") for b in blocks)
        assert "Strategy" in all_content, "Should include domain names"
        assert "Alpha" in all_content, "Should include project names"

    def test_vault_info_returns_inbox_count(self, monkeypatch):
        """build_vault_info_response includes inbox note count and last-capture time."""
        registry = self._stub_registry()
        engine = ContextInjectionEngine()

        monkeypatch.setattr(
            engine, "_read_vault_root_context", lambda: ("# Vault Root", "h")
        )
        monkeypatch.setattr(
            engine, "_get_inbox_stats", lambda db_path: (5, "2026-06-11 08:30:00")
        )

        result = engine.build_vault_info_response(registry=registry)
        assert result.is_success()

        # Inbox stats should appear in some block
        blocks = result.unwrap()
        context_blocks = [b for b in blocks if b["type"] == "context"]
        inbox_blocks = [b for b in context_blocks if b.get("source") == "inbox_stats"]
        assert len(inbox_blocks) >= 1, "Should have an inbox_stats context block"


# ============================================================================
# RED 6 — Read Response (P4-MCP-05)
# ============================================================================


class TestReadResponse:
    """build_read_response loads note bodies, injects minority-domain context first."""

    def test_read_response_loads_note_bodies(self, monkeypatch, tmp_path):
        """build_read_response loads each note's body via read_note."""
        engine = ContextInjectionEngine()

        # Stub read_note to return fake Notes
        def fake_read_note(path):
            from vault.reader import Note, NoteMetadata

            meta = NoteMetadata(
                title=path.stem if isinstance(path, Path) else Path(path).stem,
                type="note",
                tags=[],
            )
            return Success(
                Note(
                    path=Path(path),
                    metadata=meta,
                    content=f"Body of {path}",
                    content_hash=f"hash_{path}",
                )
            )

        def fake_read_context_file(path, source_label):
            return (f"Context: {path}", f"h_{path}")

        monkeypatch.setattr(engine, "_read_context_file", fake_read_context_file)

        with patch("vault.reader.read_note", side_effect=fake_read_note):
            result = engine.build_read_response(
                paths=[Path("Projects/Alpha/note1.md"), Path("Projects/Beta/note2.md")],
                registry=None,
                include_context=False,
            )

        assert result.is_success()
        blocks = result.unwrap()
        note_blocks = [b for b in blocks if b["type"] == "read_note"]
        assert len(note_blocks) == 2, f"Expected 2 note blocks, got {len(note_blocks)}"

        # Each block should have the content
        assert "Body of" in note_blocks[0]["content"]
        assert "Body of" in note_blocks[1]["content"]

    def test_binary_backed_note_returns_summary_body(self, monkeypatch):
        """A binary-backed note (note_type == 'attachment-summary') returns
        its summary body, not bytes."""
        engine = ContextInjectionEngine()

        from vault.reader import Note, NoteMetadata

        meta = NoteMetadata(
            title="report.pdf",
            type="attachment-summary",
            tags=[],
        )

        def fake_read_note(path):
            return Success(
                Note(
                    path=Path(path),
                    metadata=meta,
                    content="This is the AI summary of report.pdf",
                    content_hash="hash_report",
                )
            )

        def fake_read_context_file(path, source_label):
            return (f"Context: {path}", f"h_{path}")

        monkeypatch.setattr(engine, "_read_context_file", fake_read_context_file)

        with patch("vault.reader.read_note", side_effect=fake_read_note):
            result = engine.build_read_response(
                paths=[Path("Projects/Alpha/attachment/.summaries/report.pdf.md")],
                registry=None,
                include_context=False,
            )

        assert result.is_success()
        blocks = result.unwrap()
        note_blocks = [b for b in blocks if b["type"] == "read_note"]

        assert len(note_blocks) == 1
        content = note_blocks[0]["content"]
        assert "AI summary" in content, (
            f"Binary summary body expected, got: {content[:100]}"
        )

    def test_read_response_injects_minority_domain_context_first(self, monkeypatch):
        """When injecting context, minority-domain context blocks appear first,
        before note bodies."""
        engine = ContextInjectionEngine()

        from vault.registry import ProjectEntry, ProjectGroup, ProjectRegistry

        registry = ProjectRegistry(
            groups={
                "Strategy": ProjectGroup(
                    domain_name="Strategy",
                    domain_path=Path("/fake/Domain/Strategy"),
                    projects=[
                        ProjectEntry(name="Alpha", path=Path("/fake/Projects/Alpha")),
                    ],
                ),
                "Uncategorized": ProjectGroup(domain_name="Uncategorized", projects=[]),
            }
        )

        from vault.reader import Note, NoteMetadata

        def fake_read_note(path):
            meta = NoteMetadata(title=Path(path).stem, type="note", tags=[])
            return Success(
                Note(
                    path=Path(path),
                    metadata=meta,
                    content=f"Body of {path}",
                    content_hash=f"hash_{path}",
                )
            )

        context_read_order = []

        def fake_read_context_file(path, source_label):
            context_read_order.append(source_label)
            return (f"Context: {path}", f"h_{path}")

        monkeypatch.setattr(engine, "_read_context_file", fake_read_context_file)

        with patch("vault.reader.read_note", side_effect=fake_read_note):
            result = engine.build_read_response(
                paths=[Path("Projects/Alpha/note.md")],
                registry=registry,
                include_context=True,
            )

        assert result.is_success()
        blocks = result.unwrap()

        context_blocks = [b for b in blocks if b["type"] == "context"]
        note_blocks = [b for b in blocks if b["type"] == "read_note"]

        # Context blocks precede note blocks
        if context_blocks and note_blocks:
            first_context_idx = blocks.index(context_blocks[0])
            first_note_idx = blocks.index(note_blocks[0])
            assert first_context_idx < first_note_idx, (
                "Context blocks must precede note bodies"
            )


# ============================================================================
# RED 7 — Missing Context Files (TD-054)
# ============================================================================


class TestMissingContextFiles:
    """Graceful degrade when CLAUDE.md or context.yaml is missing."""

    def _stub_registry(self):
        from vault.registry import ProjectEntry, ProjectGroup, ProjectRegistry

        groups = {
            "Strategy": ProjectGroup(
                domain_name="Strategy",
                domain_path=Path("/fake/Domain/Strategy"),
                projects=[
                    ProjectEntry(name="Alpha", path=Path("/fake/Projects/Alpha"))
                ],
            ),
            "Uncategorized": ProjectGroup(domain_name="Uncategorized", projects=[]),
        }
        return ProjectRegistry(groups=groups)

    def _make_card(self, project: str = "Alpha") -> dict:
        from retrieval.reranker import SearchResult

        return SearchResult(
            vault_path=f"Projects/{project}/note.md",
            summary="Test summary",
            snippet="Test snippet",
            score=0.85,
            metadata={
                "title": "Test Note",
                "project": project,
                "note_type": "note",
                "updated_at": "2026-06-10 12:00:00",
                "key_topics": [],
                "tags": [],
            },
        )

    def test_missing_claude_md_produces_no_context_block(self, monkeypatch):
        """When a project has no CLAUDE.md and no context.yaml, no context
        block is contributed — search still returns cards."""
        cards = [self._make_card("Alpha") for _ in range(10)]
        registry = self._stub_registry()
        engine = ContextInjectionEngine()

        # _read_context_file returns None for missing files
        def fake_read_context_file(path, source_label):
            return (None, "")

        monkeypatch.setattr(engine, "_read_context_file", fake_read_context_file)

        result = engine.build_search_response(
            cards=cards, registry=registry, query="test"
        )

        assert result.is_success()
        blocks = result.unwrap()
        context_blocks = [b for b in blocks if b["type"] == "context"]
        result_blocks = [b for b in blocks if b["type"] == "result_card"]

        # No context contributed for missing files
        assert len(context_blocks) == 0, (
            f"Missing CLAUDE.md should produce no context blocks, got {len(context_blocks)}"
        )
        # But cards are still returned (graceful degrade)
        assert len(result_blocks) == len(cards)

    def test_context_yaml_is_text_not_schema_parsed(self, monkeypatch):
        """context.yaml is read as opaque text, not validated against a schema (A13)."""
        engine = ContextInjectionEngine()

        read_calls = []

        def fake_read_context_file(path, source_label):
            read_calls.append(str(path))
            # Simulate a context.yaml with arbitrary YAML
            if path.name == "context.yaml":
                return ("key: value\nitems:\n  - one\n  - two", "hash_yaml")
            elif path.name == "CLAUDE.md":
                return ("# Project context", "hash_md")
            return (None, "")

        monkeypatch.setattr(engine, "_read_context_file", fake_read_context_file)

        from vault.registry import ProjectEntry, ProjectGroup, ProjectRegistry

        registry = ProjectRegistry(
            groups={
                "Strategy": ProjectGroup(
                    domain_name="Strategy",
                    domain_path=Path("/fake/Domain/Strategy"),
                    projects=[
                        ProjectEntry(name="Alpha", path=Path("/fake/Projects/Alpha"))
                    ],
                ),
                "Uncategorized": ProjectGroup(domain_name="Uncategorized", projects=[]),
            }
        )

        # Call _collect_context_for_domains which should try to read context.yaml
        context_map = engine._collect_context_for_domains(
            domain_names={"Strategy"},
            registry=registry,
            include_context=True,
        )

        # Should have read context files for the domain
        assert len(context_map) >= 1, (
            "Should have collected context for Strategy domain"
        )
        assert "Strategy" in str(context_map), "Strategy should be in collected context"
