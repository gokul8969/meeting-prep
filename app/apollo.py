"""Apollo (optional). Every call fails soft: research must finish without it (requirements 4.6).

Which endpoints the current Apollo plan includes is checked at run time; anything refused is
recorded in the run's Apollo note so the team can see what the plan allows.
"""
import logging

import httpx

from . import config

log = logging.getLogger("mp.apollo")


class Apollo:
    def __init__(self):
        self.key = config.APOLLO_API_KEY
        self.notes: list[str] = []
        self.used = False

    @property
    def available(self) -> bool:
        return bool(self.key)

    def _req(self, method, path, **kw):
        if not self.key:
            return None
        try:
            r = httpx.request(method, config.APOLLO_BASE_URL + path, timeout=30,
                              headers={"X-Api-Key": self.key, "Content-Type": "application/json",
                                       "Cache-Control": "no-cache"}, **kw)
        except httpx.HTTPError as e:
            self.notes.append(f"{path}: {type(e).__name__}")
            return None
        if r.status_code >= 400:
            self.notes.append(f"{path}: HTTP {r.status_code} (not in plan or failed)")
            return None
        self.used = True
        try:
            return r.json()
        except ValueError:
            return None

    def organization(self, domain: str) -> dict | None:
        if not domain:
            return None
        d = self._req("GET", "/organizations/enrich", params={"domain": domain})
        return (d or {}).get("organization")

    def job_postings(self, org_id: str) -> list[dict]:
        if not org_id:
            return []
        d = self._req("GET", f"/organizations/{org_id}/job_postings", params={"per_page": 50})
        return (d or {}).get("organization_job_postings", []) or []

    def people_search(self, domain: str, *, departments=None, seniorities=None, per_page=25) -> list[dict]:
        body = {"q_organization_domains_list": [domain], "per_page": per_page, "page": 1}
        if departments:
            body["person_departments"] = departments
        if seniorities:
            body["person_seniorities"] = seniorities
        d = self._req("POST", "/mixed_people/search", json=body)
        if d is None:
            d = self._req("POST", "/mixed_people/api_search", json=body)
        return (d or {}).get("people", []) or []

    def person_match(self, name: str, *, domain=None, org_name=None, linkedin_url=None) -> dict | None:
        first, _, last = name.strip().partition(" ")
        body = {"first_name": first, "last_name": last, "reveal_personal_emails": False}
        if domain:
            body["domain"] = domain
        if org_name:
            body["organization_name"] = org_name
        if linkedin_url:
            body["linkedin_url"] = linkedin_url
        d = self._req("POST", "/people/match", json=body)
        return (d or {}).get("person")

    def news(self, org_id: str) -> list[dict]:
        if not org_id:
            return []
        d = self._req("POST", "/news_articles/search", json={"organization_ids": [org_id], "per_page": 20})
        return (d or {}).get("news_articles", []) or []


def org_summary(org: dict) -> dict:
    """Only the fields the profile needs."""
    if not org:
        return {}
    techs = org.get("current_technologies") or []
    return {
        "id": org.get("id"),
        "name": org.get("name"),
        "website": org.get("website_url"),
        "industry": org.get("industry"),
        "employees": org.get("estimated_num_employees"),
        "country": org.get("country"),
        "city": org.get("city"),
        "linkedin": org.get("linkedin_url"),
        "technologies": [f"{t.get('name')} ({t.get('category')})" if t.get("category") else t.get("name")
                         for t in techs if t.get("name")] or org.get("technology_names") or [],
    }


def person_summary(p: dict) -> dict:
    """Name, role, employer, public background and source. Nothing else is stored."""
    if not p:
        return {}
    hist = []
    for e in (p.get("employment_history") or [])[:6]:
        hist.append(" ".join(x for x in [e.get("title") or "", "at", e.get("organization_name") or "",
                                         f"({e.get('start_date') or '?'} to {e.get('end_date') or 'now'})"] if x))
    return {
        "name": p.get("name") or " ".join(filter(None, [p.get("first_name"), p.get("last_name")])),
        "title": p.get("title"),
        "employer": (p.get("organization") or {}).get("name"),
        "linkedin_url": p.get("linkedin_url"),
        "headline": p.get("headline"),
        "seniority": p.get("seniority"),
        "departments": p.get("departments"),
        "history": hist,
    }
