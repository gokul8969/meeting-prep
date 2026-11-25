"""Summary doc and full prep pack (requirements 5.6 to 5.8).

The model writes points that cite finding ids; confidence, sources and labels are always taken from
the findings themselves, never from the model's wording.
"""
from . import config, db, knowledge, llm, rules
from .research import current_findings, findings_digest, research_system, norm_url

PACK_SECTIONS = {
    1: "Summary",
    2: "Account snapshot",
    3: "Business situation",
    4: "IT environment and tech stack",
    5: "Sourcing model and providers",
    6: "Industry and regulation",
    7: "People",
    8: "Our angle",
    9: "Recent signals",
    10: "Relevant history",
    11: "Questions to ask",
    12: "Meeting plan and next step",
    13: "Sources and confidence",
}
WRITTEN = (2, 3, 4, 5, 6, 7, 8, 9, 10)  # sections the model writes as points
# pack section -> profile sections whose findings it draws on
PACK_TO_PROFILE = {2: [2], 3: [3], 4: [4], 5: [5], 6: [6], 7: [7], 8: [8], 9: [9], 10: [10]}

POINT = {"type": "object", "properties": {
    "text": {"type": "string"},
    "finding_ids": {"type": "array", "items": {"type": "integer"}},
    "kind": {"type": "string", "enum": ["fact", "inference", "not found", "knowledge", "guidance"]}},
    "required": ["text", "finding_ids", "kind"]}

BRIEF_TOOL = {
    "name": "write_brief",
    "description": "Write the prep pack sections requested.",
    "input_schema": {"type": "object", "properties": {
        "highlights": {"type": "array", "items": POINT, "description": "3 to 5 things the rep must know before walking in."},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "no": {"type": "integer"},
            "key_point": {"type": "string", "description": "The one key point of this section for the summary doc."},
            "key_point_ids": {"type": "array", "items": {"type": "integer"}},
            "points": {"type": "array", "items": POINT}},
            "required": ["no", "key_point", "key_point_ids", "points"]}},
        "question_groups": {"type": "array", "items": {"type": "object", "properties": {
            "theme": {"type": "string"},
            "attendee": {"type": "string", "description": "Attendee name if the group is for one person, else empty."},
            "questions": {"type": "array", "items": {"type": "object", "properties": {
                "text": {"type": "string"},
                "question_id": {"type": "integer", "description": "Id of a held question, or 0 for a new one."},
                "finding_id": {"type": "integer", "description": "Finding it checks, or 0."},
                "product": {"type": "string", "enum": ["amasol", "klaricore", "both"]}},
                "required": ["text", "question_id", "finding_id", "product"]}}},
            "required": ["theme", "attendee", "questions"]}},
        "plan": {"type": "object", "properties": {
            "flow": {"type": "array", "items": {"type": "string"}, "description": "Suggested flow for the meeting, in steps."},
            "next_step": {"type": "string"},
            "next_step_why": {"type": "string"}},
            "required": ["flow", "next_step", "next_step_why"]},
    }, "required": ["sections"]},
}


def _lead_source_text(ls: dict) -> str:
    if not ls:
        return "not given"
    kind = ls.get("kind", "")
    detail = ls.get("detail", "")
    rule = {
        "Referral": "Open the brief with the referrer's context and relationship.",
        "Partner": "Cover the partner's role and what the partner has already shared.",
        "Existing customer elsewhere in the group": "Use that group customer's history and use cases as internal proof points, and flag that whether it can be named to the prospect must be checked by the rep.",
        "Inbound": "Note what the account engaged with, if the rep said.",
        "Cold outreach": "Put more weight on the 'why now' signals.",
    }.get(kind, "")
    return f"{kind}{': ' + detail if detail else ''}. {rule}"


def _stages_text(prep) -> str:
    stages = db.jload(prep["stages_json"], {}) or {}
    out = []
    for prod, key in stages.items():
        if key == "other":
            out.append(f"{prod}: Other: {prep.get('other_stage_text') or ''}")
        else:
            p = prod if prod in ("amasol", "klaricore") else "amasol"
            out.append(f"{prod}: {knowledge.stage_name(p, key)} (focus: {knowledge.stage_focus(p, key)})")
    return "; ".join(out) or "not given"


