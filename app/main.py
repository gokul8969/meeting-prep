"""Web app: meeting prep (pull) and Trusted Advisor Radar (push) on one account profile."""
import csv
import io
import json
import logging
import secrets
import subprocess
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import brief, config, db, digest, jobs, knowledge, research, rules

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("mp")

APP_DIR = Path(__file__).parent
db.init()


def _secret():
    p = config.SECRET_KEY_FILE
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        p.chmod(0o600)
    return p.read_text().strip()


def _users() -> dict:
    if config.USERS_ENV:
        return dict(x.split(":", 1) for x in config.USERS_ENV.split(",") if ":" in x)
    p = config.DATA_DIR / "users.json"
    if not p.exists():
        pw = secrets.token_urlsafe(9)
        p.write_text(json.dumps({"admin": pw}, indent=2))
        p.chmod(0o600)
        log.warning("Created %s with user 'admin'. Edit it to add the named POC users.", p)
    return json.loads(p.read_text())


app = FastAPI(title="Meeting Prep and Trusted Advisor Radar")
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
T = Jinja2Templates(directory=APP_DIR / "templates")
T.env.globals.update(SECTIONS=rules.SECTIONS, PACK=brief.PACK_SECTIONS, today=lambda: date.today().isoformat())
T.env.filters["fromjson"] = lambda s: db.jload(s, None)
T.env.filters["host"] = rules.host


@app.on_event("startup")
def _startup():
    jobs.recover_interrupted()
    digest.start()
    _users()


PUBLIC = ("/login", "/static")


@app.middleware("http")
async def auth(request: Request, call_next):
    if not request.url.path.startswith(PUBLIC) and not request.session.get("user"):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "login required"}, status_code=401)
        return RedirectResponse("/login?next=" + request.url.path, status_code=303)
    return await call_next(request)


# added after the auth middleware so it wraps it (the last middleware added runs first)
app.add_middleware(SessionMiddleware, secret_key=_secret(), max_age=14 * 24 * 3600, same_site="lax")


def user(request: Request) -> str:
    return request.session.get("user", "")


def render(request, name, **ctx):
    ctx.setdefault("user", user(request))
    ctx.setdefault("open_alerts", (db.q1("SELECT COUNT(*) n FROM alerts WHERE status='open'") or {}).get("n", 0))
    ctx.setdefault("due_debriefs", len(_due_debriefs()))
    ctx.setdefault("flash", request.session.pop("flash", None))
    return T.TemplateResponse(request, name, ctx)


def back(path):
    return RedirectResponse(path, status_code=303)


def _due_debriefs():
    return db.q("""SELECT p.*, a.name account_name FROM preps p JOIN accounts a ON a.id=p.account_id
                   WHERE p.status='ready' AND p.meeting_date <= ? AND NOT EXISTS
                   (SELECT 1 FROM debriefs d WHERE d.prep_id=p.id) ORDER BY p.meeting_date""", (date.today().isoformat(),))

# ---------------------------------------------------------------- login

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return T.TemplateResponse(request, "login.html", {"next": next, "error": None, "user": None})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/")):
    users = _users()
    if username in users and secrets.compare_digest(users[username], password):
        request.session["user"] = username
        return back(next if next.startswith("/") else "/")
    return T.TemplateResponse(request, "login.html", {"next": next, "error": "Wrong name or password.", "user": None},
                              status_code=401)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return back("/login")

# ---------------------------------------------------------------- dashboard

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    upcoming = db.q("""SELECT p.*, a.name account_name FROM preps p LEFT JOIN accounts a ON a.id=p.account_id
                       ORDER BY p.id DESC LIMIT 15""")
    alerts = db.q("""SELECT al.*, a.name account_name FROM alerts al JOIN accounts a ON a.id=al.account_id
                     WHERE al.status='open' ORDER BY al.priority='High' DESC, al.id DESC LIMIT 8""")
    radar_n = db.q1("SELECT COUNT(*) n FROM accounts WHERE in_radar=1")["n"]
    for p in upcoming:
        b = db.q1("SELECT id FROM briefs WHERE prep_id=? ORDER BY version DESC", (p["id"],))
        p["brief_id"] = b["id"] if b else None
    return render(request, "home.html", preps=upcoming, alerts=alerts, radar_n=radar_n, debriefs=_due_debriefs())

# ---------------------------------------------------------------- meeting prep: step 1 start

@app.get("/prep/new", response_class=HTMLResponse)
def prep_new(request: Request, account_id: int | None = None):
    acc = db.get("accounts", account_id) if account_id else None
    return render(request, "prep_new.html", account=acc)


def _products(p: str) -> str:
    return {"amasol": "amasol", "klaricore": "klaricore"}.get(p, "amasol,klaricore")


