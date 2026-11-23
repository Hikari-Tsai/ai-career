"""Hosted ATS boards with public JSON feeds: Greenhouse, Lever, Ashby, SmartRecruiters.

params:
  greenhouse:      {board}            boards-api.greenhouse.io/v1/boards/{board}
  lever:           {site, region?}    api.lever.co (or api.eu.lever.co)
  ashby:           {org}              api.ashbyhq.com/posting-api/job-board/{org}
  smartrecruiters: {company}          api.smartrecruiters.com/v1/companies/{company}
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..http import get_json
from ..models import Job, html_to_text


def greenhouse(company: dict):
    board = company["params"]["board"]
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"})
    for j in data.get("jobs", []):
        yield Job(
            company_id=company["id"],
            source_id=str(j["id"]),
            title=j.get("title", ""),
            url=j.get("absolute_url", ""),
            location=(j.get("location") or {}).get("name", ""),
            department=", ".join(d.get("name", "") for d in j.get("departments", [])),
            posted_at=(j.get("updated_at") or "")[:10],
            description=html_to_text(html_to_text(j.get("content"))),  # content is HTML-escaped HTML
        )


def lever(company: dict):
    p = company["params"]
    host = "api.eu.lever.co" if p.get("region") == "eu" else "api.lever.co"
    for j in get_json(f"https://{host}/v0/postings/{p['site']}", params={"mode": "json"}):
        cats = j.get("categories", {})
        locs = cats.get("allLocations") or [cats.get("location", "")]
        yield Job(
            company_id=company["id"],
            source_id=j["id"],
            title=j.get("text", ""),
            url=j.get("hostedUrl", ""),
            location=" / ".join(l for l in locs if l),
            department=cats.get("team", "") or cats.get("department", ""),
            posted_at=datetime.fromtimestamp(j.get("createdAt", 0) / 1000, timezone.utc).date().isoformat(),
            description=(j.get("descriptionPlain") or "") + "\n" + "\n".join(
                f"{l.get('text','')}: {html_to_text(l.get('content'))}" for l in j.get("lists", [])
            ),
        )


def ashby(company: dict):
    org = company["params"]["org"]
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}", params={"includeCompensation": "false"})
    for j in data.get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", [])]
        yield Job(
            company_id=company["id"],
            source_id=j["id"],
            title=j.get("title", ""),
            url=j.get("jobUrl", ""),
            location=" / ".join(l for l in locs if l),
            department=j.get("department", "") or j.get("team", ""),
            posted_at=(j.get("publishedAt") or "")[:10],
            description=j.get("descriptionPlain", ""),
        )


def smartrecruiters(company: dict):
    cid = company["params"]["company"]
    base = f"https://api.smartrecruiters.com/v1/companies/{cid}/postings"
    offset = 0
    while True:
        data = get_json(base, params={"country": "tw", "limit": 100, "offset": offset})
        for j in data.get("content", []):
            loc = j.get("location", {})
            yield Job(
                company_id=company["id"],
                source_id=j["id"],
                title=j.get("name", ""),
                url=f"https://jobs.smartrecruiters.com/{cid}/{j['id']}",
                location=", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
                department=(j.get("department") or {}).get("label", ""),
                posted_at=(j.get("releasedDate") or "")[:10],
                extra={"ref": j.get("ref", "")},
            )
        offset += 100
        if offset >= data.get("totalFound", 0):
            break


def smartrecruiters_detail(company: dict, job: Job) -> dict:
    d = get_json(job.extra["ref"]) if job.extra.get("ref") else {}
    secs = (d.get("jobAd") or {}).get("sections", {})
    return {"description": " ".join(html_to_text((secs.get(k) or {}).get("text")) for k in secs)}