def _context(prep, acc):
    aid = acc["id"]
    products = (prep.get("products") or "amasol").split(",")
    fs = current_findings(aid)
    people = db.q("SELECT * FROM people WHERE account_id=? AND rep_status<>'removed'", (aid,))
    attendees = db.jload(prep["attendees_json"], []) or []
    att_names = {a["name"].lower() for a in attendees}
    people_txt = "\n".join(
        f"- person {p['id']}: {p['name']}, {p.get('title') or ''}{' (ATTENDEE)' if p['name'].lower() in att_names else ''}"
        f" [{p['rep_status']}]{' rep note: ' + p['rep_note'] if p.get('rep_note') else ''}" for p in people)
    qs = db.q("SELECT * FROM questions WHERE account_id=? AND status='open'", (aid,))
    q_txt = "\n".join(f"- q{q['id']} [{q['kind']}, {q['product']}{', for ' + q['person_name'] if q.get('person_name') else ''}"
                      f"{', checks #' + str(q['finding_id']) if q.get('finding_id') else ''}] {q['text']}" for q in qs)
    debriefs = db.q("SELECT * FROM debriefs WHERE account_id=? ORDER BY id DESC LIMIT 5", (aid,))
    notes = db.q("SELECT * FROM notes WHERE account_id=? ORDER BY id DESC LIMIT 10", (aid,))
    hist = "\n".join([f"- debrief {d['created_at'][:10]}: learned: {d['learned']}; next step: {d['next_step']} by {d['next_by']}" for d in debriefs]
                     + [f"- note {n['created_at'][:10]} by {n['author']}: {n['text']}" for n in notes]) or "(none)"
    run = db.get("runs", prep["run_id"]) if prep.get("run_id") else None
    apollo_used = bool(run and run.get("used_apollo"))
    return {
        "products": products, "findings": fs, "people": people, "questions": qs,
        "text": f"""{'MEETING' }
Company: {acc['name']} ({acc.get('legal_name') or ''}), website {acc.get('website') or ''}
Products: {', '.join(products)}
Meeting date: {prep.get('meeting_date')}
Stage: {_stages_text(prep)}
Attendees: {'; '.join(a['name'] + ', ' + (a.get('title') or '') for a in attendees)}
What a good result looks like: {prep.get('good_result')}
Lead source: {_lead_source_text(db.jload(prep.get('lead_source_json'), {}))}
Partner involved: {prep.get('partner') or 'none'}
Region: {prep.get('region') or 'the company main country'}
Apollo used in research: {'yes' if apollo_used else 'no (people findings are rated one level lower)'}

PRODUCT KNOWLEDGE (the only source for offerings, case studies and proof points):
{knowledge.products_text(products)}

TIPS (information only, never rules):
{chr(10).join('- ' + t for t in knowledge.tips())}

WRITING RULES:
{knowledge.writing_rules()}

FINDINGS (id, profile section, type, confidence, status):
{findings_digest(fs)}

PEOPLE:
{people_txt or '(none)'}

OPEN QUESTIONS HELD:
{q_txt or '(none)'}

HISTORY:
{hist}
"""}


