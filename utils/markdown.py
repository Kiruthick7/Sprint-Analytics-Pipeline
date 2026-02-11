"""Markdown and table formatting helpers."""


def escape_markdown_table_cell(text: str, max_length: int = 100) -> str:
    """Escape and clean text for markdown table cell."""
    if not text:
        return ""
    text = str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    text = " ".join(text.split())
    return text[:max_length] + "..." if len(text) > max_length else text
