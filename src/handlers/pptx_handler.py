"""PPTX handler — extracts slide text from PowerPoint files via python-pptx."""
from __future__ import annotations

from pathlib import Path

from core.result import Failure, Result, Success
from handlers.base import BaseHandler, RawContent
from handlers.registry import HandlerRegistry


@HandlerRegistry.register
class PptxHandler(BaseHandler):
    def can_handle(self, path: Path) -> bool:
        return path.suffix.lower() == ".pptx"

    def extract(self, path: Path) -> Result[RawContent]:
        try:
            from pptx import Presentation

            prs = Presentation(str(path))
            slides: list[str] = []
            for i, slide in enumerate(prs.slides, start=1):
                title = ""
                texts: list[str] = []
                for shape in slide.shapes:
                    if not shape.has_text_frame:
                        continue
                    shape_text = shape.text_frame.text.strip()
                    if not shape_text:
                        continue
                    if (
                        shape.is_placeholder
                        and shape.placeholder_format is not None
                        and shape.placeholder_format.idx == 0
                    ):
                        title = shape_text
                    else:
                        texts.append(shape_text)
                header = f'[Slide {i}: "{title}"]' if title else f"[Slide {i}]"
                if texts:
                    slides.append(header + "\n" + "\n".join(texts))
                elif title:
                    slides.append(header)
            text = "\n\n".join(slides).strip()
        except Exception as exc:
            return Failure(
                error=f"PPTX read failed: {exc}",
                recoverable=False,
                context={"path": str(path)},
            )
        return Success(RawContent(text=text, source_path=path, is_md=False))
