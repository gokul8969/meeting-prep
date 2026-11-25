"""Background work: research continues when the rep leaves the page."""
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import brief, db, llm, research, rules

log = logging.getLogger("mp.jobs")
POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="job")


def submit(fn, *a, **kw):
    def wrap():
        try:
            fn(*a, **kw)
        except Exception:
            log.error("job failed: %s", traceback.format_exc())
    return POOL.submit(wrap)


def recover_interrupted():
    for r in db.q("SELECT id FROM runs WHERE status='running'"):
        db.update("runs", r["id"], {"status": "failed", "error": "Interrupted by a server restart. Start it again.",
                                    "finished_at": db.now()})
    for p in db.q("SELECT id,status FROM preps WHERE status IN ('identifying','researching','writing')"):
        db.update("preps", p["id"], {"status": {"identifying": "start", "researching": "describe", "writing": "checkback"}[p["status"]]})

# ---------------------------------------------------------------- rep input

SPLIT_TOOL = {
    "name": "record_rep_facts",
    "description": "Record the facts in the rep's notes, one per item, each with its profile section.",
    "input_schema": {"type": "object", "properties": {
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "statement": {"type": "string"},
            "section": {"type": "integer", "description": "2 snapshot, 3 business, 4 IT environment, 5 sourcing, 6 industry and regulation, 7 people, 9 signals, 10 interaction history"},
            "person_name": {"type": "string", "description": "If about one person, their name, else empty."},
            "answers_question_id": {"type": "integer", "description": "If it answers one of the listed open questions, its id, else 0."}},
            "required": ["statement", "section", "person_name", "answers_question_id"]}},
    }, "required": ["facts"]},
}


def store_rep_text(aid, text, *, origin, user, run_id=None, questions=None):
    """Split free text (notes, pasted emails, debrief) into Rep input findings. Rep input always wins."""
    if not text or not text.strip():
        return []
    qtxt = "\n".join(f"- q{q['id']}: {q['text']}" for q in (questions or []))
    prompt = f"""A sales rep wrote the notes below about this account. Pull out each fact as a separate short statement in plain
business English, in the rep's meaning. Do not add, generalise or strengthen anything: "they use ServiceNow for ITSM" stays
exactly that, it never becomes "they shared their tooling". Keep the context of a fact (who said it, when or where) inside that
fact's statement, and never record the conversation itself as a separate fact. Ignore greetings and email boilerplate.
Never use em dashes.
{('Open questions (mark answers_question_id if a fact answers one):' + chr(10) + qtxt) if qtxt else ''}

NOTES:
{text[:20000]}

Call record_rep_facts."""
    out = llm.structured(llm.Ctx(run_id, aid), f"rep input ({origin})", "You structure a sales rep's notes. You never invent facts.",
                         prompt, SPLIT_TOOL, effort="low")
    ids = []
    for f in out.get("facts", []):
        pid = None
        if f.get("person_name"):
            p = db.q1("SELECT id FROM people WHERE account_id=? AND lower(name)=lower(?)", (aid, f["person_name"]))
            pid = p["id"] if p else None
        fid = research.add_finding(aid, {
            "section": f["section"] if f.get("section") in (2, 3, 4, 5, 6, 7, 9, 10) else 10,
            "statement": f["statement"], "type": "Rep input",
            "sources": [{"url": "", "publisher": f"Rep input ({user or 'rep'}, {db.today()})", "tier": 0, "published": db.today()}],
            "origin": origin, "person_id": pid, "product": "both", "topic": "rep input"}, run_id, user=user)
        if fid:
            ids.append(fid)
        qid = f.get("answers_question_id")
        if qid and db.q1("SELECT id FROM questions WHERE id=? AND account_id=? AND status='open'", (qid, aid)):
            db.update("questions", qid, {"status": "answered", "answer": f["statement"], "answered_at": db.now(),
                                         "answered_by": user})
    return ids


