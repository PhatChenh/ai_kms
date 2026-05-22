"""XLSX handler — extracts text from Excel workbooks via openpyxl.

Each worksheet becomes a section headed by [Sheet: "name"]. The first row
is treated as column headers; subsequent non-empty rows follow as pipe-
separated values.

Known limitation (TD-H8): merged cells produce None fill-cells in output,
e.g. "Header | None | None". The LLM handles this gracefully; proper merged-
cell detection is deferred to Phase 3+.

Only .xlsx (OOXML) is supported. Legacy .xls files are not handled (TD-H9).
"""
from __future__ import annotations

from pathlib import Path

from core.result import Failure, Result, Success
from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry


@HandlerRegistry.register
class XlsxHandler(BaseHandler):
    """Extract text from .xlsx files with per-sheet section headers."""

    def can_handle(self, path: Path) -> bool:
        """Return True for .xlsx files (case-insensitive)."""
        return path.suffix.lower() == ".xlsx"

    def extract(self, path: Path) -> Result[RawContent]:
        """Extract all sheet content as structured text.

        Args:
            path: Absolute path to the .xlsx file.

        Returns:
            Success(RawContent) with text containing one section per non-empty
            sheet, or Failure if the file cannot be read.
        """
        try:
            import openpyxl

            wb = openpyxl.load_workbook(str(path), data_only=True)
            sections: list[str] = []
            for sheet in wb.worksheets:
                rows = list(sheet.iter_rows(values_only=True))
                if not rows:
                    continue
                header = " | ".join(
                    str(c) if c is not None else "" for c in rows[0]
                )
                body_rows = [
                    " | ".join(str(c) if c is not None else "" for c in row)
                    for row in rows[1:]
                    if any(c is not None for c in row)
                ]
                section = f'[Sheet: "{sheet.title}"]\n{header}'
                if body_rows:
                    section += "\n" + "\n".join(body_rows)
                sections.append(section)
            text = "\n\n".join(sections).strip()
        except Exception as exc:
            return Failure(
                error=f"XLSX read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