@app.post("/prep/new")
def prep_create(request: Request, product: str = Form(...), company: str = Form(""), meeting_date: str = Form(...),
                account_id: int | None = Form(None)):
    u = user(request)
    row = {"products": _products(product), "meeting_date": meeting_date, "company_input": company.strip(),
           "created_by": u, "created_at": db.now(), "updated_at": db.now()}
    if account_id:
        acc = db.get("accounts", account_id)
        if not acc:
            raise HTTPException(404)
        row.update(account_id=account_id, company_input=acc["name"], status="radar")
        pid = db.insert("preps", row)
        return back(f"/preps/{pid}")
    if not company.strip():
        return back("/prep/new")
    row["status"] = "identifying"
    pid = db.insert("preps", row)
    jobs.submit(jobs.identify_job, pid, u)
    return back(f"/preps/{pid}")


@app.get("/preps/{pid}", response_class=HTMLResponse)
def prep_page(request: Request, pid: int):
    prep = db.get("preps", pid)
    if not prep:
        raise HTTPException(404)
    acc = db.get("accounts", prep["account_id"]) if prep.get("account_id") else None
    ctx = {"prep": prep, "account": acc, "products": (prep.get("products") or "").split(","),
           "stages": knowledge.stages(), "run": db.get("runs", prep["run_id"]) if prep.get("run_id") else None}
    st = prep["status"]
    if st == "confirm":
        ctx["candidates"] = db.jload(prep["candidates_json"], [])
    if st == "checkback":
        aid = prep["account_id"]
        fs = research.current_findings(aid, [2, 4, 5, 7, 9])
        people = {p["id"]: p for p in db.q("SELECT * FROM people WHERE account_id=?", (aid,))}
        for f in fs:
            f["sources"] = db.jload(f["sources_json"], [])
            f["person"] = people.get(f.get("person_id"))
            f["out_of_date"] = rules.out_of_date(f)
        ctx["groups"] = [(s, rules.SECTIONS[s], [f for f in fs if f["section"] == s]) for s in (2, 4, 5, 7, 9)]
        ctx["gapq"] = (db.jload(prep.get("checkback_json"), {}) or {}).get("gap_questions", [])
        ctx["attendees"] = db.jload(prep["attendees_json"], []) or []
    if st in ("ready", "debriefed"):
        b = db.q1("SELECT id FROM briefs WHERE prep_id=? ORDER BY version DESC", (pid,))
        ctx["brief_id"] = b["id"] if b else None
    if st == "describe":
        ctx["attendees"] = db.jload(prep.get("attendees_json"), []) or []
        ctx["lead"] = db.jload(prep.get("lead_source_json"), {}) or {}
        ctx["prep_stages"] = db.jload(prep.get("stages_json"), {}) or {}
    return render(request, "prep.html", **ctx)


@app.get("/api/preps/{pid}")
def prep_status(pid: int):
    prep = db.get("preps", pid)
    run = db.get("runs", prep["run_id"]) if prep and prep.get("run_id") else None
    if prep and prep["status"] == "writing":
        run = db.q1("SELECT * FROM runs WHERE prep_id=? AND kind='brief' ORDER BY id DESC", (pid,))
    return {"status": prep["status"] if prep else None, "run": _run_json(run)}


def _run_json(run):
    if not run:
        return None
    return {"id": run["id"], "status": run["status"], "steps": db.jload(run["steps_json"], []),
            "error": run.get("error"), "cost_usd": round(run.get("cost_usd") or 0, 3),
            "apollo_note": run.get("apollo_note"), "started_at": run.get("started_at"), "finished_at": run.get("finished_at")}


@app.get("/api/runs/{rid}")
def run_status(rid: int):
    return _run_json(db.get("runs", rid)) or {}


@app.post("/preps/{pid}/confirm")
def prep_confirm(request: Request, pid: int, choice: str = Form(...), hint: str = Form("")):
    prep = db.get("preps", pid)
    u = user(request)
    if choice == "retry":
        db.update("preps", pid, {"status": "identifying", "region": hint, "company_input": prep["company_input"]})
        jobs.submit(jobs.identify_job, pid, u)
        return back(f"/preps/{pid}")
    cands = db.jload(prep["candidates_json"], [])
    c = cands[int(choice)]
    aid = research.find_or_create_account(c, u)
    db.update("preps", pid, {"account_id": aid, "status": "radar", "updated_at": db.now()})
    return back(f"/preps/{pid}")


@app.post("/preps/{pid}/radar")
def prep_radar(request: Request, pid: int, answer: str = Form(...)):
    prep = db.get("preps", pid)
    if answer == "yes":
        jobs._radar_on(prep["account_id"], {"product_focus": prep.get("products")}, user(request))
    db.update("preps", pid, {"status": "describe", "updated_at": db.now()})
    return back(f"/preps/{pid}")

# ---------------------------------------------------------------- step 3 describe

