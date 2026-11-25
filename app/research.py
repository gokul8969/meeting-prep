"""The shared research engine (requirements section 4).

Order: 1 identify (rep confirms) -> source list for the industry and countries -> areas 2-7 and
attendees in parallel -> merge into the profile (Find new labels) -> synthesis (opportunity view,
questions, summary, alerts).
"""
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from . import config, db, knowledge, llm, rules
from .apollo import Apollo, org_summary, person_summary

log = logging.getLogger("mp.research")

# ---------------------------------------------------------------- prompts

def research_system() -> str:
    return f"""You are the research engine behind a meeting prep tool for amasol and klaricore sales reps.
amasol is an observability consultancy positioned as the customer's trusted advisor, tool-agnostic.
klaricore is amasol's IT reliability product: contract-first SLA and service level governance across many providers.
Today is {date.today().isoformat()}.

Rules you must follow:
- Every finding needs at least one source URL that you actually saw in your web search results, with its publication date if known (YYYY-MM-DD, or YYYY-MM, or YYYY). Never invent a URL or a date.
- Never fill a gap with a guess. If you find nothing credible on something that matters, report it as a gap.
- Type "Stated" means a source says it. Type "Inferred" means you worked it out from signals; then give the reasoning, for example "Inferred from 4 job postings, not confirmed by the company."
- Keep findings about the parent group and about the subsidiary apart, and say which entity each finding is about.
- Use the company's own words for its priorities where possible.
- Source tiers: 1 = the company itself (filings, annual reports, investor material, press releases, website) and regulators or public registers; 2 = reputable press, analysts, trade press for that industry; 3 = job postings, professional profiles, Apollo and other data providers; 4 = blogs, forums, social posts (only to back up other sources, never alone).
- People: describe a person only as far as a cited source says. LinkedIn information may be used only as it appears in search results (profile link, role, employer, headline). Never log in to or scrape LinkedIn.
- Global by default: do not assume one country, region, regulator or industry.
- Never mention "Detectability" in any form.
- Write plain business English. One or two sentences per finding. Never use em dashes.
- "so_what" says briefly why it matters for amasol, klaricore or both. Lead with the customer's situation, not a product pitch.
"""


AREAS = {
    "business": {
        "section": 3, "label": "Business situation",
        "what": "Strategy, stated priorities, recent results and financial position, cost or growth programmes, transformation programmes, M&A.",
        "where": "Annual and quarterly reports, investor presentations, earnings call transcripts, CEO letters, press releases.",
        "signals": "Quote the company's own words for its priorities so the brief can echo them. A plan or target is a plan, never a result.",
    },
    "it": {
        "section": 4, "label": "IT environment and tech stack",
        "what": "Tools and platforms in use, cloud providers, monitoring and observability tools, ITSM tools, data platforms, IT team shape and hiring, a first view of maturity.",
        "where": "Job postings (the company's careers page and job boards relevant to each country), current staff's public profiles and listed skills, vendor customer stories and press releases, conference talks, engineering blogs, public code repositories.",
        "signals": "A tool named in several postings points to the stack (count the postings). Many SRE or observability hires point to a capability being built. Several monitoring tools at once suggest tool sprawl. Phrases such as 'SLA reporting in Excel', 'vendor governance' or 'service level manager' in postings are klaricore signals.",
    },
    "sourcing": {
        "section": 5, "label": "Sourcing model and providers",
        "what": "Outsourcing and managed-service partners, multi-vendor setup, who plays the service-integrator (SIAM) role, contracts and tenders.",
        "where": "Outsourcing and partner press releases, public tender portals (for example TED in the EU, Contracts Finder in the UK, GeM in India, and the equivalents for this company's countries), annual reports.",
        "signals": "Several providers, or a recent merger, point to a multi-vendor or consolidation problem: a klaricore angle.",
    },
    "triggers": {
        "section": 9, "label": "Trigger events",
        "what": "Leadership changes (CIO, CTO, head of IT operations), outages and incidents, M&A, cloud or platform migrations, vendor changes, fines, major projects. Each finding is one dated event.",
        "where": "News, company and regulator announcements, public status pages, outage trackers, and the account's own source list below.",
        "signals": "A new CIO or CTO (first 100 days), a major outage, a merger or a vendor switch are high-priority signals.",
        "uses_source_list": True,
    },
    "regulation": {
        "section": 6, "label": "Industry and regulation",
        "what": "The account's industry and countries, the regulations that apply to it, typical industry pains.",
        "where": "Regulators, trade press, industry bodies and analysts for that industry and those countries (the account's source list below).",
        "signals": "Link regulation to need. Example: rules on overseeing third-party ICT providers (such as DORA for EU financial firms) are a direct klaricore angle.",
        "uses_source_list": True,
    },
}

FINDING_ITEM = {
    "type": "object",
    "properties": {
        "statement": {"type": "string", "description": "The fact, in one or two sentences."},
        "type": {"type": "string", "enum": ["Stated", "Inferred"]},
        "sources": {"type": "array", "items": {"type": "object", "properties": {
            "url": {"type": "string"}, "publisher": {"type": "string"},
            "tier": {"type": "integer", "enum": [1, 2, 3, 4]},
            "published": {"type": "string", "description": "YYYY-MM-DD, YYYY-MM, YYYY or empty if unknown"}},
            "required": ["url", "publisher", "tier", "published"]}},
        "reasoning": {"type": "string", "description": "For Inferred: which signals led to it. Else empty."},
        "so_what": {"type": "string"},
        "product": {"type": "string", "enum": ["amasol", "klaricore", "both", "none"]},
        "entity": {"type": "string", "description": "Which legal entity this is about (subsidiary or parent)."},
        "topic": {"type": "string", "description": "Short key for what this is about, e.g. 'CIO', 'monitoring tools', 'outsourcing partner'."},
        "relation": {"type": "string", "description": "'new', or 'same_as:<id>', 'updates:<id>', 'conflicts:<id>' against an existing finding id listed in the prompt."},
    },
    "required": ["statement", "type", "sources", "reasoning", "so_what", "product", "entity", "topic", "relation"],
}