def _instructions(sections: list[int], full: bool) -> str:
    specs = {
        2: "Account snapshot: legal entity, parent and subsidiaries, countries, size, our relationship, lead source.",
        3: "Business situation: priorities in the company's own words, results, programmes, M&A.",
        4: "IT environment and tech stack: tools and platforms, cloud, monitoring and ITSM stack, IT team and hiring. Mark inferences.",
        5: "Sourcing model and providers: outsourcing and managed-service partners, multi-vendor setup, integrator role. Important for klaricore.",
        6: "Industry and regulation: industry, countries, regulations that apply and why they matter.",
        7: "People: the wider buying committee in brief (attendee profiles are added by the tool from the findings; you may add one point per attendee on how to approach them).",
        8: "Our angle: the view for the chosen product(s): fit, maturity guess, which klaricore pains apply, relevant use cases and proof from the PRODUCT KNOWLEDGE only (kind 'knowledge', no finding ids needed; case study customer names are flagged by the tool). Lead with the customer's situation and outcomes, not a pitch.",
        9: "Recent signals: trigger events, newest first, with dates.",
        10: "Relevant history: past preps, debriefs, rep notes and prior knowledge that matter for this meeting.",
    }
    want = "\n".join(f"- Section {n}. {specs[n]}" for n in sections if n in specs)
    s = f"""Write these prep pack sections:
{want}

Rules:
- Each point is one or two short sentences and cites the finding ids it rests on. A point of kind "fact" or "inference" MUST cite finding ids. Use kind "not found" (no ids) to say plainly that nothing credible was found on something that matters.
- Use the rep's input and corrections over web research. Never state Low confidence findings as fact: phrase them as "to validate".
- Do not invent anything that is not in the findings or the product knowledge. Goals are never shown as results.
- Focus each section on the meeting stage, the chosen products and the lead source.
- Give every section a key_point (the one thing from it the rep should know) with its finding ids.
- Plain business English, never em dashes. Never mention Detectability.
"""
    if full:
        s += """Also write:
- highlights: the top 3 to 5 things the rep must know before walking in, each citing finding ids.
- question_groups: the questions to ask, grouped by theme and by attendee. Reuse held questions by question_id where they fit
  the stage; add new ones (question_id 0) only where needed. Questions that check a Medium or Low finding cite its finding_id.
  For klaricore POC stage include the three klaricore POC questions. 8 to 20 questions in total.
- plan: a suggested flow for the meeting and the suggested next step (for amasol the ORS workshop or questionnaire; for klaricore a
  POC working session or the next stage), with why it fits.
"""
    return s


def generate(prep_id: int, run_id=None, user=None, sections=None, base_brief=None, note=None) -> int:
    prep = db.get("preps", prep_id)
    acc = db.get("accounts", prep["account_id"])
    ctx = llm.Ctx(run_id, acc["id"])
    c = _context(prep, acc)
    full = sections is None
    secs = list(WRITTEN) if full else [s for s in sections if s in WRITTEN]
    prompt = c["text"] + "\n" + _instructions(secs, full) + "\nThen call write_brief."
    out = llm.structured(ctx, "brief" if full else f"redo section {sections}", research_system(), prompt, BRIEF_TOOL,
                         max_tokens=32000)
    by_id = {f["id"]: f for f in c["findings"]}
    content = db.jload(base_brief["content_json"], {}) if base_brief else {}
    content.setdefault("sections", {})
    for s in out.get("sections", []):
        no = s.get("no")
        if no not in secs:
            continue
        content["sections"][str(no)] = {
            "points": _clean_points(s.get("points", []), by_id, no),
            "key_point": {"text": s.get("key_point", ""), "finding_ids": [i for i in s.get("key_point_ids", []) if i in by_id]},
        }
    if full or 1 in (sections or []):
        content["highlights"] = _clean_points(out.get("highlights", []), by_id, 1)[:5]
    if full or 11 in (sections or []):
        content["question_groups"] = _store_questions(acc["id"], prep_id, out.get("question_groups", []), by_id)
    if full or 12 in (sections or []):
        content["plan"] = out.get("plan") or content.get("plan") or {}
    content["meta"] = {"generated_at": db.now(), "apollo_used": "Apollo was not used" not in (
        (db.get("runs", prep["run_id"]) or {}).get("apollo_note") or "") if prep.get("run_id") else False}
    return save_version(prep_id, content, user, note or ("Generated" if full else f"Redid section(s) {', '.join(map(str, sections))}"))


def redo(brief_id: int, section: int, user=None, run_id=None) -> int:
    b = db.get("briefs", brief_id)
    targets = {1: [1], 11: [11], 12: [12]}.get(section, [section])
    if section in (1, 11, 12):
        # these come from the full-brief part of the tool; ask for one cheap section plus that part
        prep = db.get("preps", b["prep_id"])
        acc = db.get("accounts", prep["account_id"])
        ctx = llm.Ctx(run_id, acc["id"])
        c = _context(prep, acc)
        what = {1: "Write only the highlights (3 to 5) and leave sections empty.",
                11: "Write only question_groups and leave sections empty.",
                12: "Write only the plan and leave sections empty."}[section]
        prompt = c["text"] + "\n" + _instructions([], True) + "\n" + what + "\nThen call write_brief."
        out = llm.structured(ctx, f"redo section {section}", research_system(), prompt, BRIEF_TOOL)
        by_id = {f["id"]: f for f in c["findings"]}
        content = db.jload(b["content_json"], {})
        if section == 1:
            content["highlights"] = _clean_points(out.get("highlights", []), by_id, 1)[:5]
        elif section == 11:
            content["question_groups"] = _store_questions(acc["id"], b["prep_id"], out.get("question_groups", []), by_id)
        else:
            content["plan"] = out.get("plan") or {}
        return save_version(b["prep_id"], content, user, f"Redid section {section}")
    return generate(b["prep_id"], run_id=run_id, user=user, sections=targets, base_brief=b)


