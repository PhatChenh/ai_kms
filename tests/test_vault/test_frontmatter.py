"""tests/test_vault/test_frontmatter.py"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path


from core.result import Failure, Success


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def write_file(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# parse tests
# ---------------------------------------------------------------------------

def test_parse_minimal_note(tmp_path):
    """Unknown key 'title' goes into extra; NoteMetadata fields stay default."""
    from vault.frontmatter import parse

    f = write_file(tmp_path, "n.md", "---\ntitle: T\n---\nbody")
    result = parse(f)
    assert isinstance(result, Success)
    meta, body = result.value
    assert meta.extra == {"title": "T"}
    assert body.strip() == "body"


def test_parse_no_frontmatter(tmp_path):
    """File with no --- block: all metadata defaults, whole content is body."""
    from vault.frontmatter import parse

    f = write_file(tmp_path, "n.md", "just body text")
    result = parse(f)
    assert isinstance(result, Success)
    meta, body = result.value
    assert meta.extra == {}
    assert meta.tags == []
    assert meta.updated_by_human is False
    assert "just body text" in body


def test_parse_unknown_fields_preserved(tmp_path):
    """Unknown frontmatter key 'custom_field: 5' lands in extra."""
    from vault.frontmatter import parse

    f = write_file(tmp_path, "n.md", "---\ncustom_field: 5\n---\nbody")
    result = parse(f)
    assert isinstance(result, Success)
    meta, _ = result.value
    assert meta.extra == {"custom_field": 5}


def test_parse_pyyaml_bool_quirk_coerced(tmp_path):
    """PyYAML 1.1 parses 'yes'/'on'/'true' as bool; coercion keeps them as strings."""
    from vault.frontmatter import parse

    f = write_file(tmp_path, "n.md", "---\nstatus: yes\n---\nbody")
    result = parse(f)
    assert isinstance(result, Success)
    meta, _ = result.value
    assert meta.status == "yes"  # coerced from bool True → "yes"


def test_parse_malformed_yaml_returns_failure(tmp_path):
    """Unterminated frontmatter → Failure(recoverable=False) with path in context."""
    from vault.frontmatter import parse

    f = write_file(tmp_path, "n.md", "---\nkey: [\n---\nbody")
    result = parse(f)
    assert isinstance(result, Failure)
    assert result.recoverable is False
    assert str(f) in str(result.context)


def test_parse_missing_file_returns_failure(tmp_path):
    """Non-existent path → Failure."""
    from vault.frontmatter import parse

    result = parse(tmp_path / "ghost.md")
    assert isinstance(result, Failure)
    assert result.recoverable is False


# ---------------------------------------------------------------------------
# dumps tests
# ---------------------------------------------------------------------------

def test_dumps_round_trips_known_fields(tmp_path):
    """parse(dumps(meta, body)) returns identical metadata for all known fields."""
    from vault.frontmatter import NoteMetadata, dumps, parse

    meta = NoteMetadata(
        type="note",
        tags=["a", "b"],
        project="X",
        created=date(2026, 1, 1),
        updated=datetime(2026, 1, 2, tzinfo=timezone.utc),
        confidence=0.9,
        updated_by_human=True,
        summary="summ",
        source="email",
        source_file="attachment/f.pdf",
        status="active",
    )
    body = "hello"
    rendered = dumps(meta, body)
    f = write_file(tmp_path, "n.md", rendered)
    result = parse(f)
    assert isinstance(result, Success)
    meta2, body2 = result.value
    assert meta2.type == meta.type
    assert meta2.tags == meta.tags
    assert meta2.project == meta.project
    assert meta2.created == meta.created
    assert meta2.confidence == meta.confidence
    assert meta2.updated_by_human == meta.updated_by_human
    assert meta2.summary == meta.summary
    assert meta2.source == meta.source
    assert meta2.source_file == meta.source_file
    assert meta2.status == meta.status
    assert body2.strip() == body


def test_dumps_round_trips_extra_fields(tmp_path):
    """Extra keys in metadata.extra survive the round trip unchanged."""
    from vault.frontmatter import NoteMetadata, dumps, parse

    meta = NoteMetadata(extra={"custom_key": "val", "num": 42})
    rendered = dumps(meta, "body")
    f = write_file(tmp_path, "n.md", rendered)
    result = parse(f)
    assert isinstance(result, Success)
    meta2, _ = result.value
    assert meta2.extra.get("custom_key") == "val"
    assert meta2.extra.get("num") == 42


def test_dumps_uses_block_list_tags(tmp_path):
    """YAML output uses block list format for tags (Obsidian compatibility)."""
    from vault.frontmatter import NoteMetadata, dumps

    meta = NoteMetadata(tags=["a", "b"])
    rendered = dumps(meta, "body")
    assert "- a" in rendered
    assert "- b" in rendered
    assert "[a" not in rendered  # no flow-style list


def test_vietnamese_content_round_trips_without_escaping(tmp_path):
    """Vietnamese text in frontmatter values round-trips without \\uXXXX escaping."""
    from vault.frontmatter import NoteMetadata, dumps, parse

    meta = NoteMetadata(summary="Phân loại dự án", status="đang hoạt động")
    rendered = dumps(meta, "nội dung")
    assert "\\u" not in rendered  # no escaped Unicode
    f = write_file(tmp_path, "n.md", rendered)
    result = parse(f)
    assert isinstance(result, Success)
    meta2, body2 = result.value
    assert meta2.summary == "Phân loại dự án"
    assert meta2.status == "đang hoạt động"
    assert "nội dung" in body2


def test_source_file_field_round_trips(tmp_path):
    """source_file survives parse→dumps→parse; absent key parses to None."""
    from vault.frontmatter import NoteMetadata, dumps, parse

    # with source_file
    meta = NoteMetadata(source_file="attachment/Q2 Report.pdf")
    rendered = dumps(meta, "body")
    f = write_file(tmp_path, "with.md", rendered)
    r = parse(f)
    assert isinstance(r, Success)
    assert r.value[0].source_file == "attachment/Q2 Report.pdf"

    # without source_file
    meta2 = NoteMetadata()
    rendered2 = dumps(meta2, "body")
    f2 = write_file(tmp_path, "without.md", rendered2)
    r2 = parse(f2)
    assert isinstance(r2, Success)
    assert r2.value[0].source_file is None


def test_attachment_path_field_parsed_not_in_extra(tmp_path):
    """attachment_path in frontmatter lands on metadata field, not in extra."""
    from vault.frontmatter import parse

    f = write_file(
        tmp_path, "n.md",
        "---\nattachment_path: Projects/A/attachment/report.pdf\n---\nbody"
    )
    r = parse(f)
    assert isinstance(r, Success)
    meta, _ = r.value
    assert meta.attachment_path == "Projects/A/attachment/report.pdf"
    assert "attachment_path" not in meta.extra


def test_attachment_path_field_round_trips(tmp_path):
    """attachment_path survives dumps → parse unchanged."""
    from vault.frontmatter import NoteMetadata, dumps, parse

    meta = NoteMetadata(attachment_path="Projects/A/attachment/report.pdf")
    rendered = dumps(meta, "body")
    f = write_file(tmp_path, "rt.md", rendered)
    r = parse(f)
    assert isinstance(r, Success)
    assert r.value[0].attachment_path == "Projects/A/attachment/report.pdf"


def test_attachment_path_defaults_to_none_when_absent(tmp_path):
    """parse() on note without attachment_path sets field to None."""
    from vault.frontmatter import parse

    f = write_file(tmp_path, "n.md", "---\ntitle: hello\n---\nbody")
    r = parse(f)
    assert isinstance(r, Success)
    assert r.value[0].attachment_path is None


def test_unknown_keys_still_go_to_extra_with_attachment_path_known(tmp_path):
    """attachment_path known; other unknown keys still land in extra."""
    from vault.frontmatter import parse

    f = write_file(
        tmp_path, "n.md",
        "---\nattachment_path: Projects/A/attachment/f.pdf\nweirdo: 42\n---\nbody"
    )
    r = parse(f)
    assert isinstance(r, Success)
    meta, _ = r.value
    assert meta.attachment_path == "Projects/A/attachment/f.pdf"
    assert meta.extra == {"weirdo": 42}


# ---------------------------------------------------------------------------
# source_hash field (Phase 6 — Idempotent Capture)
# ---------------------------------------------------------------------------


def test_source_hash_round_trips(tmp_path):
    """source_hash survives dumps → parse unchanged."""
    from vault.frontmatter import NoteMetadata, dumps, parse

    fake_hash = "a" * 64  # 64 hex chars = sha256 digest length
    meta = NoteMetadata(source_hash=fake_hash)
    rendered = dumps(meta, "body")
    f = write_file(tmp_path, "sh.md", rendered)
    r = parse(f)
    assert isinstance(r, Success)
    assert r.value[0].source_hash == fake_hash


def test_source_hash_none_does_not_appear_in_yaml(tmp_path):
    """source_hash=None (default) must NOT write the key to YAML output."""
    from vault.frontmatter import NoteMetadata, dumps, parse

    meta = NoteMetadata(type="note")  # source_hash not set → default None
    rendered = dumps(meta, "body")
    assert "source_hash" not in rendered

    f = write_file(tmp_path, "no_hash.md", rendered)
    r = parse(f)
    assert isinstance(r, Success)
    assert r.value[0].source_hash is None


def test_source_hash_field_parsed_not_in_extra(tmp_path):
    """source_hash in frontmatter lands on metadata field, not in extra."""
    from vault.frontmatter import parse

    fake_hash = "b" * 64
    f = write_file(
        tmp_path, "n.md",
        f"---\nsource_hash: {fake_hash}\n---\nbody"
    )
    r = parse(f)
    assert isinstance(r, Success)
    meta, _ = r.value
    assert meta.source_hash == fake_hash
    assert "source_hash" not in meta.extra


# ---------------------------------------------------------------------------
# Phase 3A — _DEPRECATED_KEYS dumps() filter (TD-038)
# ---------------------------------------------------------------------------


def test_dumps_strips_deprecated_domain_key():
    """domain: in metadata.extra is stripped from YAML output by _DEPRECATED_KEYS filter."""
    from vault.frontmatter import NoteMetadata, dumps

    meta = NoteMetadata(extra={"domain": "finance"})
    rendered = dumps(meta, "body")
    assert "domain:" not in rendered


def test_dumps_preserves_non_deprecated_extra_keys():
    """Non-deprecated keys in metadata.extra survive dumps() unchanged."""
    from vault.frontmatter import NoteMetadata, dumps

    meta = NoteMetadata(extra={"custom_field": "value"})
    rendered = dumps(meta, "body")
    assert "custom_field:" in rendered


# ---------------------------------------------------------------------------
# Phase 3B — domain scalar field removal (TD-038)
# ---------------------------------------------------------------------------


def test_parse_yaml_with_domain_produces_no_domain_attr(tmp_path):
    """YAML with domain: parses to extra, not a domain attribute on NoteMetadata."""
    from vault.frontmatter import parse

    f = write_file(tmp_path, "n.md", "---\ndomain: finance\n---\nbody")
    result = parse(f)
    assert isinstance(result, Success)
    meta, _ = result.value
    assert hasattr(meta, "domain") is False
    assert meta.extra.get("domain") == "finance"