GAP_ITEM = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "why": {"type": "string"},
        "question": {"type": "string", "description": "The open question to ask the customer."},
        "product": {"type": "string", "enum": ["amasol", "klaricore", "both"]},
    },
    "required": ["topic", "why", "question", "product"],
}

RECORD_TOOL = {
    "name": "record_findings",
    "description": "Record the research findings and gaps for this area. Call it once, at the end.",
    "input_schema": {"type": "object", "properties": {
        "findings": {"type": "array", "items": FINDING_ITEM},
        "still_seen": {"type": "array", "items": {"type": "integer"},
                       "description": "Ids of existing findings that your sources show are still true."},
        "gaps": {"type": "array", "items": GAP_ITEM},
    }, "required": ["findings", "gaps"]},
}

PERSON_POINT = {
    "type": "object",
    "properties": {
        "statement": {"type": "string"},
        "kind": {"type": "string", "enum": ["role", "tenure", "background", "tools", "themes", "activity", "priorities"]},
        "type": {"type": "string", "enum": ["Stated", "Inferred"]},
        "sources": FINDING_ITEM["properties"]["sources"],
        "reasoning": {"type": "string"},
    },
    "required": ["statement", "kind", "type", "sources", "reasoning"],
}

PEOPLE_TOOL = {
    "name": "record_people",
    "description": "Record the people found. Call it once, at the end.",
    "input_schema": {"type": "object", "properties": {
        "people": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "title": {"type": "string"},
            "linkedin_url": {"type": "string", "description": "Only if seen in search results or given by the rep; else empty."},
            "found": {"type": "boolean", "description": "False if nothing credible was found."},
            "relevance": {"type": "string", "description": "Why this person likely matters for amasol and/or klaricore."},
            "points": {"type": "array", "items": PERSON_POINT},
            "talking_points": {"type": "array", "items": {"type": "string"},
                               "description": "One or two suggested talking points or questions for this person."},
            "existing_person_id": {"type": "integer", "description": "Id if this is a person already listed in the prompt, else 0."},
        }, "required": ["name", "title", "linkedin_url", "found", "relevance", "points", "talking_points", "existing_person_id"]}},
    }, "required": ["people"]},
}

IDENTIFY_TOOL = {
    "name": "record_candidates",
    "description": "Record the companies that match what the rep typed.",
    "input_schema": {"type": "object", "properties": {
        "candidates": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "legal_name": {"type": "string"},
            "website": {"type": "string"}, "parent": {"type": "string"},
            "subsidiaries": {"type": "array", "items": {"type": "string"}},
            "countries": {"type": "array", "items": {"type": "string"}},
            "hq_country": {"type": "string"},
            "industry": {"type": "string"},
            "description": {"type": "string", "description": "One line on what it does."},
            "note": {"type": "string", "description": "How it differs from the other candidates, e.g. 'the energy spin-off, not Siemens AG'."},
            "sources": FINDING_ITEM["properties"]["sources"],
        }, "required": ["name", "legal_name", "website", "parent", "subsidiaries", "countries", "hq_country",
                        "industry", "description", "note", "sources"]}},
    }, "required": ["candidates"]},
}

SOURCE_LIST_TOOL = {
    "name": "record_source_list",
    "description": "Record the account's industries, main countries and the credible sources to use for it.",
    "input_schema": {"type": "object", "properties": {
        "industries": {"type": "array", "items": {"type": "string"}},
        "countries": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "url": {"type": "string"},
            "kind": {"type": "string", "enum": ["regulator", "trade publication", "industry body", "analyst", "register", "tender portal", "other"]},
            "country": {"type": "string"}, "why": {"type": "string"}},
            "required": ["name", "url", "kind", "country", "why"]}},
    }, "required": ["industries", "countries", "sources"]},
}

# ---------------------------------------------------------------- progress

class Progress:
    """Steps live in the run row; every change reloads them so several writers never clobber each other."""

    def __init__(self, run_id):
        self.run_id = run_id

    def _edit(self, fn):
        with db._lock:
            run = db.get("runs", self.run_id)
            steps = db.jload(run["steps_json"], []) if run else []
            fn(steps)
            db.update("runs", self.run_id, {"steps_json": db.jdump(steps)})

    def add(self, key, label):
        def f(steps):
            if not any(s["key"] == key for s in steps):
                steps.append({"key": key, "label": label, "status": "pending", "detail": ""})
        self._edit(f)

    def set(self, key, status, detail=""):
        def f(steps):
            for s in steps:
                if s["key"] == key:
                    s["status"] = status
                    if detail:
                        s["detail"] = detail
        self._edit(f)

    def message(self, text):
        db.update("runs", self.run_id, {"message": text})


def new_run(kind, account_id=None, prep_id=None, user=None):
    return db.insert("runs", {"account_id": account_id, "prep_id": prep_id, "kind": kind, "status": "running",
                              "steps_json": "[]", "started_at": db.now(), "created_by": user})

# ---------------------------------------------------------------- identification

def identify(company: str, hint: str = "", ctx=None) -> list[dict]:
    """Research area 1. Returns candidate companies for the rep to confirm."""
    ap = Apollo()
    prompt = f"""The rep typed this company name: "{company}".
{('Extra context from the rep: ' + hint) if hint else ''}
Find the exact company the rep most likely means, plus other real companies that could also match
(for example a parent group versus its listed subsidiary, or same-name companies in other countries).
Use the company website and business registers (for example Companies House, Handelsregister, MCA or the register for its country).
Return up to 4 candidates, best match first. Use only facts you found; leave a field empty if unknown."""
    out, seen = llm.research(ctx, "identify", research_system(), prompt, IDENTIFY_TOOL, max_searches=4)
    cands = (out or {}).get("candidates", [])
    for c in cands:
        c["sources"] = _valid_sources(c.get("sources", []), seen, set())
        c["domain"] = rules.domain_of(c.get("website", ""))
        if ap.available and c["domain"]:
            org = org_summary(ap.organization(c["domain"]))
            if org:
                c["apollo"] = org
    return cands


def norm_url(u: str) -> str:
    u = (u or "").strip().rstrip("/").lower()
    for p in ("https://", "http://"):
        if u.startswith(p):
            u = u[len(p):]
    if u.startswith("www."):
        u = u[4:]
    return u.split("#")[0]