def _clean_points(points, by_id, section):
    out = []
    for p in points:
        text = rules.clean_text(p.get("text", ""))
        if not text or rules.banned(text):
            continue
        ids = [i for i in p.get("finding_ids", []) if i in by_id]
        kind = p.get("kind", "fact")
        if kind in ("fact", "inference") and not ids:
            if section in (8, 12):
                kind = "knowledge"
            else:
                continue  # a point without a source is never shown as fact
        out.append({"text": text, "finding_ids": ids, "kind": kind})
    return out


def _store_questions(aid, prep_id, groups, by_id):
    out = []
    for g in groups:
        qs = []
        for qd in g.get("questions", []):
            t = rules.clean_text(qd.get("text", ""))
            if not t or rules.banned(t):
                continue
            qid = qd.get("question_id") or 0
            row = db.q1("SELECT * FROM questions WHERE id=? AND account_id=?", (qid, aid)) if qid else None
            fid = qd.get("finding_id") if qd.get("finding_id") in by_id else (row or {}).get("finding_id")
            if not row:
                row_id = db.insert("questions", {"account_id": aid, "text": t, "kind": "check" if fid else "brief",
                                                 "product": qd.get("product"), "theme": g.get("theme"),
                                                 "person_name": g.get("attendee") or None, "finding_id": fid,
                                                 "prep_id": prep_id, "status": "open", "created_at": db.now()})
            else:
                row_id = row["id"]
            low = bool(fid and by_id.get(fid, {}).get("confidence") in ("Medium", "Low"))
            qs.append({"text": t, "question_id": row_id, "finding_id": fid, "checks_low": low,
                       "product": qd.get("product")})
        if qs:
            out.append({"theme": rules.clean_text(g.get("theme", "")), "attendee": g.get("attendee") or "", "questions": qs})
    return out


def snapshot(aid):
    fs = current_findings(aid, include_superseded=True)
    return {"taken_at": db.now(),
            "findings": [{"id": f["id"], "status": f["status"], "confidence": f["confidence"], "type": f["type"],
                          "verdict": f.get("rep_verdict"), "statement": f["statement"]} for f in fs],
            "people": [{"id": p["id"], "name": p["name"], "rep_status": p["rep_status"]}
                       for p in db.q("SELECT * FROM people WHERE account_id=?", (aid,))]}


def save_version(prep_id, content, user, note):
    prep = db.get("preps", prep_id)
    v = (db.q1("SELECT MAX(version) v FROM briefs WHERE prep_id=?", (prep_id,)) or {}).get("v") or 0
    return db.insert("briefs", {"prep_id": prep_id, "version": v + 1, "content_json": db.jdump(content),
                                "snapshot_json": db.jdump(snapshot(prep["account_id"])), "note": note,
                                "created_by": user, "created_at": db.now()})

# ---------------------------------------------------------------- view model for templates