@app.post("/preps/{pid}/describe")
async def prep_describe(request: Request, pid: int):
    form = await request.form()
    prep = db.get("preps", pid)
    u = user(request)
    names = form.getlist("att_name")
    titles = form.getlist("att_title")
    links = form.getlist("att_link")
    attendees = [{"name": n.strip(), "title": (titles[i] if i < len(titles) else "").strip(),
                  "link": (links[i] if i < len(links) else "").strip()}
                 for i, n in enumerate(names) if n.strip()]
    products = _products(form.get("product") or prep.get("products"))
    stages = {}
    for p in products.split(","):
        if form.get(f"stage_{p}"):
            stages[p] = form.get(f"stage_{p}")
    lead = {"kind": form.get("lead_kind", ""), "detail": (form.get("lead_detail") or "").strip()}
    errors = []
    if not attendees:
        errors.append("Add at least one attendee.")
    if not stages:
        errors.append("Pick a meeting stage.")
    if not (form.get("good_result") or "").strip():
        errors.append("Say in one line what a good result looks like.")
    if not lead["kind"]:
        errors.append("Pick the lead source.")
    upd = {"attendees_json": db.jdump(attendees), "stages_json": db.jdump(stages), "products": products,
           "meeting_date": form.get("meeting_date") or prep["meeting_date"],
           "good_result": (form.get("good_result") or "").strip(), "known_text": form.get("known_text") or "",
           "lead_source_json": db.jdump(lead), "partner": (form.get("partner") or "").strip(),
           "region": (form.get("region") or "").strip(), "other_stage_text": (form.get("other_stage_text") or "").strip(),
           "updated_at": db.now()}
    db.update("preps", pid, upd)
    if errors:
        request.session["flash"] = " ".join(errors)
        return back(f"/preps/{pid}")
    run_id = research.new_run("prep", prep["account_id"], pid, u)
    db.update("preps", pid, {"status": "researching", "run_id": run_id})
    jobs.submit(jobs.prep_research_job, pid, u)
    return back(f"/preps/{pid}")


@app.post("/preps/{pid}/restart")
def prep_restart(request: Request, pid: int):
    prep = db.get("preps", pid)
    run_id = research.new_run("prep", prep["account_id"], pid, user(request))
    db.update("preps", pid, {"status": "researching", "run_id": run_id})
    jobs.submit(jobs.prep_research_job, pid, user(request))
    return back(f"/preps/{pid}")

# ---------------------------------------------------------------- step 5 check back

@app.post("/findings/{fid}/verdict")
def finding_verdict(request: Request, fid: int, verdict: str = Form(...), correction: str = Form("")):
    f = db.get("findings", fid)
    if not f or verdict not in ("Confirmed", "Corrected", "Rejected", "Clear"):
        raise HTTPException(400)
    u = user(request)
    if verdict == "Clear":
        db.update("findings", fid, {"rep_verdict": None, "rep_correction": None})
        db.finding_event(fid, f["account_id"], "verdict cleared", "", u)
        return {"ok": True}
    upd = {"rep_verdict": verdict, "rep_verdict_by": u, "rep_verdict_at": db.now()}
    if verdict == "Corrected":
        correction = rules.clean_text(correction)
        if not correction:
            raise HTTPException(400, "correction text needed")
        upd["rep_correction"] = correction
        # the correction is stored as rep input and wins over research from now on
        new_id = jobs.rep_fact(f["account_id"], f["section"], correction, user=u, person_id=f.get("person_id"),
                               topic=f.get("topic") or "rep correction")
        db.update("findings", fid, {"superseded_by_id": new_id})
        db.update("findings", new_id, {"supersedes_id": fid})
    db.update("findings", fid, upd)
    if f.get("conflicts_with_id") and verdict in ("Confirmed", "Rejected"):
        other = db.get("findings", f["conflicts_with_id"])
        if other:
            if verdict == "Confirmed" and other["status"] == "Conflicting" and other["type"] != "Rep input":
                db.update("findings", other["id"], {"status": "Superseded", "superseded_by_id": fid})
            db.update("findings", fid, {"status": "Updated" if verdict == "Confirmed" else f["status"]})
    db.finding_event(fid, f["account_id"], f"rep {verdict.lower()}", correction, u)
    return {"ok": True}


@app.post("/preps/{pid}/write")
async def prep_write(request: Request, pid: int):
    form = await request.form()
    prep = db.get("preps", pid)
    u = user(request)
    gapq = (db.jload(prep.get("checkback_json"), {}) or {}).get("gap_questions", [])
    for g in gapq:
        ans = (form.get(f"gap_{g['question_id']}") or "").strip()
        if ans and form.get(f"gapdk_{g['question_id']}") != "1":
            jobs.rep_fact(prep["account_id"], g.get("section") or 10, f"{g['text']} Rep: {ans}", user=u, topic="gap answer")
            db.update("questions", g["question_id"], {"status": "answered", "answer": ans, "answered_at": db.now(),
                                                      "answered_by": u})
    jobs.submit(jobs.brief_job, pid, u)
    db.update("preps", pid, {"status": "writing"})
    return back(f"/preps/{pid}")

# ---------------------------------------------------------------- outputs

@app.get("/briefs/{bid}", response_class=HTMLResponse)
def brief_summary(request: Request, bid: int):
    v = brief.view(bid)
    return render(request, "brief_summary.html", v=v, standalone=False, pack_href=f"/briefs/{bid}/pack")


@app.get("/briefs/{bid}/pack", response_class=HTMLResponse)
def brief_pack(request: Request, bid: int):
    v = brief.view(bid)
    redo_runs = db.q("SELECT id,status FROM runs WHERE prep_id=? AND kind='redo' AND status='running'", (v["prep"]["id"],))
    return render(request, "brief_pack.html", v=v, standalone=False, redo_running=bool(redo_runs))


