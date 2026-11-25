"""Weekly digest (requirements 6.6): Find new on every Radar account, then one HTML email."""
import html
import logging
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from . import config, db, jobs, research

log = logging.getLogger("mp.digest")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_running = threading.Lock()


def schedule():
    return {"day": db.setting("digest_day", "Monday"), "time": db.setting("digest_time", "07:00"),
            "enabled": db.setting("digest_enabled", "1") == "1", "to": db.setting("digest_to", config.DIGEST_TO)}


def next_run(now=None):
    s = schedule()
    tz = ZoneInfo(config.TIMEZONE)
    now = now or datetime.now(tz)
    h, m = (int(x) for x in s["time"].split(":"))
    target = DAYS.index(s["day"]) if s["day"] in DAYS else 0
    cand = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=(target - now.weekday()) % 7)
    if cand <= now:
        cand += timedelta(days=7)
    return cand


def run_digest(user="digest"):
    if not _running.acquire(blocking=False):
        return None
    try:
        started = db.now()
        accounts = db.q("SELECT * FROM accounts WHERE in_radar=1 ORDER BY name")
        results = []
        for acc in accounts:
            run_id = research.new_run("digest", acc["id"], None, user)
            res = jobs.find_new_job(acc["id"], user, run_id=run_id)
            run = db.get("runs", run_id)
            results.append({"account": acc, "run": run, "res": res})
        html_body = build_html(results, started)
        path = config.OUTBOX_DIR / f"digest-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        path.write_text(html_body, encoding="utf-8")
        to = schedule()["to"]
        delivery = send(to, "Trusted Advisor Radar: weekly digest", html_body)
        did = db.insert("digests", {"run_at": started, "status": "done", "sent_to": to, "delivery": delivery,
                                    "html_path": str(path), "summary": f"{len(accounts)} accounts"})
        return did
    finally:
        _running.release()


def send(to, subject, body) -> str:
    if not config.SMTP_HOST:
        return "saved to outbox (no SMTP configured)"
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, config.SMTP_FROM or config.SMTP_USER, to
    msg.attach(MIMEText(body, "html", "utf-8"))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=60) as s:
            s.starttls()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.sendmail(msg["From"], [to], msg.as_string())
        return f"emailed to {to}"
    except Exception as e:
        log.error("digest email failed: %s", e)
        return f"email failed ({e}); saved to outbox"


def build_html(results, started):
    e = html.escape
    base = config.PUBLIC_URL.rstrip("/")
    high, normal, changed, quiet, failed = [], [], [], [], []
    for r in results:
        acc, run = r["account"], r["run"]
        if run["status"] == "failed":
            failed.append((acc, run.get("error") or "unknown error"))
            continue
        alerts = db.q("SELECT * FROM alerts WHERE run_id=? AND status='open'", (run["id"],))
        for a in alerts:
            (high if a["priority"] == "High" else normal).append((acc, a))
        labels = r["res"].get("labels", {})
        n = labels.get("New", 0) + labels.get("Updated", 0) + labels.get("Conflicting", 0)
        if n:
            summ = db.jload(db.get("accounts", acc["id"]).get("summary_json"), {}) or {}
            lines = [c["text"] for c in summ.get("changed", [])][:4]
            changed.append((acc, labels, lines, run))
        else:
            quiet.append(acc)
        if run["status"] == "partial":
            failed.append((acc, "some research steps failed: " + (run.get("error") or "")))

    def alert_li(acc, a):
        src = f' <a href="{e(a["source_url"])}">{e(a["source_name"] or "source")}</a>' if a.get("source_url") else ""
        return (f'<li><b>{e(acc["name"])}</b>: {e(a["what"])} ({e(a.get("event_date") or "")}){src}<br>'
                f'<span style="color:#555">Why it matters ({e(a.get("product") or "")}): {e(a["why"])}. Suggested: {e(a["angle"])}</span> '
                f'<a href="{base}/accounts/{acc["id"]}">Open account</a></li>')

    parts = [f"""<html><body style="font-family:Arial,Helvetica,sans-serif;color:#1d2433;max-width:760px">
<h2 style="margin-bottom:4px">Trusted Advisor Radar: weekly digest</h2>
<p style="color:#555;margin-top:0">Run started {e(started[:16].replace('T', ' '))} UTC. {len(results)} accounts checked with Find new.</p>"""]
    parts.append("<h3>High priority alerts</h3>" + ("<ul>" + "".join(alert_li(*x) for x in high) + "</ul>" if high else "<p>None this week.</p>"))
    if normal:
        parts.append("<h3>Other alerts</h3><ul>" + "".join(alert_li(*x) for x in normal) + "</ul>")
    parts.append("<h3>What changed, by account</h3>")
    if changed:
        for acc, labels, lines, run in changed:
            lab = ", ".join(f"{v} {k.lower()}" for k, v in labels.items() if v and k != "Already known")
            parts.append(f'<p><b><a href="{base}/accounts/{acc["id"]}">{e(acc["name"])}</a></b> ({e(lab)})</p>'
                         + ("<ul>" + "".join(f"<li>{e(l)}</li>" for l in lines) + "</ul>" if lines else ""))
    else:
        parts.append("<p>No changes found.</p>")
    parts.append("<h3>No changes</h3>" + (", ".join(f'<a href="{base}/accounts/{a["id"]}">{e(a["name"])}</a>' for a in quiet) or "<p>None.</p>"))
    parts.append("<h3>Research problems</h3>" + ("<ul>" + "".join(f"<li>{e(a['name'])}: {e(err[:300])}</li>" for a, err in failed) + "</ul>" if failed else "<p>None.</p>"))
    parts.append("<p style='color:#888;font-size:12px'>Generated by the meeting prep tool. Nothing here has been sent to any customer.</p></body></html>")
    return "\n".join(parts)


def scheduler_loop():
    """Checks once a minute whether the configured weekly slot has arrived."""
    while True:
        try:
            s = schedule()
            if s["enabled"]:
                tz = ZoneInfo(config.TIMEZONE)
                now = datetime.now(tz)
                due = db.setting("digest_next")
                if not due:
                    db.set_setting("digest_next", next_run(now).isoformat())
                elif now >= datetime.fromisoformat(due):
                    db.set_setting("digest_next", next_run(now + timedelta(minutes=1)).isoformat())
                    threading.Thread(target=run_digest, daemon=True).start()
        except Exception:
            log.exception("scheduler error")
        time.sleep(60)


def start():
    threading.Thread(target=scheduler_loop, daemon=True, name="digest-scheduler").start()
