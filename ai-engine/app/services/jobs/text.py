from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
        "tr", "table", "section", "article", "header", "footer", "blockquote",
    }
)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template"})
_SPACES = re.compile(r"[ \t   ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Convert job-board HTML into readable plain text with line breaks."""
    if not value:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # pragma: no cover - HTMLParser is lenient
        text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text).replace("\r", "")
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def join_sections(*sections: tuple[str | None, str | None]) -> str:
    """Join (heading, html) pairs into one plain-text description."""
    blocks: list[str] = []
    for heading, body in sections:
        text = html_to_text(body)
        if not text:
            continue
        blocks.append(f"{heading}\n{text}" if heading else text)
    return "\n\n".join(blocks)