@app.post("/briefs/{bid}/redo")
def brief_redo(request: Request, bid: int, section: int = Form(...)):
    b = db.get("briefs", bid)
    prep = db.get("preps", b["prep_id"])
    run_id = research.new_run("redo", prep["account_id"], prep["id"], user(request))
    jobs.submit(jobs.redo_job, bid, section, user(request), run_id)
    return back(f"/briefs/{bid}/pack?redo={section}#s{section}")


@app.get("/api/briefs/{bid}/latest")
def brief_latest(bid: int):
    b = db.get("briefs", bid)
    latest = db.q1("SELECT id FROM briefs WHERE prep_id=? ORDER BY version DESC", (b["prep_id"],))
    running = db.q1("SELECT id FROM runs WHERE prep_id=? AND kind='redo' AND status='running'", (b["prep_id"],))
    failed = db.q1("SELECT error FROM runs WHERE prep_id=? AND kind='redo' ORDER BY id DESC", (b["prep_id"],))
    return {"latest": latest["id"], "running": bool(running), "error": (failed or {}).get("error")}


@app.post("/briefs/{bid}/edit")
def brief_edit(request: Request, bid: int, section: str = Form(...), index: int = Form(...), text: str = Form(...),
               is_fact: str = Form("0")):
    """Direct edit of one point. Edits to facts are stored back in the profile as rep input."""
    b = db.get("briefs", bid)
    prep = db.get("preps", b["prep_id"])
    u = user(request)
    content = db.jload(b["content_json"], {})
    text = rules.clean_text(text)
    if section == "highlights":
        pts = content.get("highlights", [])
    elif section.startswith("key:"):
        sec = content["sections"][section[4:]]
        pts = [sec.get("key_point")]
    else:
        pts = content["sections"][section]["points"]
    if section.startswith("key:"):
        index = 0
    if index >= len(pts):
        raise HTTPException(400)
    p = pts[index]
    if not text:
        if not section.startswith("key:"):
            pts.pop(index)
    else:
        old_ids = p.get("finding_ids", [])
        p["text"] = text
        if is_fact == "1":
            sec_no = None
            for fid in old_ids:
                f = db.get("findings", fid)
                if f:
                    sec_no = sec_no or f["section"]
                    db.update("findings", fid, {"rep_verdict": "Corrected", "rep_correction": text, "rep_verdict_by": u,
                                                "rep_verdict_at": db.now()})
                    db.finding_event(fid, f["account_id"], "rep corrected (brief edit)", text, u)
            if sec_no is None:
                sec_no = int(section) if section.isdigit() and int(section) in rules.SECTIONS and int(section) <= 10 else 10
            new_id = jobs.rep_fact(prep["account_id"], sec_no, text, user=u, topic="brief edit")
            p["finding_ids"] = [new_id]
            p["kind"] = "fact"
    nid = brief.save_version(b["prep_id"], content, u, f"Edited section {section}")
    return {"ok": True, "brief_id": nid}


@app.post("/briefs/{bid}/plan")
def brief_plan_edit(request: Request, bid: int, flow: str = Form(""), next_step: str = Form("")):
    b = db.get("briefs", bid)
    content = db.jload(b["content_json"], {})
    content["plan"] = {**(content.get("plan") or {}), "flow": [rules.clean_text(l) for l in flow.splitlines() if l.strip()],
                       "next_step": rules.clean_text(next_step)}
    nid = brief.save_version(b["prep_id"], content, user(request), "Edited meeting plan")
    return back(f"/briefs/{nid}/pack#s12")


def _render_static(request, bid):
    v = brief.view(bid)
    css = (APP_DIR / "static" / "app.css").read_text()
    summ = T.get_template("brief_summary.html").render(request=request, v=v, standalone=True, css=css,
                                                       pack_href="prep-pack.html", user=None)
    pack = T.get_template("brief_pack.html").render(request=request, v=v, standalone=True, css=css, user=None)
    return v, summ, pack


