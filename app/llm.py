"""Model calls: one place for the client, web search, structured output and cost logging."""
import json
import logging
import time

import anthropic

from . import config, db
from .rules import clean

log = logging.getLogger("mp.llm")

_client = None


def client():
    global _client
    if _client is None:
        kw = {"api_key": config.LLM_API_KEY, "max_retries": 3, "timeout": 600}
        if config.LLM_BASE_URL:
            kw["base_url"] = config.LLM_BASE_URL
        _client = anthropic.Anthropic(**kw)
    return _client


class Ctx:
    """Who a call is for, so its cost lands on the right run."""

    def __init__(self, run_id=None, account_id=None):
        self.run_id = run_id
        self.account_id = account_id


def _cost(u) -> tuple[float, int]:
    searches = 0
    stu = getattr(u, "server_tool_use", None)
    if stu is not None:
        searches = getattr(stu, "web_search_requests", 0) or 0
    usd = ((u.input_tokens or 0) * config.PRICE_IN
           + (u.output_tokens or 0) * config.PRICE_OUT
           + (getattr(u, "cache_creation_input_tokens", 0) or 0) * config.PRICE_CACHE_WRITE
           + (getattr(u, "cache_read_input_tokens", 0) or 0) * config.PRICE_CACHE_READ) / 1e6
    usd += searches * config.PRICE_SEARCH_PER_1000 / 1000
    return usd, searches


def _log_cost(ctx: Ctx | None, step: str, resp):
    u = resp.usage
    usd, searches = _cost(u)
    db.insert("cost_log", {
        "run_id": ctx.run_id if ctx else None, "account_id": ctx.account_id if ctx else None,
        "step": step, "model": resp.model,
        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "searches": searches, "usd": round(usd, 5), "created_at": db.now()})
    if ctx and ctx.run_id:
        db.ex("UPDATE runs SET cost_usd = COALESCE(cost_usd,0) + ? WHERE id=?", (usd, ctx.run_id))


def _create(**kw):
    for attempt in range(4):
        try:
            if kw.get("max_tokens", 0) > 16000:
                with client().messages.stream(**kw) as s:
                    return s.get_final_message()
            return client().messages.create(**kw)
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as e:
            if attempt == 3:
                raise
            wait = 5 * (attempt + 1)
            log.warning("model call failed (%s), retrying in %ss", type(e).__name__, wait)
            time.sleep(wait)


def _tool_input(resp, name):
    for b in resp.content:
        if b.type == "tool_use" and b.name == name:
            return b.input if isinstance(b.input, dict) else json.loads(b.input)
    return None


def _text(resp):
    return "\n".join(b.text for b in resp.content if b.type == "text")


def structured(ctx, step, system, prompt, tool: dict, *, effort=None, max_tokens=16000):
    """One call that must answer through `tool`. Returns the tool input (cleaned)."""
    tools = [tool]
    msgs = [{"role": "user", "content": prompt}]
    for attempt in range(2):
        resp = _create(model=config.MODEL, max_tokens=max_tokens, system=system, tools=tools,
                       tool_choice={"type": "auto"}, messages=msgs,
                       output_config={"effort": effort or config.WRITE_EFFORT})
        _log_cost(ctx, step, resp)
        out = _tool_input(resp, tool["name"])
        if out is not None:
            return clean(out)
        msgs = [{"role": "user", "content": prompt + f"\n\nAnswer only by calling the {tool['name']} tool."}]
    raise RuntimeError(f"{step}: the model did not return structured output")


def research(ctx, step, system, prompt, tool: dict, *, max_searches=None, effort=None, max_tokens=16000):
    """Web-search research that ends in one structured tool call.

    Returns (tool_input or None, seen) where seen maps url -> {title, page_age}: only URLs the
    search actually returned may be used as sources.
    """
    max_searches = max_searches or config.SEARCHES_PER_AREA
    search_tool = {"type": config.WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}
    resp = _create(model=config.MODEL, max_tokens=max_tokens, system=system,
                   tools=[search_tool, tool], tool_choice={"type": "auto"},
                   messages=[{"role": "user", "content": prompt}],
                   output_config={"effort": effort or config.RESEARCH_EFFORT})
    _log_cost(ctx, step, resp)
    seen = {}
    cited = []
    for b in resp.content:
        if b.type == "web_search_tool_result":
            content = b.content
            if isinstance(content, list):
                for r in content:
                    url = getattr(r, "url", None)
                    if url:
                        seen[url] = {"title": getattr(r, "title", "") or "", "page_age": getattr(r, "page_age", None)}
        elif b.type == "text" and getattr(b, "citations", None):
            for c in b.citations:
                url = getattr(c, "url", None)
                if url:
                    seen.setdefault(url, {"title": getattr(c, "title", "") or "", "page_age": None})
                    cited.append({"url": url, "text": getattr(c, "cited_text", "")})
    out = _tool_input(resp, tool["name"])
    if out is None:
        # The model wrote prose (or paused). Turn what it found into the structured record
        # without searching again; the search results themselves cannot be sent back.
        notes = _text(resp)
        listing = "\n".join(f"- {u} | {m['title']} | published: {m['page_age'] or 'unknown'}" for u, m in seen.items())
        quotes = "\n".join(f"- {c['url']}: \"{c['text'][:400]}\"" for c in cited[:40])
        follow = (prompt + "\n\nThe web research has already been done. Here is what it produced.\n"
                  f"Research notes:\n{notes or '(none)'}\n\nQuoted passages:\n{quotes or '(none)'}\n\n"
                  f"Search results seen:\n{listing or '(none)'}\n\nRecord only what these support, "
                  f"using only these URLs as sources, by calling {tool['name']}.")
        try:
            out = structured(ctx, step + ":record", system, follow, tool, effort="low")
        except RuntimeError:
            out = None
    return (clean(out) if out is not None else None), seen
