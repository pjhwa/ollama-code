from __future__ import annotations
import re


_LANG_ALIASES: dict[str, list[str]] = {
    "python": ["python", "py", "python3"],
    "javascript": ["javascript", "js", "node"],
    "go": ["go", "golang"],
    "bash": ["bash", "sh", "shell"],
}


def _aliases(lang: str) -> list[str]:
    lang = lang.lower()
    return _LANG_ALIASES.get(lang, [lang])


def extract_code(text: str, lang: str) -> str:
    """Extract first code block from LLM response.

    Priority:
    1. Fenced block with matching language tag
    2. Fenced block with no language tag
    3. Full text stripped of <think> tags
    """
    # Strip <think> tags (Qwen3 thinking output)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    # Try fenced block with matching language
    for alias in _aliases(lang):
        pattern = rf"```{re.escape(alias)}\n(.*?)```"
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1).strip()

    # Try any fenced block
    m = re.search(r"```\w*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()

    # Fallback: return stripped text
    return text.strip()