def _pdf(html_text: str, pdf_links_base: str | None = None) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "doc.html"
        out = Path(d) / "doc.pdf"
        src.write_text(html_text, encoding="utf-8")
        subprocess.run([config.CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={out}", src.as_uri()], check=True, capture_output=True, timeout=120)
        return out.read_bytes()


@app.get("/briefs/{bid}/download/{name}")
def brief_download(request: Request, bid: int, name: str):
    v, summ, pack = _render_static(request, bid)
    slug = "".join(ch if ch.isalnum() else "-" for ch in v["account"]["name"]).strip("-").lower()
    base = f"{slug}-{v['prep']['meeting_date']}-v{v['brief']['version']}"
    if name == "summary.html":
        return Response(summ, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{base}-summary.html"'})
    if name == "pack.html":
        return Response(pack, media_type="text/html", headers={"Content-Disposition": f'attachment; filename="{base}-prep-pack.html"'})
    if name in ("summary.pdf", "pack.pdf"):
        if name == "summary.pdf":
            # in the PDF the summary links to the prep pack sections in the tool
            html_text = T.get_template("brief_summary.html").render(
                request=request, v=v, standalone=True, css=(APP_DIR / "static" / "app.css").read_text(),
                pack_href=f"{config.PUBLIC_URL.rstrip('/')}/briefs/{bid}/pack", user=None)
        else:
            html_text = pack
        data = _pdf(html_text)
        fn = f"{base}-{'summary' if name == 'summary.pdf' else 'prep-pack'}.pdf"
        return Response(data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{fn}"'})
    if name == "all.zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("summary.html", summ)
            z.writestr("prep-pack.html", pack)
            try:
                z.writestr("prep-pack.pdf", _pdf(pack))
            except Exception as e:
                log.warning("pdf failed: %s", e)
        return Response(buf.getvalue(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{base}.zip"'})
    raise HTTPException(404)

# ---------------------------------------------------------------- step 8 debrief

@app.get("/preps/{pid}/debrief", response_class=HTMLResponse)
def debrief_page(request: Request, pid: int):
    prep = db.get("preps", pid)
    acc = db.get("accounts", prep["account_id"])
    people = db.q("SELECT * FROM people WHERE account_id=? AND rep_status<>'removed' ORDER BY name", (acc["id"],))
    qs = db.q("SELECT * FROM questions WHERE account_id=? AND status='open' ORDER BY kind, id", (acc["id"],))
    att = {a["name"].lower() for a in db.jload(prep["attendees_json"], []) or []}
    return render(request, "debrief.html", prep=prep, account=acc, people=people, questions=qs, att=att,
                  done=db.q1("SELECT * FROM debriefs WHERE prep_id=?", (pid,)))


@app.post("/preps/{pid}/debrief")
async def debrief_save(request: Request, pid: int):
    form = await request.form()
    prep = db.get("preps", pid)
    aid = prep["account_id"]
    u = user(request)
    people_changes, answers = [], []
    for p in db.q("SELECT * FROM people WHERE account_id=? AND rep_status<>'removed'", (aid,)):
        if form.get(f"pm_{p['id']}") == "1":
            role = (form.get(f"prole_{p['id']}") or "").strip()
            upd = {"rep_status": "confirmed", "updated_at": db.now()}
            if role and role != (p.get("title") or ""):
                upd.update(title=role, rep_status="corrected", rep_note=f"Role set by {u} in debrief")
            db.update("people", p["id"], upd)
            people_changes.append({"id": p["id"], "name": p["name"], "role": role or p.get("title")})
            jobs.rep_fact(aid, 7, f"{p['name']} matters for this account{(' (' + role + ')') if role else ''}.",
                          origin="Debrief", user=u, person_id=p["id"], topic=f"{p['name']}: matters")
    for n, r in zip(form.getlist("new_name"), form.getlist("new_role")):
        if n.strip():
            pid_ = db.insert("people", {"account_id": aid, "name": n.strip(), "title": r.strip(), "origin": "Debrief",
                                        "rep_status": "rep added", "created_at": db.now(), "updated_at": db.now()})
            jobs.rep_fact(aid, 7, f"{n.strip()}{', ' + r.strip() if r.strip() else ''} matters for this account.",
                          origin="Debrief", user=u, person_id=pid_, topic=f"{n.strip()}: matters")
            people_changes.append({"id": pid_, "name": n.strip(), "role": r.strip(), "new": True})
    for q in db.q("SELECT * FROM questions WHERE account_id=? AND status='open'", (aid,)):
        a = (form.get(f"q_{q['id']}") or "").strip()
        if a:
            db.update("questions", q["id"], {"status": "answered", "answer": a, "answered_at": db.now(), "answered_by": u})
            answers.append({"id": q["id"], "q": q["text"], "a": a})
            sec = db.get("findings", q["finding_id"])["section"] if q.get("finding_id") and db.get("findings", q["finding_id"]) else 10
            jobs.rep_fact(aid, sec, f"{q['text']} Answer: {a}", origin="Debrief", user=u, topic="answered question")
    next_step, next_by = (form.get("next_step") or "").strip(), (form.get("next_by") or "").strip()
    did = db.insert("debriefs", {"prep_id": pid, "account_id": aid, "learned": form.get("learned") or "",
                                 "people_json": db.jdump(people_changes), "answers_json": db.jdump(answers),
                                 "next_step": next_step, "next_by": next_by, "created_by": u, "created_at": db.now()})
    if next_step:
        jobs.rep_fact(aid, 10, f"Debrief of the {prep['meeting_date']} meeting: agreed next step: {next_step}"
                      + (f", by {next_by}" if next_by else "") + ".", origin="Debrief", user=u, topic="next step")
    acc = db.get("accounts", aid)
    db.timeline(aid, "debrief", f"Debrief by {u} of the {prep['meeting_date']} meeting. Next step: {next_step or 'not set'}"
                + (f" by {next_by}" if next_by else ""), link=f"/preps/{pid}")
    if acc.get("in_radar"):
        db.update("accounts", aid, {"moved_at": db.now(), "moved_note": f"Met on {prep['meeting_date']}; next: {next_step or 'not set'}"
                                    + (f" by {next_by}" if next_by else "")})
    db.update("preps", pid, {"status": "debriefed", "updated_at": db.now()})
    jobs.submit(jobs.debrief_job, did, u)
    return back(f"/accounts/{aid}?debriefed=1")

# ---------------------------------------------------------------- Radar

@app.get("/radar", response_class=HTMLResponse)
def radar(request: Request):
    accs = db.q("SELECT * FROM accounts ORDER BY in_radar DESC, name")
    for a in accs:
        a["alerts_open"] = db.q1("SELECT COUNT(*) n FROM alerts WHERE account_id=? AND status='open'", (a["id"],))["n"]
        a["high_open"] = db.q1("SELECT COUNT(*) n FROM alerts WHERE account_id=? AND status='open' AND priority='High'", (a["id"],))["n"]
        a["run"] = db.q1("SELECT * FROM runs WHERE account_id=? ORDER BY id DESC", (a["id"],))
    pending = db.jload(db.setting("pending_accounts", "[]"), [])
    return render(request, "radar.html", accounts=accs, pending=pending)


@app.post("/radar/add")
def radar_add(request: Request, name: str = Form(""), website: str = Form(""), product_focus: str = Form(""),
              region: str = Form(""), lead_source: str = Form(""), notes: str = Form("")):
    row = {"name": name, "website": website, "product_focus": _products(product_focus) if product_focus else "",
           "region": region, "lead_source": lead_source, "notes": notes}
    jobs.submit(jobs.add_accounts_job, [row], user(request))
    request.session["flash"] = f"Identifying {name or website}. It appears below once identified, or under 'To confirm' if the match is unsure."
    return back("/radar")


@app.post("/radar/bulk")
async def radar_bulk(request: Request, pasted: str = Form(""), file: UploadFile | None = File(None)):
    text = pasted
    if file is not None and file.filename:
        raw = await file.read()
        if file.filename.lower().endswith((".xlsx", ".xls")):
            request.session["flash"] = "Please upload a CSV file (or paste the rows)."
            return back("/radar")
        text = raw.decode("utf-8-sig", errors="replace")
    rows = _parse_rows(text)
    if not rows:
        request.session["flash"] = "No rows found. Use: company name, website (one per line, optional: product focus, region, lead source, notes)."
        return back("/radar")
    jobs.submit(jobs.add_accounts_job, rows, user(request))
    request.session["flash"] = f"Adding {len(rows)} accounts. Each is identified, duplicates merged, then researched."
    return back("/radar")


def _parse_rows(text: str):
    text = (text or "").strip()
    if not text:
        return []
    sample = text.splitlines()[0]
    delim = "\t" if "\t" in sample else (";" if sample.count(";") > sample.count(",") else ",")
    reader = list(csv.reader(io.StringIO(text), delimiter=delim))
    keys = ["name", "website", "product_focus", "region", "lead_source", "notes"]
    head = [h.strip().lower() for h in reader[0]]
    if any(h in ("company", "company name", "name", "website") for h in head):
        mapping = {"company": "name", "company name": "name", "name": "name", "website": "website", "product": "product_focus",
                   "product focus": "product_focus", "region": "region", "lead source": "lead_source", "notes": "notes"}
        cols = [mapping.get(h) for h in head]
        reader = reader[1:]
    else:
        cols = keys
    rows = []
    for r in reader:
        d = {}
        for i, v in enumerate(r):
            if i < len(cols) and cols[i]:
                d[cols[i]] = v.strip()
        if d.get("product_focus"):
            d["product_focus"] = _products(d["product_focus"].lower())
        if d.get("name") or d.get("website"):
            rows.append(d)
    return rows


@app.post("/radar/pending/{idx}")
def radar_pending(request: Request, idx: int, choice: str = Form(...)):
    pending = db.jload(db.setting("pending_accounts", "[]"), [])
    if idx >= len(pending):
        return back("/radar")
    item = pending.pop(idx)
    u = user(request)
    if choice != "drop":
        c = item["candidates"][int(choice)]
        aid = research.find_or_create_account(c, u)
        jobs._radar_on(aid, item["row"], u)
        jobs.submit(jobs.find_new_job, aid, u, None, "deep")
    db.set_setting("pending_accounts", db.jdump(pending))
    return back("/radar")


@app.get("/accounts/{aid}", response_class=HTMLResponse)
def account_page(request: Request, aid: int, show: str = ""):
    acc = db.get("accounts", aid)
    if not acc:
        raise HTTPException(404)
    all_f = db.q("SELECT * FROM findings WHERE account_id=? ORDER BY section, id DESC", (aid,))
    people = db.q("SELECT * FROM people WHERE account_id=? ORDER BY rep_status='removed', name", (aid,))
    pmap = {p["id"]: p for p in people}
    for f in all_f:
        f["sources"] = db.jload(f["sources_json"], [])
        f["out_of_date"] = rules.out_of_date(f)
        f["person"] = pmap.get(f.get("person_id"))
    current = [f for f in all_f if f["status"] != "Superseded"]
    by_sec = {s: [f for f in current if f["section"] == s] for s in rules.SECTIONS}
    srcs = {}
    for f in current:
        for s in f["sources"]:
            if s.get("url"):
                srcs.setdefault(research.norm_url(s["url"]), {**s, "n": 0})["n"] += 1
    for p in people:
        p["findings"] = [f for f in current if f.get("person_id") == p["id"]]
    return render(request, "account.html", a=acc, by_sec=by_sec, superseded=[f for f in all_f if f["status"] == "Superseded"],
                  people=people, questions=db.q("SELECT * FROM questions WHERE account_id=? ORDER BY status, id DESC", (aid,)),
                  alerts=db.q("SELECT * FROM alerts WHERE account_id=? ORDER BY status='open' DESC, id DESC", (aid,)),
                  notes=db.q("SELECT * FROM notes WHERE account_id=? ORDER BY id DESC", (aid,)),
                  timeline=db.q("SELECT * FROM timeline WHERE account_id=? ORDER BY id DESC LIMIT 100", (aid,)),
                  preps=db.q("SELECT * FROM preps WHERE account_id=? ORDER BY id DESC", (aid,)),
                  summary=db.jload(acc.get("summary_json"), {}) or {},
                  source_list=db.jload(acc.get("source_list_json"), {}) or {},
                  sources=sorted(srcs.values(), key=lambda s: (s.get("tier") or 9, s.get("publisher") or "")),
                  run=db.q1("SELECT * FROM runs WHERE account_id=? AND kind IN ('find_new','deep','digest','prep') ORDER BY id DESC", (aid,)),
                  findings_by_id={f["id"]: f for f in all_f})


@app.post("/accounts/{aid}/find-new")
def account_find_new(request: Request, aid: int):
    running = db.q1("SELECT id FROM runs WHERE account_id=? AND status='running' AND kind IN ('find_new','deep','digest','prep')", (aid,))
    if not running:
        run_id = research.new_run("find_new", aid, None, user(request))
        jobs.submit(jobs.find_new_job, aid, user(request), run_id)
    return back(f"/accounts/{aid}")


@app.post("/accounts/{aid}/note")
def account_note(request: Request, aid: int, text: str = Form(...)):
    if text.strip():
        db.insert("notes", {"account_id": aid, "text": text.strip(), "author": user(request), "created_at": db.now()})
        jobs.rep_fact(aid, 10, text.strip(), user=user(request), topic="rep note")
        db.timeline(aid, "note", f"Note by {user(request)}: {text.strip()[:140]}")
    return back(f"/accounts/{aid}#notes")


@app.post("/accounts/{aid}/radar")
def account_radar(request: Request, aid: int, on: str = Form(...)):
    if on == "1":
        jobs._radar_on(aid, {}, user(request))
    else:
        db.update("accounts", aid, {"in_radar": 0, "radar_removed_at": db.now()})
        db.timeline(aid, "radar", f"Removed from the Radar program by {user(request)}. Profile kept, no longer tracked.")
    return back(f"/accounts/{aid}")


@app.post("/accounts/{aid}/people/{pid}")
def person_update(request: Request, aid: int, pid: int, action: str = Form(...), title: str = Form(""), note: str = Form("")):
    p = db.q1("SELECT * FROM people WHERE id=? AND account_id=?", (pid, aid))
    if not p:
        raise HTTPException(404)
    u = user(request)
    if action == "confirm":
        db.update("people", pid, {"rep_status": "confirmed", "updated_at": db.now()})
    elif action == "remove":
        db.update("people", pid, {"rep_status": "removed", "rep_note": note or f"Removed by {u}", "updated_at": db.now()})
    elif action == "correct":
        db.update("people", pid, {"rep_status": "corrected", "title": title or p["title"], "rep_note": note, "updated_at": db.now()})
        jobs.rep_fact(aid, 7, f"{p['name']}: {title or p['title']}{'. ' + note if note else ''}", user=u, person_id=pid,
                      topic=f"{p['name']}: role")
    elif action == "restore":
        db.update("people", pid, {"rep_status": "confirmed", "updated_at": db.now()})
    db.timeline(aid, "people", f"{u} {action}ed {p['name']}" if action != "remove" else f"{u} removed {p['name']} from the buying committee")
    return back(f"/accounts/{aid}#people")


@app.post("/accounts/{aid}/people")
def person_add(request: Request, aid: int, name: str = Form(...), title: str = Form(""), link: str = Form(""), note: str = Form("")):
    u = user(request)
    pid = db.insert("people", {"account_id": aid, "name": name.strip(), "title": title.strip(), "linkedin_url": link.strip(),
                               "origin": "Rep input", "rep_status": "rep added", "rep_note": note, "created_at": db.now(),
                               "updated_at": db.now()})
    jobs.rep_fact(aid, 7, f"{name.strip()}{', ' + title.strip() if title.strip() else ''}{'. ' + note if note else ''}",
                  user=u, person_id=pid, topic=f"{name.strip()}: role")
    db.timeline(aid, "people", f"{u} added {name.strip()} to the buying committee")
    return back(f"/accounts/{aid}#people")


@app.post("/accounts/{aid}/questions/{qid}")
def question_update(request: Request, aid: int, qid: int, answer: str = Form(""), action: str = Form("answer")):
    q = db.q1("SELECT * FROM questions WHERE id=? AND account_id=?", (qid, aid))
    if not q:
        raise HTTPException(404)
    if action == "drop":
        db.update("questions", qid, {"status": "dropped"})
    elif answer.strip():
        db.update("questions", qid, {"status": "answered", "answer": answer.strip(), "answered_at": db.now(), "answered_by": user(request)})
        jobs.rep_fact(aid, 10, f"{q['text']} Answer: {answer.strip()}", user=user(request), topic="answered question")
    return back(f"/accounts/{aid}#questions")


@app.post("/accounts/{aid}/findings")
def finding_add(request: Request, aid: int, section: int = Form(...), statement: str = Form(...)):
    if statement.strip():
        jobs.rep_fact(aid, section, rules.clean_text(statement), user=user(request))
    return back(f"/accounts/{aid}#sec{section}")

# ---------------------------------------------------------------- alerts

@app.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request, show: str = "open"):
    where = "WHERE al.status='open'" if show == "open" else ""
    rows = db.q(f"""SELECT al.*, a.name account_name FROM alerts al JOIN accounts a ON a.id=al.account_id {where}
                    ORDER BY al.status='open' DESC, al.priority='High' DESC, al.id DESC""")
    return render(request, "alerts.html", alerts=rows, show=show)


@app.post("/alerts/{alid}/dismiss")
def alert_dismiss(request: Request, alid: int, reason: str = Form(...), other: str = Form("")):
    r = reason if reason != "other" else (other.strip() or "other")
    db.update("alerts", alid, {"status": "dismissed", "dismiss_reason": r, "dismissed_by": user(request), "dismissed_at": db.now()})
    a = db.get("alerts", alid)
    db.timeline(a["account_id"], "alert", f"Alert dismissed by {user(request)} ({r}): {a['what']}")
    return back(request.headers.get("referer") or "/alerts")


@app.post("/alerts/{alid}/prep")
def alert_prep(request: Request, alid: int):
    a = db.get("alerts", alid)
    db.update("alerts", alid, {"status": "acted"})
    return back(f"/prep/new?account_id={a['account_id']}")

# ---------------------------------------------------------------- knowledge, settings, costs

@app.get("/knowledge", response_class=HTMLResponse)
def knowledge_page(request: Request, f: str = "amasol.md"):
    if f not in knowledge.EDITABLE:
        f = "amasol.md"
    return render(request, "knowledge.html", files=knowledge.EDITABLE, current=f, text=knowledge.read(f))


@app.post("/knowledge")
def knowledge_save(request: Request, f: str = Form(...), text: str = Form(...)):
    try:
        knowledge.write(f, text.replace("\r\n", "\n"))
        request.session["flash"] = f"Saved {f}. It is used from the next research or brief."
    except ValueError as e:
        request.session["flash"] = f"Not saved: {e}"
    return back(f"/knowledge?f={f}")


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return render(request, "settings.html", s=digest.schedule(), next_run=db.setting("digest_next"), days=digest.DAYS,
                  digests=db.q("SELECT * FROM digests ORDER BY id DESC LIMIT 20"), apollo=bool(config.APOLLO_API_KEY),
                  smtp=bool(config.SMTP_HOST), tz=config.TIMEZONE, model=config.MODEL)


@app.post("/settings/digest")
def settings_digest(request: Request, day: str = Form(...), time: str = Form(...), enabled: str = Form("0"), to: str = Form("")):
    db.set_setting("digest_day", day if day in digest.DAYS else "Monday")
    db.set_setting("digest_time", time if len(time) == 5 else "07:00")
    db.set_setting("digest_enabled", "1" if enabled == "1" else "0")
    if to.strip():
        db.set_setting("digest_to", to.strip())
    db.set_setting("digest_next", digest.next_run().isoformat())
    return back("/settings")


@app.post("/digest/run")
def digest_run(request: Request):
    jobs.submit(digest.run_digest, user(request))
    request.session["flash"] = "Digest started: Find new runs on every Radar account, then the digest is sent. This takes a few minutes per account."
    return back("/settings")


@app.get("/digests/{did}", response_class=HTMLResponse)
def digest_view(did: int):
    d = db.get("digests", did)
    return HTMLResponse(Path(d["html_path"]).read_text(encoding="utf-8"))


@app.get("/costs", response_class=HTMLResponse)
def costs_page(request: Request):
    runs = db.q("""SELECT r.*, a.name account_name, (SELECT COUNT(*) FROM cost_log c WHERE c.run_id=r.id) calls,
                   (SELECT SUM(searches) FROM cost_log c WHERE c.run_id=r.id) searches
                   FROM runs r LEFT JOIN accounts a ON a.id=r.account_id ORDER BY r.id DESC LIMIT 200""")
    totals = db.q("SELECT kind, COUNT(*) n, SUM(cost_usd) usd, AVG(cost_usd) avg FROM runs GROUP BY kind")
    unattached = db.q1("SELECT SUM(usd) usd FROM cost_log WHERE run_id IS NULL") or {}
    return render(request, "costs.html", runs=runs, totals=totals, unattached=unattached.get("usd") or 0)


@app.get("/flash")
def flash(request: Request):
    return {"msg": request.session.pop("flash", None)}