def _valid_sources(sources, seen: dict, company_domains: set, allowed_extra=()):
    """Keep only sources whose URL the search really returned (or the rep/Apollo provided)."""
    seen_norm = {norm_url(u): (u, m) for u, m in seen.items()}
    extra = {norm_url(u) for u in allowed_extra if u}
    out = []
    for s in sources or []:
        url = (s.get("url") or "").strip()
        n = norm_url(url)
        meta = None
        if n in seen_norm:
            url, meta = seen_norm[n]
        elif n.split("?")[0] in {k.split("?")[0] for k in seen_norm}:
            meta = {}
        elif n in extra or "apollo.io" in n:
            meta = {}
        else:
            continue
        published = s.get("published") or (meta or {}).get("page_age") or ""
        d = rules.parse_date(published)
        out.append({"url": url, "publisher": s.get("publisher") or rules.host(url),
                    "tier": rules.tier_for(url, s.get("tier"), company_domains),
                    "published": d.isoformat() if d else ""})
    # de-duplicate by URL
    uniq = {}
    for s in out:
        uniq.setdefault(norm_url(s["url"]), s)
    return list(uniq.values())


def find_or_create_account(cand: dict, user=None) -> int:
    domain = cand.get("domain") or rules.domain_of(cand.get("website", ""))
    if domain:
        existing = db.q1("SELECT id FROM accounts WHERE domain=?", (domain,))
        if existing:
            return existing["id"]
    name = cand.get("name") or cand.get("legal_name")
    existing = db.q1("SELECT id FROM accounts WHERE lower(name)=lower(?) OR lower(legal_name)=lower(?)",
                     (name, cand.get("legal_name") or name))
    if existing and not domain:
        return existing["id"]
    aid = db.insert("accounts", {
        "name": name, "legal_name": cand.get("legal_name"), "website": cand.get("website"),
        "domain": domain or None, "parent": cand.get("parent"),
        "subsidiaries": ", ".join(cand.get("subsidiaries") or []),
        "countries": ", ".join(cand.get("countries") or []), "industry": cand.get("industry"),
        "size": str((cand.get("apollo") or {}).get("employees") or ""),
        "identity_confirmed": 1, "created_by": user, "created_at": db.now(), "updated_at": db.now()})
    # identification facts become section 2 findings
    srcs = cand.get("sources") or []
    company_domains = {domain} if domain else set()
    for s in srcs:
        s["tier"] = rules.tier_for(s["url"], s.get("tier"), company_domains)
    facts = []
    if cand.get("legal_name"):
        facts.append(("legal entity", f"Legal entity: {cand['legal_name']}."))
    if cand.get("parent"):
        facts.append(("parent", f"Parent company: {cand['parent']}."))
    if cand.get("subsidiaries"):
        facts.append(("subsidiaries", "Subsidiaries include: " + ", ".join(cand["subsidiaries"][:12]) + "."))
    if cand.get("countries"):
        facts.append(("countries", "Operates in: " + ", ".join(cand["countries"][:20]) + "."))
    if cand.get("description"):
        facts.append(("what it does", cand["description"]))
    for topic, st in facts:
        if srcs:
            add_finding(aid, {"section": 2, "statement": st, "type": "Stated", "sources": srcs, "topic": topic,
                              "entity": cand.get("legal_name") or name, "origin": "Web research", "product": "none"})
    if cand.get("apollo"):
        _apollo_org_findings(aid, cand["apollo"], None)
    db.timeline(aid, "account", f"Account created and identity confirmed: {cand.get('legal_name') or name}")
    return aid


def _apollo_source(org_id=None):
    url = f"https://app.apollo.io/#/organizations/{org_id}" if org_id else "https://app.apollo.io"
    return [{"url": url, "publisher": "Apollo", "tier": 3, "published": db.today()}]


def _apollo_org_findings(aid, org, run_id):
    src = _apollo_source(org.get("id"))
    bits = []
    if org.get("employees"):
        bits.append(f"about {org['employees']:,} employees" if isinstance(org["employees"], int) else f"{org['employees']} employees")
    if org.get("industry"):
        bits.append(f"industry: {org['industry']}")
    if org.get("city") or org.get("country"):
        bits.append("based in " + ", ".join(x for x in [org.get("city"), org.get("country")] if x))
    if bits:
        merge_one(aid, {"section": 2, "statement": "Apollo company data: " + "; ".join(bits) + ".", "type": "Stated",
                        "sources": src, "topic": "company size (Apollo)", "origin": "Apollo", "product": "none",
                        "relation": "auto"}, run_id)
    if org.get("technologies"):
        techs = org["technologies"][:40]
        merge_one(aid, {"section": 4, "statement": "Apollo technology data lists: " + ", ".join(techs) + ".",
                        "type": "Stated", "sources": src, "topic": "technologies (Apollo)", "origin": "Apollo",
                        "so_what": "A starting point for the tech stack; check which tools are central.",
                        "product": "both", "relation": "auto"}, run_id)

# ---------------------------------------------------------------- storing findings

def add_finding(aid, f: dict, run_id=None, status="New", user=None) -> int | None:
    st = rules.clean_text(f.get("statement", ""))
    if not st or rules.banned(st) or rules.banned(f.get("so_what", "")):
        return None
    sources = f.get("sources") or []
    is_people = f.get("section") == 7
    apollo_used = f.get("_apollo_used", True)
    conf = f.get("confidence") or rules.confidence(f.get("type", "Stated"), sources, is_people=is_people,
                                                   apollo_used=apollo_used)
    fid = db.insert("findings", {
        "account_id": aid, "section": f["section"], "statement": st, "type": f.get("type", "Stated"),
        "sources_json": db.jdump(sources), "date_published": f.get("date_published") or rules.newest_date(sources),
        "date_found": db.today(), "last_seen": db.today(), "confidence": conf,
        "reasoning": f.get("reasoning") or "", "so_what": f.get("so_what") or "", "product": f.get("product") or "none",
        "entity": f.get("entity") or "", "person_id": f.get("person_id"), "linked_question": f.get("linked_question"),
        "status": status, "origin": f.get("origin") or "Web research", "topic": f.get("topic") or "",
        "supersedes_id": f.get("supersedes_id"), "conflicts_with_id": f.get("conflicts_with_id"),
        "run_id": run_id, "created_by": user, "created_at": db.now()})
    db.finding_event(fid, aid, "created", f"{status}; origin {f.get('origin') or 'Web research'}", user)
    return fid


