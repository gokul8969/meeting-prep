"""Deterministic rules from the requirements: source tiers, confidence, content rules.

The model proposes; these rules decide. Confidence is never taken from the model.
"""
import re
from datetime import date, datetime
from urllib.parse import urlparse

SECTIONS = {
    1: "Account summary",
    2: "Account snapshot",
    3: "Business situation and priorities",
    4: "IT environment and tech stack",
    5: "Sourcing model and providers",
    6: "Industry and regulation",
    7: "People and personas",
    8: "Opportunity view",
    9: "Signals and alerts timeline",
    10: "Interaction history",
    11: "Open questions",
    12: "Sources and confidence",
}

TYPES = ("Stated", "Inferred", "Rep input", "Unknown")
STATUSES = ("New", "Updated", "Conflicting", "Already known", "Superseded")
CONF_ORDER = {"High": 3, "Medium": 2, "Low": 1}

# ---------- content rules ----------

_BANNED = re.compile(r"detectab", re.I)  # amasol is fading out "Detectability": never shown


def banned(text: str) -> bool:
    return bool(text and _BANNED.search(text))


def clean_text(s):
    """Plain business English, never em dashes."""
    if not isinstance(s, str):
        return s
    s = re.sub(r"\s*—\s*", ", ", s)          # em dash
    s = re.sub(r"(\d)\s*–\s*(\d)", r"\1-\2", s)  # en dash in ranges
    s = re.sub(r"\s+–\s+", ", ", s)          # en dash used as a dash
    s = s.replace("–", "-").replace("―", ", ")
    s = re.sub(r",\s*,", ",", s)
    return s.strip()


def clean(o):
    if isinstance(o, str):
        return clean_text(o)
    if isinstance(o, list):
        return [clean(x) for x in o]
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    return o


# ---------- sources ----------

TIER4_HOSTS = ("reddit.com", "medium.com", "substack.com", "x.com", "twitter.com", "facebook.com",
               "quora.com", "blogspot.", "wordpress.com", "tumblr.com", "youtube.com", "tiktok.com",
               "instagram.com", "threads.net", "glassdoor.")
TIER3_HOSTS = ("linkedin.com", "indeed.", "stepstone.", "monster.", "naukri.com", "xing.com",
               "apollo.io", "rocketreach.co", "zoominfo.com", "crunchbase.com", "theorg.com",
               "greenhouse.io", "lever.co", "workday", "smartrecruiters.com", "jobs.", "careers.",
               "builtwith.com", "stackshare.io", "6sense.com", "signalhire.com", "lusha.com")
TIER1_HINTS = ("gov", "regulator", "europa.eu", "sec.gov", "companieshouse", "handelsregister",
               "unternehmensregister", "bafin", "fca.org.uk", "ecb.europa", "ted.europa.eu",
               "contractsfinder", "gem.gov.in", "mca.gov.in", "eba.europa", "esma.europa")


def host(url: str) -> str:
    try:
        h = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return h[4:] if h.startswith("www.") else h


def registrable(h: str) -> str:
    parts = h.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "gov", "ac", "net") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def domain_of(website: str) -> str:
    if not website:
        return ""
    w = website.strip()
    if "://" not in w:
        w = "https://" + w
    return registrable(host(w))


def tier_for(url: str, suggested: int | None, company_domains: set[str]) -> int:
    h = host(url)
    reg = registrable(h)
    if reg in company_domains:
        # the company's own careers pages are job postings (tier 3), not company statements
        if any(k in h or k in url.lower() for k in ("career", "jobs", "job", "recruit")):
            return 3
        return 1
    if any(t in h for t in TIER4_HOSTS):
        return 4
    if any(t in h for t in TIER3_HOSTS) or "/jobs/" in url.lower() or "/careers/" in url.lower():
        return 3
    if any(t in h for t in TIER1_HINTS) or h.endswith(".gov") or ".gov." in h:
        return 1
    try:
        s = int(suggested) if suggested is not None else 2
    except (TypeError, ValueError):
        s = 2
    # a third-party site can only be tier 1 if it is a regulator or register (handled above)
    return min(max(s, 2), 4)


def parse_date(s):
    if not s:
        return None
    if isinstance(s, (date, datetime)):
        return s if isinstance(s, date) else s.date()
    s = str(s).strip()
    for cand in (s, s[:10], s[:7]):
        for fmt in ("%Y-%m-%d", "%Y-%m", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    m = re.search(r"(\d+)\s+(day|week|month|year)s?\s+ago", s, re.I)
    if m:
        n, unit = int(m[1]), m[2].lower()
        days = n * {"day": 1, "week": 7, "month": 30, "year": 365}[unit]
        return date.fromordinal(date.today().toordinal() - days)
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.search(r"(20\d{2}|19\d{2})", s)
    if m:
        return date(int(m[1]), 1, 1)
    return None


def months_old(d, ref=None) -> float | None:
    d = parse_date(d)
    if not d:
        return None
    ref = ref or date.today()
    return (ref - d).days / 30.44


def newest_date(sources):
    ds = [parse_date(s.get("published")) for s in sources]
    ds = [d for d in ds if d]
    return max(ds).isoformat() if ds else None


def lower(conf: str) -> str:
    return {"High": "Medium", "Medium": "Low", "Low": "Low"}.get(conf, "Low")


def confidence(ftype: str, sources: list[dict], *, is_people=False, apollo_used=True) -> str:
    """Section 4.5 rules."""
    if ftype == "Rep input":
        return "High"
    if ftype == "Unknown" or not sources:
        return "Low"
    tiers = [int(s.get("tier") or 4) for s in sources]
    hosts = {registrable(host(s.get("url", ""))) for s in sources if int(s.get("tier") or 4) in (2, 3)}
    age = months_old(newest_date(sources))
    if all(t == 4 for t in tiers):
        conf = "Low"
    elif ftype == "Inferred":
        strong = len([t for t in tiers if t <= 3]) >= 3
        conf = "Medium" if strong else "Low"
    else:
        if 1 in tiers and (age is None or age <= 12):
            conf = "High"
        elif len(hosts) >= 2 and age is not None and age <= 12:
            conf = "High"
        elif any(t <= 3 for t in tiers):
            conf = "Medium"
        else:
            conf = "Low"
        if age is None and conf == "High" and 1 not in tiers:
            conf = "Medium"
        if age is not None and 12 < age <= 24 and conf == "High":
            conf = "Medium"
        if age is not None and age > 24:
            conf = "Low"
    if is_people and not apollo_used:
        conf = lower(conf)
    return conf


def out_of_date(f) -> bool:
    age = months_old(f.get("date_published"))
    return age is not None and age > 24


def worst(confs):
    confs = [c for c in confs if c in CONF_ORDER]
    if not confs:
        return None
    return min(confs, key=lambda c: CONF_ORDER[c])