def view(brief_id: int) -> dict:
    b = db.get("briefs", brief_id)
    prep = db.get("preps", b["prep_id"])
    acc = db.get("accounts", prep["account_id"])
    content = db.jload(b["content_json"], {})
    all_f = {f["id"]: f for f in db.q("SELECT * FROM findings WHERE account_id=?", (acc["id"],))}
    for f in all_f.values():
        f["sources"] = db.jload(f["sources_json"], [])
        f["out_of_date"] = rules.out_of_date(f)
    used_ids = set()

    def deco(p):
        fs = [all_f[i] for i in p.get("finding_ids", []) if i in all_f]
        used_ids.update(f["id"] for f in fs)
        conf = rules.worst([f["confidence"] for f in fs])
        types = {f["type"] for f in fs}
        label = None
        rep = [f["type"] == "Rep input" or f.get("rep_verdict") in ("Confirmed", "Corrected") for f in fs]
        if fs and all(rep):
            label = "Rep input"
            conf = "High"
        elif any(rep):
            label = "Rep input and research"
        elif p.get("kind") == "inference" or "Inferred" in types:
            label = "Inference"
        if conf == "Low":
            label = (label + ", to validate") if label else "To validate"
        uniq, reasons = {}, []
        for f in fs:
            for sx in f["sources"]:
                uniq.setdefault(norm_url(sx.get("url")) or sx.get("publisher"), sx)
            if f["type"] == "Inferred" and f.get("reasoning") and f["reasoning"] not in reasons:
                reasons.append(f["reasoning"])
        return {**p, "sources": list(uniq.values())[:6], "reasons": reasons[:2], "confidence": conf if fs else None, "label": label, "findings": fs,
                "conflict": any(f["status"] == "Conflicting" for f in fs),
                "stale": any(f["out_of_date"] for f in fs)}

    sections = {}
    for no in WRITTEN:
        s = content.get("sections", {}).get(str(no), {"points": [], "key_point": {"text": "", "finding_ids": []}})
        sections[no] = {"points": [deco(p) for p in s.get("points", [])], "key_point": deco(s.get("key_point") or {"text": "", "finding_ids": []})}
    highlights = [deco(p) for p in content.get("highlights", [])]
    attendees = db.jload(prep["attendees_json"], []) or []
    att_cards = []
    for a in attendees:
        p = db.q1("SELECT * FROM people WHERE account_id=? AND lower(name)=lower(?)", (acc["id"], a["name"]))
        pts = []
        if p:
            for f in db.q("SELECT * FROM findings WHERE account_id=? AND person_id=? AND status<>'Superseded' AND COALESCE(rep_verdict,'')<>'Rejected' ORDER BY id", (acc["id"], p["id"])):
                f = all_f.get(f["id"], f)
                used_ids.add(f["id"])
                pts.append(f)
        tps = db.q("SELECT text FROM questions WHERE account_id=? AND kind='talking point' AND lower(person_name)=lower(?) AND status='open' LIMIT 2",
                   (acc["id"], a["name"]))
        att_cards.append({"name": a["name"], "title": a.get("title") or (p or {}).get("title"),
                          "link": a.get("link") or (p or {}).get("linkedin_url"), "person": p, "points": pts,
                          "talking_points": [t["text"] for t in tps]})
    qgroups = content.get("question_groups", [])
    for g in qgroups:
        for qd in g["questions"]:
            if qd.get("finding_id") in all_f:
                used_ids.add(qd["finding_id"])
                qd["finding"] = all_f[qd["finding_id"]]
    used = [all_f[i] for i in sorted(used_ids) if i in all_f]
    srcs = {}
    for f in used:
        for s in f["sources"]:
            k = norm_url(s["url"])
            if k not in srcs:
                srcs[k] = {**s, "findings": []}
            srcs[k]["findings"].append(f["id"])
    sl = db.jload(acc.get("source_list_json"), {}) or {}
    versions = db.q("SELECT id,version,note,created_by,created_at FROM briefs WHERE prep_id=? ORDER BY version DESC", (prep["id"],))
    stages = db.jload(prep["stages_json"], {}) or {}
    case_names = _case_study_names(prep)
    return {
        "brief": b, "prep": prep, "account": acc, "content": content, "sections": sections, "highlights": highlights,
        "attendee_cards": att_cards, "question_groups": qgroups, "plan": content.get("plan") or {},
        "tips": knowledge.tips(), "sources": sorted(srcs.values(), key=lambda s: (s["tier"], s.get("published") or "")),
        "source_list": sl, "titles": PACK_SECTIONS, "findings": all_f, "versions": versions,
        "stages_text": _stages_text(prep), "products": (prep.get("products") or "").split(","),
        "apollo_used": (content.get("meta") or {}).get("apollo_used"), "case_names": case_names,
        "lead_source": db.jload(prep.get("lead_source_json"), {}) or {},
        "stages": stages,
    }


def _case_study_names(prep):
    names = []
    for p in (prep.get("products") or "").split(","):
        txt = knowledge.read(f"{p}.md")
        in_cases = False
        for line in txt.splitlines():
            if line.startswith("## "):
                in_cases = "case stud" in line.lower()
                continue
            if in_cases and line.startswith("- "):
                name = line[2:].split(":")[0].split(".")[0].strip()
                if name and "anonymised" not in name.lower():
                    names.append(name.split("(")[0].strip())
    return names