def _rep_backed(f) -> bool:
    return f["type"] == "Rep input" or f.get("rep_verdict") in ("Confirmed", "Corrected")


def merge_one(aid, f: dict, run_id=None) -> tuple[str, int | None]:
    """Apply the Find new labels (section 6.4). Returns (label, finding id)."""
    rel = (f.get("relation") or "new").strip()
    kind, _, ref = rel.partition(":")
    target = None
    if ref.strip().isdigit():
        target = db.q1("SELECT * FROM findings WHERE id=? AND account_id=?", (int(ref), aid))
    if kind == "auto":
        # deterministic match for system-made findings (Apollo): same section and topic
        target = db.q1("SELECT * FROM findings WHERE account_id=? AND section=? AND topic=? AND status<>'Superseded' "
                       "ORDER BY id DESC", (aid, f["section"], f.get("topic")))
        if target and target["statement"] == rules.clean_text(f["statement"]):
            kind = "same_as"
        elif target:
            kind = "updates"
        else:
            kind = "new"
    if target and target["status"] == "Superseded":
        target = None
    if kind == "same_as" and target:
        merged = db.jload(target["sources_json"], [])
        have = {norm_url(s["url"]) for s in merged}
        merged += [s for s in f.get("sources", []) if norm_url(s["url"]) not in have]
        db.update("findings", target["id"], {"last_seen": db.today(), "sources_json": db.jdump(merged)})
        db.finding_event(target["id"], aid, "seen again", f"run {run_id}")
        return "Already known", target["id"]
    if kind in ("updates", "conflicts") and target:
        if kind == "conflicts" or _rep_backed(target):
            fid = add_finding(aid, {**f, "conflicts_with_id": target["id"]}, run_id, status="Conflicting")
            if fid:
                db.update("findings", target["id"], {"conflicts_with_id": fid,
                                                     "status": target["status"] if _rep_backed(target) else "Conflicting"})
                db.finding_event(target["id"], aid, "conflict", f"new finding {fid} disagrees")
            return "Conflicting", fid
        fid = add_finding(aid, {**f, "supersedes_id": target["id"]}, run_id, status="Updated")
        if fid:
            db.update("findings", target["id"], {"status": "Superseded", "superseded_by_id": fid})
            db.finding_event(target["id"], aid, "superseded", f"by finding {fid}")
        return "Updated", fid
    fid = add_finding(aid, f, run_id, status="New")
    return "New", fid


def current_findings(aid, sections=None, include_superseded=False):
    sql = "SELECT * FROM findings WHERE account_id=?"
    args = [aid]
    if not include_superseded:
        sql += " AND status<>'Superseded' AND COALESCE(rep_verdict,'')<>'Rejected'"
    if sections:
        sql += f" AND section IN ({','.join('?' for _ in sections)})"
        args += list(sections)
    return db.q(sql + " ORDER BY section, id", args)


def findings_digest(fs, with_sources=False, limit_chars=60000):
    lines = []
    for f in fs:
        tag = f"#{f['id']} [s{f['section']}] [{f['type']}, {f['confidence']}, {f['status']}"
        if f.get("rep_verdict"):
            tag += f", rep {f['rep_verdict']}"
        tag += f", published {f.get('date_published') or 'unknown'}]"
        line = f"{tag} {f['statement']}"
        if f.get("rep_correction"):
            line += f" (Rep correction: {f['rep_correction']})"
        if f.get("entity"):
            line += f" (entity: {f['entity']})"
        if f.get("reasoning"):
            line += f" (reasoning: {f['reasoning']})"
        if with_sources:
            srcs = db.jload(f["sources_json"], [])
            line += " sources: " + "; ".join(f"{s['publisher']} t{s['tier']}" for s in srcs[:3])
        lines.append(line)
    text = "\n".join(lines)
    return text[:limit_chars]


def _effective_statement(f):
    return f["rep_correction"] if f.get("rep_verdict") == "Corrected" and f.get("rep_correction") else f["statement"]


def store_gaps(aid, gaps, prep_id=None):
    existing = {r["text"].strip().lower() for r in db.q("SELECT text FROM questions WHERE account_id=?", (aid,))}
    for g in gaps or []:
        qtext = rules.clean_text(g.get("question", ""))
        if not qtext or qtext.lower() in existing or rules.banned(qtext):
            continue
        existing.add(qtext.lower())
        db.insert("questions", {"account_id": aid, "text": qtext, "kind": "gap", "product": g.get("product") or "both",
                                "theme": g.get("topic"), "prep_id": prep_id, "status": "open", "created_at": db.now()})

# ---------------------------------------------------------------- the run

def _company_block(acc):
    return (f"Company: {acc['name']} (legal entity: {acc.get('legal_name') or 'unknown'}; website: {acc.get('website') or 'unknown'}; "
            f"parent: {acc.get('parent') or 'none known'}; countries: {acc.get('countries') or 'unknown'}; "
            f"industry: {acc.get('industry') or 'unknown'}).")


def _existing_block(aid, section, mode, last_at):
    fs = current_findings(aid, [section])
    if not fs:
        return "There are no existing findings for this area yet. Set relation to 'new' for everything."
    head = ("Existing findings we already hold for this area are listed below with their ids. "
            f"The last research ran on {last_at or 'an earlier date'}. Look only for what is NEW or CHANGED since then. "
            "For each finding you record, set relation: 'new' if we did not have it, 'updates:<id>' if it is a newer fact that "
            "replaces an existing one (for example a new CIO), 'conflicts:<id>' if a source disagrees with an existing one, "
            "or 'same_as:<id>' if it matches one (then only record it if you have a source confirming it is still true). "
            "Put ids of existing findings that your sources show are still true in still_seen.\n"
            if mode == "find_new" else
            "Existing findings for this area are listed below. Do not repeat them; set relation as described if you find something related.\n")
    return head + findings_digest(fs)