def rep_fact(aid, section, statement, *, origin="Rep input", user=None, person_id=None, run_id=None, topic="rep input"):
    return research.add_finding(aid, {
        "section": section, "statement": statement, "type": "Rep input",
        "sources": [{"url": "", "publisher": f"Rep input ({user or 'rep'}, {db.today()})", "tier": 0, "published": db.today()}],
        "origin": origin, "person_id": person_id, "product": "both", "topic": topic}, run_id, user=user)

# ---------------------------------------------------------------- identification

def identify_job(prep_id, user):
    prep = db.get("preps", prep_id)
    try:
        cands = research.identify(prep["company_input"], prep.get("region") or "", llm.Ctx())
        for c in cands:
            ex = db.q1("SELECT id,name,in_radar,last_research_at FROM accounts WHERE domain=?", (c.get("domain"),)) if c.get("domain") else None
            c["existing"] = ex
        db.update("preps", prep_id, {"candidates_json": db.jdump(cands), "status": "confirm", "updated_at": db.now()})
    except Exception as e:
        log.error("identify failed: %s", traceback.format_exc())
        db.update("preps", prep_id, {"candidates_json": db.jdump({"error": str(e)}), "status": "confirm"})

# ---------------------------------------------------------------- meeting prep research

def prep_research_job(prep_id, user):
    prep = db.get("preps", prep_id)
    aid = prep["account_id"]
    run_id = prep["run_id"]
    prog = research.Progress(run_id)
    try:
        prog.add("rep", "Store your input as rep input")
        prog.set("rep", "running")
        acc = db.get("accounts", aid)
        ls = db.jload(prep.get("lead_source_json"), {}) or {}
        attendees = db.jload(prep["attendees_json"], []) or []
        if ls.get("kind"):
            text = f"Lead source: {ls['kind']}" + (f" ({ls['detail']})" if ls.get("detail") else "") + "."
            rep_fact(aid, 2, text, user=user, run_id=run_id, topic="lead source")
            db.update("accounts", aid, {"lead_source": text})
        if prep.get("partner"):
            rep_fact(aid, 2, f"Partner involved: {prep['partner']}.", user=user, run_id=run_id, topic="partner")
        stage_txt = brief._stages_text(prep)
        rep_fact(aid, 10, f"Meeting prep for {prep.get('meeting_date')}: stage {stage_txt}; attendees "
                 + "; ".join(f"{a['name']} ({a.get('title') or 'title not given'})" for a in attendees)
                 + f"; goal: {prep.get('good_result')}.", user=user, run_id=run_id, topic="meeting prep")
        for a in attendees:
            p = db.q1("SELECT id FROM people WHERE account_id=? AND lower(name)=lower(?)", (aid, a["name"]))
            if not p:
                db.insert("people", {"account_id": aid, "name": a["name"], "title": a.get("title"),
                                     "linkedin_url": a.get("link") or "", "origin": "Rep input", "rep_status": "rep added",
                                     "created_at": db.now(), "updated_at": db.now()})
        if prep.get("known_text"):
            store_rep_text(aid, prep["known_text"], origin="Rep input", user=user, run_id=run_id,
                           questions=db.q("SELECT id,text FROM questions WHERE account_id=? AND status='open'", (aid,)))
        prog.set("rep", "done")
        mode = "find_new" if acc.get("last_research_at") else "full"
        links = [a.get("link") for a in attendees if a.get("link")]
        res = research.run_research(run_id, aid, mode=mode, products=(prep.get("products") or "amasol").split(","),
                                    attendees=attendees, rep_links=links, prep_id=prep_id)
        prog.add("checkback", "Prepare check back")
        prog.set("checkback", "running")
        try:
            pick_gap_questions(prep_id, run_id)
            prog.set("checkback", "done")
        except Exception as e:
            prog.set("checkback", "failed", str(e)[:200])
        db.update("runs", run_id, {"status": "done" if not res["failures"] else "partial",
                                   "error": "; ".join(res["failures"]) or None, "finished_at": db.now()})
        db.update("preps", prep_id, {"status": "checkback", "updated_at": db.now()})
    except Exception as e:
        log.error("prep research failed: %s", traceback.format_exc())
        db.update("runs", run_id, {"status": "failed", "error": str(e), "finished_at": db.now()})
        db.update("preps", prep_id, {"status": "checkback", "updated_at": db.now()})


