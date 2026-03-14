import re


def strip_markdown(text: str) -> str:
    """Remove markdown formatting so the text reads cleanly in WhatsApp.

    WhatsApp does not render markdown: asterisks, hashes and backticks appear
    as literal characters and degrade readability.
    """
    # Fenced code blocks ```...```
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).strip("`").strip(), text)
    # Inline code `...`
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Bold+italic ***text*** or ___text___
    text = re.sub(r"\*{3}(.+?)\*{3}", r"\1", text)
    text = re.sub(r"_{3}(.+?)_{3}", r"\1", text)
    # Bold **text** or __text__
    text = re.sub(r"\*{2}(.+?)\*{2}", r"\1", text)
    text = re.sub(r"_{2}(.+?)_{2}", r"\1", text)
    # Italic *text* or _text_
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    # Headings: # Title
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Markdown links [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Bullet list markers (-, *, +) at line start → keep content
    text = re.sub(r"^[\*\-\+]\s+", "", text, flags=re.MULTILINE)
    # Trailing spaces
    text = re.sub(r" +\n", "\n", text)
    # Collapse 3+ consecutive blank lines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