def run_research(run_id: int, account_id: int, *, mode="full", products=("amasol", "klaricore"),
                 attendees=(), rep_links=(), prep_id=None):
    """Runs in a background thread. mode: 'full' (first research) or 'find_new'."""
    prog = Progress(run_id)
    ctx = llm.Ctx(run_id, account_id)
    acc = db.get("accounts", account_id)
    ap = Apollo()
    last_at = acc.get("last_research_at")
    if mode == "full" and last_at:
        mode = "find_new"
    db.update("runs", run_id, {"kind": db.get("runs", run_id)["kind"] or mode})
    company_domains = {acc["domain"]} if acc.get("domain") else set()
    system = research_system()
    company = _company_block(acc)
    focus = ", ".join(products) if products else "amasol and klaricore"
    labels_count = {"New": 0, "Updated": 0, "Conflicting": 0, "Already known": 0}
    failures = []

    prog.add("identity", "Company identified and confirmed")
    prog.set("identity", "done", acc.get("legal_name") or acc["name"])
    prog.add("apollo", "Apollo company, technology and people data")
    prog.add("sources", "Industry, countries and source list")
    for k, a in AREAS.items():
        prog.add(k, a["label"])
    prog.add("committee", "Buying committee")
    for i, at in enumerate(attendees):
        prog.add(f"att{i}", f"Attendee: {at.get('name')}")
    prog.add("merge", "Merge into the account profile")
    prog.add("synthesis", "Opportunity view, questions and summary")

    # ---- Apollo
    apollo_ctx = {"org": {}, "jobs": [], "people": [], "news": [], "attendees": {}}
    if ap.available:
        prog.set("apollo", "running")
        try:
            org = org_summary(ap.organization(acc.get("domain")))
            apollo_ctx["org"] = org
            if org:
                _apollo_org_findings(account_id, org, run_id)
                apollo_ctx["jobs"] = [j.get("title") for j in ap.job_postings(org.get("id")) if j.get("title")][:50]
                apollo_ctx["news"] = [{"title": n.get("title"), "url": n.get("url"), "date": n.get("published_at")}
                                      for n in ap.news(org.get("id"))][:20]
            apollo_ctx["people"] = [person_summary(p) for p in ap.people_search(
                acc.get("domain"), departments=["master_information_technology", "master_operations"],
                seniorities=["c_suite", "vp", "head", "director"])][:25]
            for at in attendees:
                m = ap.person_match(at["name"], domain=acc.get("domain"), org_name=acc["name"],
                                    linkedin_url=at.get("link") or None)
                if m:
                    apollo_ctx["attendees"][at["name"]] = person_summary(m)
        except Exception as e:  # never let Apollo stop the research
            ap.notes.append(f"error: {e}")
        if ap.used:
            prog.set("apollo", "done", "; ".join(ap.notes[:3]) if ap.notes else "")
        else:
            prog.set("apollo", "skipped", "Apollo returned nothing: " + "; ".join(ap.notes[:3]))
    else:
        prog.set("apollo", "skipped", "Apollo not configured; research continues with public sources")
    apollo_used = ap.used
    db.update("runs", run_id, {"used_apollo": 1 if apollo_used else 0,
                               "apollo_note": "; ".join(ap.notes) if ap.notes else ("" if apollo_used else "Apollo was not used")})

    def area_job(key):
        a = AREAS[key]
        prog.set(key, "running")
        extra = ""
        if a.get("uses_source_list"):
            sl = db.jload(db.get("accounts", account_id).get("source_list_json"), {}) or {}
            if sl.get("sources"):
                extra += "\nThe account's source list (use it alongside general sources):\n" + "\n".join(
                    f"- {s['name']} ({s['kind']}, {s.get('country')}): {s['url']}" for s in sl["sources"][:25])
        if key == "it" and (apollo_ctx["org"].get("technologies") or apollo_ctx["jobs"]):
            extra += ("\nApollo data (tier 3, cite as https://app.apollo.io): technologies: "
                      + ", ".join(apollo_ctx["org"].get("technologies", [])[:40])
                      + "; open job titles: " + "; ".join(apollo_ctx["jobs"][:40]))
        if key == "triggers" and apollo_ctx["news"]:
            extra += "\nApollo news signals: " + "; ".join(f"{n['title']} ({n['date']}) {n['url']}" for n in apollo_ctx["news"])
        prompt = f"""{company}
Products in focus: {focus}.
Research area: {a['label']}.
What to find: {a['what']}
Where to look: {a['where']}
Signals and correlations: {a['signals']}{extra}

{_existing_block(account_id, a['section'], mode, last_at)}

Search the web, then call record_findings once. Include gaps: things that matter for amasol or klaricore that you could not find."""
        out, seen = llm.research(ctx, key, system, prompt, RECORD_TOOL)
        return key, a["section"], out, seen

    def sources_job():
        prog.set("sources", "running")
        prompt = f"""{company}
Work out this account's industry (or industries) and its main countries. Then build its source list: the relevant
regulators, trade publications, industry bodies, analysts, business registers and public tender portals for that industry
and those countries. Only list real organisations with their real website. Then call record_source_list."""
        out, seen = llm.research(ctx, "source_list", system, prompt, SOURCE_LIST_TOOL, max_searches=3)
        return out

    def committee_job():
        prog.set("committee", "running")
        existing = db.q("SELECT id,name,title,rep_status FROM people WHERE account_id=?", (account_id,))
        ex_txt = "\n".join(f"- id {p['id']}: {p['name']}, {p['title']} ({p['rep_status']})" for p in existing) or "(none)"
        ap_txt = "\n".join(f"- {p['name']}, {p['title']}, {p.get('linkedin_url') or ''} (Apollo)" for p in apollo_ctx["people"]) or "(Apollo not used)"
        prompt = f"""{company}
Products in focus: {focus}.
Find the wider buying committee: the people likely to matter for observability (amasol) and SLA, service level and
provider governance (klaricore). Typical roles: CIO, CTO, CDO, head of IT operations or infrastructure, head of service
management, head of vendor or sourcing management, SRE or platform leads, IT procurement.
Look at Apollo results, LinkedIn profiles found through web search, the company's leadership pages, press, interviews and
conference speaker lists. For each person: role, how long in the role, background (previous employers, tools used),
topics they speak or post on, each as a sourced point. Up to 8 people, most relevant first.
People we already hold (match them with existing_person_id; do not repeat what we know unless it changed):
{ex_txt}
Apollo people search results:
{ap_txt}
Search, then call record_people once."""
        out, seen = llm.research(ctx, "committee", system, prompt, PEOPLE_TOOL, max_searches=5)
        return out, seen

    def attendee_job(i, at):
        prog.set(f"att{i}", "running")
        apm = apollo_ctx["attendees"].get(at["name"])
        prompt = f"""{company}
Products in focus: {focus}.
Build a short profile of this meeting attendee: {at['name']}, {at.get('title') or 'title not given'}{', profile link given by the rep: ' + at['link'] if at.get('link') else ''}.
Cover: role and how long they have been in it; background (previous employers and tools or platforms they worked with);
recent posts, talks or interviews and the themes they care about; one or two suggested talking points or questions.
{('Apollo data (cite as https://app.apollo.io): ' + str(apm)) if apm else 'Apollo data: not available.'}
Make sure it is the right person at this company. If nothing credible is found, set found=false and give no points.
Search, then call record_people once with exactly this one person."""
        out, seen = llm.research(ctx, f"attendee:{at['name']}", system, prompt, PEOPLE_TOOL, max_searches=4)
        return out, seen

    def store_people(out, seen, attendee=None):
        stored = []
        for p in (out or {}).get("people", []):
            name = rules.clean_text(p.get("name") or (attendee or {}).get("name") or "")
            if not name:
                continue
            pid = None
            if p.get("existing_person_id"):
                r = db.q1("SELECT id FROM people WHERE id=? AND account_id=?", (p["existing_person_id"], account_id))
                pid = r["id"] if r else None
            if not pid:
                r = db.q1("SELECT id FROM people WHERE account_id=? AND lower(name)=lower(?)", (account_id, name))
                pid = r["id"] if r else None
            link = p.get("linkedin_url") or ""
            allowed = [link] if link and norm_url(link) in {norm_url(u) for u in seen} else []
            if attendee and attendee.get("link"):
                allowed.append(attendee["link"])
            if link and not allowed:
                link = ""
            pts = []
            for pt in p.get("points", []):
                srcs = _valid_sources(pt.get("sources"), seen, company_domains, allowed_extra=allowed)
                if srcs:
                    pts.append((pt, srcs))
            if not pid:
                existing_person = None
                pid = db.insert("people", {
                    "account_id": account_id, "name": name, "title": p.get("title") or (attendee or {}).get("title"),
                    "linkedin_url": link or (attendee or {}).get("link") or "", "background": "",
                    "priorities": p.get("relevance") or "", "origin": "Apollo" if apollo_used and name in apollo_ctx["attendees"] else "Web research",
                    "rep_status": "rep added" if attendee else "proposed",
                    "created_at": db.now(), "updated_at": db.now()})
            else:
                existing_person = db.get("people", pid)
                upd = {"updated_at": db.now()}
                if link and not existing_person.get("linkedin_url"):
                    upd["linkedin_url"] = link
                if existing_person["rep_status"] not in ("corrected", "rep added", "confirmed") and p.get("title"):
                    upd["title"] = p["title"]  # rep changes are never overwritten
                db.update("people", pid, upd)
            for pt, srcs in pts:
                f = {"section": 7, "statement": pt["statement"], "type": pt.get("type", "Stated"), "sources": srcs,
                     "reasoning": pt.get("reasoning"), "topic": f"{name}: {pt.get('kind')}", "person_id": pid,
                     "entity": acc["name"], "product": "both", "_apollo_used": apollo_used,
                     "origin": "Apollo" if all("apollo.io" in s["url"] for s in srcs) else "Web research",
                     "relation": "new"}
                same = db.q1("SELECT * FROM findings WHERE account_id=? AND person_id=? AND topic=? AND status<>'Superseded' ORDER BY id DESC",
                             (account_id, pid, f["topic"]))
                if same:
                    f["relation"] = ("same_as:" if rules.clean_text(pt["statement"]).lower() == same["statement"].lower() else "updates:") + str(same["id"])
                label, _ = merge_one(account_id, f, run_id)
                labels_count[label] = labels_count.get(label, 0) + 1
            for tp in p.get("talking_points", [])[:2]:
                tp = rules.clean_text(tp)
                if tp and not rules.banned(tp) and not db.q1("SELECT id FROM questions WHERE account_id=? AND lower(text)=lower(?)", (account_id, tp)):
                    db.insert("questions", {"account_id": account_id, "text": tp, "kind": "talking point",
                                            "product": "both", "person_id": pid, "person_name": name,
                                            "prep_id": prep_id, "status": "open", "created_at": db.now()})
            stored.append({"name": name, "found": bool(p.get("found") and pts), "points": len(pts)})
        return stored

    def apply_area(key, section, out, seen):
        if out is None:
            raise RuntimeError("no structured result")
        n_drop = 0
        for f in out.get("findings", []):
            srcs = _valid_sources(f.get("sources"), seen, company_domains, allowed_extra=rep_links)
            if not srcs:
                n_drop += 1
                continue
            label, _ = merge_one(account_id, {**f, "section": section, "sources": srcs, "origin":
                                              "Apollo" if all("apollo.io" in s["url"] for s in srcs) else "Web research"}, run_id)
            labels_count[label] = labels_count.get(label, 0) + 1
        for fid in out.get("still_seen", []) or []:
            if db.q1("SELECT id FROM findings WHERE id=? AND account_id=?", (fid, account_id)):
                db.update("findings", fid, {"last_seen": db.today()})
        store_gaps(account_id, out.get("gaps"), prep_id)
        return len(out.get("findings", [])) - n_drop, n_drop

    with ThreadPoolExecutor(max_workers=config.PARALLEL_CALLS) as pool:
        futs = {}
        futs[pool.submit(sources_job)] = ("sources",)
        for k in ("business", "it", "sourcing"):
            futs[pool.submit(area_job, k)] = ("area", k)
        futs[pool.submit(committee_job)] = ("committee",)
        for i, at in enumerate(attendees):
            futs[pool.submit(attendee_job, i, at)] = ("att", i, at)
        pending = set(futs)
        while pending:
            for fut in as_completed(list(pending)):
                pending.discard(fut)
                tag = futs[fut]
                try:
                    res = fut.result()
                    if tag[0] == "sources":
                        out = res or {}
                        if out:
                            upd = {"source_list_json": db.jdump(out), "updated_at": db.now()}
                            if out.get("industries"):
                                upd["industry"] = ", ".join(out["industries"])
                            if out.get("countries") and not acc.get("countries"):
                                upd["countries"] = ", ".join(out["countries"])
                            db.update("accounts", account_id, upd)
                        prog.set("sources", "done", f"{len((out or {}).get('sources', []))} sources")
                        for k in ("triggers", "regulation"):
                            nf = pool.submit(area_job, k)
                            futs[nf] = ("area", k)
                            pending.add(nf)
                    elif tag[0] == "area":
                        key, section, out, seen = res
                        kept, dropped = apply_area(key, section, out, seen)
                        prog.set(key, "done", f"{kept} findings" + (f", {dropped} dropped without a usable source" if dropped else ""))
                    elif tag[0] == "committee":
                        out, seen = res
                        stored = store_people(out, seen)
                        prog.set("committee", "done", f"{len(stored)} people")
                    elif tag[0] == "att":
                        out, seen = res
                        stored = store_people(out, seen, attendee=tag[2])
                        found = any(s["found"] for s in stored)
                        if not stored:
                            store_people({"people": [{"name": tag[2]["name"], "title": tag[2].get("title", ""), "linkedin_url": "",
                                                      "found": False, "relevance": "", "points": [], "talking_points": [],
                                                      "existing_person_id": 0}]}, {}, attendee=tag[2])
                        prog.set(f"att{tag[1]}", "done", "profile built" if found else "nothing credible found; showing only what the rep gave")
                except Exception as e:
                    log.error("research step %s failed: %s", tag, traceback.format_exc())
                    failures.append(f"{tag[0]} {tag[1] if len(tag) > 1 else ''}: {e}")
                    key = {"sources": "sources", "committee": "committee"}.get(tag[0]) or (tag[1] if tag[0] == "area" else f"att{tag[1]}")
                    prog.set(key, "failed", str(e)[:200])
                    if tag[0] == "sources":
                        for k in ("triggers", "regulation"):
                            nf = pool.submit(area_job, k)
                            futs[nf] = ("area", k)
                            pending.add(nf)
                break  # re-enter as_completed with the updated pending set

    prog.set("merge", "done", ", ".join(f"{v} {k.lower()}" for k, v in labels_count.items() if v))
    prog.set("synthesis", "running")
    try:
        synthesize(account_id, run_id, mode=mode, products=products, prep_id=prep_id, apollo_used=apollo_used)
        prog.set("synthesis", "done")
    except Exception as e:
        log.error("synthesis failed: %s", traceback.format_exc())
        failures.append(f"synthesis: {e}")
        prog.set("synthesis", "failed", str(e)[:200])
    db.update("accounts", account_id, {"last_research_at": db.now(), "updated_at": db.now()})
    summary = (f"{'Find new' if mode == 'find_new' else 'Research'}: " +
               ", ".join(f"{v} {k.lower()}" for k, v in labels_count.items() if v)) or "no findings"
    db.timeline(account_id, "research", summary + (f" ({len(failures)} steps failed)" if failures else ""), run_id)
    return {"labels": labels_count, "failures": failures, "apollo_used": apollo_used}