GAPQ_TOOL = {
    "name": "pick_gap_questions",
    "description": "Pick the gap questions to ask the rep before the brief is written.",
    "input_schema": {"type": "object", "properties": {
        "questions": {"type": "array", "items": {"type": "object", "properties": {
            "text": {"type": "string", "description": "Short question to the rep, e.g. 'We found no information on their monitoring tools. Do you know which they use?'"},
            "question_id": {"type": "integer"},
            "section": {"type": "integer", "description": "Profile section an answer belongs to (2-10)."}},
            "required": ["text", "question_id", "section"]}},
    }, "required": ["questions"]},
}


def pick_gap_questions(prep_id, run_id):
    prep = db.get("preps", prep_id)
    aid = prep["account_id"]
    gaps = db.q("SELECT id,text,theme,product FROM questions WHERE account_id=? AND status='open' AND kind='gap'", (aid,))
    if not gaps:
        db.update("preps", prep_id, {"checkback_json": db.jdump({"gap_questions": []})})
        return
    prompt = f"""Meeting: {brief._stages_text(prep)}; products {prep.get('products')}; goal: {prep.get('good_result')}.
The research could not find the items below. Pick at most 3 that really matter for this meeting's brief and that the rep may
know, and phrase each as a short question to the rep. Pick none if none matter. Never use em dashes.
{chr(10).join(f"- q{g['id']} ({g['product']}, {g['theme']}): {g['text']}" for g in gaps)}
Call pick_gap_questions."""
    out = llm.structured(llm.Ctx(run_id, aid), "gap questions", "You help a sales rep prepare.", prompt, GAPQ_TOOL, effort="low")
    qs = [q for q in out.get("questions", []) if any(g["id"] == q.get("question_id") for g in gaps)][:3]
    db.update("preps", prep_id, {"checkback_json": db.jdump({"gap_questions": qs})})


def brief_job(prep_id, user):
    prep = db.get("preps", prep_id)
    run_id = research.new_run("brief", prep["account_id"], prep_id, user)
    prog = research.Progress(run_id)
    prog.add("write", "Write the summary doc and prep pack")
    prog.set("write", "running")
    db.update("preps", prep_id, {"status": "writing", "updated_at": db.now()})
    try:
        bid = brief.generate(prep_id, run_id=run_id, user=user)
        prog.set("write", "done")
        db.update("runs", run_id, {"status": "done", "finished_at": db.now()})
        db.update("preps", prep_id, {"status": "ready", "updated_at": db.now()})
        db.timeline(prep["account_id"], "prep", f"Meeting brief written for {prep.get('meeting_date')} (version {db.get('briefs', bid)['version']})",
                    run_id, link=f"/briefs/{bid}")
    except Exception as e:
        log.error("brief failed: %s", traceback.format_exc())
        prog.set("write", "failed", str(e)[:200])
        db.update("runs", run_id, {"status": "failed", "error": str(e), "finished_at": db.now()})
        db.update("preps", prep_id, {"status": "checkback", "updated_at": db.now()})


def redo_job(brief_id, section, user, run_id):
    try:
        brief.redo(brief_id, section, user=user, run_id=run_id)
        db.update("runs", run_id, {"status": "done", "finished_at": db.now()})
    except Exception as e:
        log.error("redo failed: %s", traceback.format_exc())
        db.update("runs", run_id, {"status": "failed", "error": str(e), "finished_at": db.now()})

# ---------------------------------------------------------------- Radar

