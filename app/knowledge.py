"""Product knowledge is content, not code: read from knowledge/ at run time."""
import json

from . import config

EDITABLE = ("amasol.md", "klaricore.md", "tips.md", "writing-rules.md", "stages.json")


def read(name: str) -> str:
    p = config.KNOWLEDGE_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def write(name: str, text: str):
    if name not in EDITABLE:
        raise ValueError("not an editable knowledge file")
    if name.endswith(".json"):
        json.loads(text)  # validate
    (config.KNOWLEDGE_DIR / name).write_text(text, encoding="utf-8")


def stages() -> dict:
    try:
        return json.loads(read("stages.json") or "{}")
    except ValueError:
        return {}


def stage_name(product: str, key: str) -> str:
    for s in stages().get(product, []):
        if s["key"] == key:
            return s["name"]
    return key or ""


def stage_focus(product: str, key: str) -> str:
    for s in stages().get(product, []):
        if s["key"] == key:
            return s["focus"]
    return ""


def products_text(products: list[str]) -> str:
    parts = []
    for p in products:
        if p in ("amasol", "klaricore"):
            parts.append(read(f"{p}.md"))
    return "\n\n".join(parts)


def tips() -> list[str]:
    return [l[2:].strip() for l in read("tips.md").splitlines() if l.startswith("- ")]


def writing_rules() -> str:
    return read("writing-rules.md")