# ---------------------------------------------------------------- synthesis

SYNTH_TOOL = {
    "name": "record_synthesis",
    "description": "Record the opportunity view, questions, account summary and alerts.",
    "input_schema": {"type": "object", "properties": {
        "opportunity": {"type": "array", "items": {"type": "object", "properties": {
            "statement": {"type": "string"},
            "product": {"type": "string", "enum": ["amasol", "klaricore", "both"]},
            "kind": {"type": "string", "enum": ["fit", "maturity guess", "klaricore pain", "next move", "group relationship"]},
            "basis_ids": {"type": "array", "items": {"type": "integer"}, "description": "Finding ids this rests on."},
            "reasoning": {"type": "string"}},
            "required": ["statement", "product", "kind", "basis_ids", "reasoning"]}},
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string"},
            "kind": {"type": "string", "enum": ["check", "gap", "deeper"]},
            "finding_id": {"type": "integer", "description": "The finding it checks or deepens, 0 for a gap."},
            "product": {"type": "string", "enum": ["amasol", "klaricore", "both"]},
            "theme": {"type": "string"},
            "stages": {"type": "array", "items": {"type": "string"}, "description": "Stage keys this fits."}},
            "required": ["text", "kind", "finding_id", "product", "theme", "stages"]}},
        "summary": {"type": "object", "properties": {
            "highlights": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "finding_ids": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "finding_ids"]}},
            "changed": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"}, "finding_ids": {"type": "array", "items": {"type": "integer"}}},
                "required": ["text", "finding_ids"]}}},
            "required": ["highlights", "changed"]},
        "alerts": {"type": "array", "items": {"type": "object", "properties": {
            "finding_id": {"type": "integer"},
            "what": {"type": "string"},
            "why": {"type": "string"},
            "angle": {"type": "string"},
            "priority": {"type": "string", "enum": ["High", "Normal"]},
            "product": {"type": "string", "enum": ["amasol", "klaricore", "both"]}},
            "required": ["finding_id", "what", "why", "angle", "priority", "product"]}},
    }, "required": ["opportunity", "questions", "summary", "alerts"]},
}