def find_new_job(account_id, user, run_id=None, kind="find_new"):
    acc = db.get("accounts", account_id)
    run_id = run_id or research.new_run(kind, account_id, None, user)
    try:
        focus = (acc.get("product_focus") or "amasol,klaricore").replace("both", "amasol,klaricore").split(",")
        mode = "find_new" if acc.get("last_research_at") else "full"
        res = research.run_research(run_id, account_id, mode=mode, products=focus)
        db.update("runs", run_id, {"status": "done" if not res["failures"] else "partial",
                                   "error": "; ".join(res["failures"]) or None, "finished_at": db.now()})
        return res
    except Exception as e:
        log.error("find new failed: %s", traceback.format_exc())
        db.update("runs", run_id, {"status": "failed", "error": str(e), "finished_at": db.now()})
        return {"failures": [str(e)], "labels": {}}


def add_accounts_job(rows, user, batch_id=None):
    """Directly added accounts (single or list): identify each, merge duplicates, deep research.

    Matches the tool is unsure about are parked for the rep to confirm (account_pending setting)."""
    pending = db.jload(db.setting("pending_accounts", "[]"), [])
    for row in rows:
        name, website = row.get("name", "").strip(), row.get("website", "").strip()
        if not name and not website:
            continue
        try:
            dom = rules.domain_of(website)
            ex = db.q1("SELECT * FROM accounts WHERE domain=?", (dom,)) if dom else None
            if ex:
                _radar_on(ex["id"], row, user)
                submit(find_new_job, ex["id"], user)
                continue
            cands = research.identify(name or website, f"website: {website}" if website else "", llm.Ctx())
            sure = [c for c in cands if dom and c.get("domain") == dom]
            if len(sure) == 1 or (len(cands) == 1 and (not dom or cands[0].get("domain") == dom)):
                c = sure[0] if sure else cands[0]
                aid = research.find_or_create_account(c, user)
                _radar_on(aid, row, user)
                submit(find_new_job, aid, user, None, "deep")
            else:
                pending.append({"row": row, "candidates": cands, "added_by": user, "at": db.now()})
        except Exception as e:
            pending.append({"row": row, "candidates": [], "error": str(e), "added_by": user, "at": db.now()})
    db.set_setting("pending_accounts", db.jdump(pending))


def _radar_on(aid, row, user):
    acc = db.get("accounts", aid)
    upd = {"in_radar": 1, "radar_added_at": acc.get("radar_added_at") or db.now(), "radar_removed_at": None, "updated_at": db.now()}
    if row.get("product_focus"):
        upd["product_focus"] = row["product_focus"]
    if row.get("region"):
        upd["region"] = row["region"]
    db.update("accounts", aid, upd)
    if row.get("lead_source"):
        rep_fact(aid, 2, f"Lead source: {row['lead_source']}.", user=user, topic="lead source")
        db.update("accounts", aid, {"lead_source": row["lead_source"]})
    if row.get("notes"):
        db.insert("notes", {"account_id": aid, "text": row["notes"], "author": user, "created_at": db.now()})
    if not acc.get("in_radar"):
        db.timeline(aid, "radar", f"Added to the Trusted Advisor Radar program by {user}")

# ---------------------------------------------------------------- debrief

def debrief_job(debrief_id, user):
    d = db.get("debriefs", debrief_id)
    aid = d["account_id"]
    prep = db.get("preps", d["prep_id"])
    run_id = research.new_run("debrief", aid, prep["id"], user)
    try:
        if d.get("learned"):
            store_rep_text(aid, d["learned"], origin="Debrief", user=user, run_id=run_id,
                           questions=db.q("SELECT id,text FROM questions WHERE account_id=? AND status='open'", (aid,)))
        db.update("runs", run_id, {"status": "done", "finished_at": db.now()})
    except Exception as e:
        log.error("debrief processing failed: %s", traceback.format_exc())
        db.update("runs", run_id, {"status": "failed", "error": str(e), "finished_at": db.now()})