def synthesize(aid, run_id, *, mode, products, prep_id=None, apollo_used=True):
    ctx = llm.Ctx(run_id, aid)
    acc = db.get("accounts", aid)
    fs = [f for f in current_findings(aid) if f["section"] != 8]
    changed = [f for f in fs if f["run_id"] == run_id and f["status"] in ("New", "Updated", "Conflicting")]
    open_q = db.q("SELECT id,text,kind FROM questions WHERE account_id=? AND status='open'", (aid,))
    old_opp = current_findings(aid, [8])
    stage_keys = {p: [s["key"] for s in knowledge.stages().get(p, [])] for p in ("amasol", "klaricore")}
    history = db.q("SELECT kind,text,created_at FROM timeline WHERE account_id=? ORDER BY id DESC LIMIT 15", (aid,))
    prompt = f"""{_company_block(acc)}
Lead source and relationship: {acc.get('lead_source') or 'not given'}; {acc.get('relationship') or ''}

PRODUCT KNOWLEDGE (the only source for offerings, case studies and proof points):
{knowledge.products_text(['amasol', 'klaricore'])}

CURRENT FINDINGS (id, section, type, confidence, status):
{findings_digest(fs)}

CHANGED IN THIS RUN: {', '.join('#' + str(f['id']) for f in changed) or 'none'}
Mode: {'Find new (update of an existing profile)' if mode == 'find_new' else 'first full research'}

EXISTING OPPORTUNITY VIEW (replace it):
{findings_digest(old_opp) or '(none)'}

OPEN QUESTIONS ALREADY HELD (do not repeat):
{chr(10).join('- ' + q['text'] for q in open_q) or '(none)'}

RECENT HISTORY:
{chr(10).join(f"- {h['created_at'][:10]} {h['kind']}: {h['text']}" for h in history) or '(none)'}

Do the following, using only the findings above:
1. opportunity: the amasol fit and the klaricore fit for this account; a first guess at observability maturity (level 1 to 4
   from the amasol maturity model, labelled as a guess); for klaricore, which of the four pains likely apply; and the suggested
   next move (for amasol the ORS workshop or the assessment questionnaire; for klaricore an SLA pitch or POC working session).
   If a group company is already a customer, say so. Every item lists the finding ids it rests on. Never claim results we do not have.
2. questions: Medium and Low confidence findings that matter become "check" questions; things we need but have no finding on
   become "gap" questions; a few High findings can lead to a "deeper" question. Plain, open questions a rep can ask a customer.
   Stage keys: amasol {stage_keys['amasol']}, klaricore {stage_keys['klaricore']}. At most 15 new questions.
3. summary: 3 to 5 highlights (the things a rep must know about this account), and what changed in this run
   ({'list the important changes' if mode == 'find_new' else 'leave empty for a first research'}).
4. alerts: {'for changed findings that matter, create an alert. High priority: new CIO, CTO or head of IT operations; major outage or incident; merger or acquisition; new outsourcing or managed-service deal; regulatory action or a new regulation that applies; a large transformation or migration programme. Normal: hiring push in IT operations or SRE, a new initiative, a tech stack change, a new person in the buying committee. Skip trivia.' if mode == 'find_new' else 'leave empty (first research).'}
Write plain business English and never use em dashes. Then call record_synthesis."""
    out = llm.structured(ctx, "synthesis", research_system(), prompt, SYNTH_TOOL, effort=config.WRITE_EFFORT)
    by_id = {f["id"]: f for f in fs}

    # opportunity view: replace previous items (kept in history as superseded)
    for f in old_opp:
        db.update("findings", f["id"], {"status": "Superseded"})
        db.finding_event(f["id"], aid, "superseded", f"opportunity view regenerated in run {run_id}")
    for o in out.get("opportunity", []):
        basis = [by_id[i] for i in o.get("basis_ids", []) if i in by_id]
        srcs = []
        for b in basis:
            srcs += db.jload(b["sources_json"], [])
        uniq = {norm_url(s["url"]): s for s in srcs}
        if not basis:
            continue
        conf = rules.worst([b["confidence"] for b in basis] + ["Medium"])
        add_finding(aid, {"section": 8, "statement": o["statement"], "type": "Inferred", "sources": list(uniq.values()),
                          "reasoning": (o.get("reasoning") or "") + " Based on findings " + ", ".join(f"#{b['id']}" for b in basis) + ".",
                          "product": o.get("product"), "topic": o.get("kind"), "origin": "Web research",
                          "confidence": conf}, run_id)

    existing = {r["text"].strip().lower() for r in db.q("SELECT text FROM questions WHERE account_id=?", (aid,))}
    for qd in out.get("questions", []):
        t = rules.clean_text(qd.get("text", ""))
        if not t or t.lower() in existing or rules.banned(t):
            continue
        existing.add(t.lower())
        fid = qd.get("finding_id") if qd.get("finding_id") in by_id else None
        db.insert("questions", {"account_id": aid, "text": t, "kind": qd.get("kind"), "product": qd.get("product"),
                                "stage": ",".join(qd.get("stages") or []), "theme": qd.get("theme"),
                                "finding_id": fid, "prep_id": prep_id, "status": "open", "created_at": db.now()})
        if fid:
            db.update("findings", fid, {"linked_question": t})

    summ = out.get("summary") or {}
    summ["generated_at"] = db.now()
    summ["apollo_used"] = apollo_used
    db.update("accounts", aid, {"summary_json": db.jdump(summ)})

    if mode == "find_new":
        for a in out.get("alerts", []):
            f = by_id.get(a.get("finding_id"))
            if not f or rules.banned(a.get("what", "")):
                continue
            if db.q1("SELECT id FROM alerts WHERE finding_id=?", (f["id"],)):
                continue
            srcs = db.jload(f["sources_json"], [])
            aid_ = db.insert("alerts", {
                "account_id": aid, "finding_id": f["id"], "run_id": run_id, "what": a["what"], "why": a["why"],
                "angle": a["angle"], "priority": a.get("priority", "Normal"), "product": a.get("product"),
                "source_url": srcs[0]["url"] if srcs else "", "source_name": srcs[0]["publisher"] if srcs else "",
                "event_date": f.get("date_published") or f.get("date_found"), "status": "open", "created_at": db.now()})
            db.timeline(aid, "alert", f"{a.get('priority', 'Normal')} alert: {a['what']}", run_id, link=f"/alerts#a{aid_}")
    return out
