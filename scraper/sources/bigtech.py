"""Custom career-site APIs of large tech companies.

google:     careers.google.com results page (embedded AF_initDataCallback JSON)
amazon:     amazon.jobs search.json
eightfold:  Eightfold-hosted career sites (Microsoft, Qualcomm, ...); params {host, domain, api?}
apple:      jobs.apple.com search API
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ..http import get_json, get_text, post_json
from ..models import Job, html_to_text

# ---------------------------------------------------------------- Google
_G_DS1 = re.compile(r"AF_initDataCallback\(\{key: 'ds:1', hash: '\d+', data:(.*?), sideChannel: \{\}\}\);</script>", re.S)


def google(company: dict):
    seen = set()
    for page in range(1, 40):
        html = get_text(
            "https://www.google.com/about/careers/applications/jobs/results",
            params={"location": "Taiwan", "page": page},
        )
        m = _G_DS1.search(html)
        if not m:
            break
        data = json.loads(m.group(1))
        rows = data[0] or []
        for r in rows:
            if r[0] in seen:
                continue
            seen.add(r[0])
            desc = " ".join(html_to_text((r[i] or [None, ""])[1]) for i in (10, 3, 4, 19) if isinstance(r[i], list))
            ts = (r[12] or [0])[0] if len(r) > 12 else 0
            yield Job(
                company_id=company["id"],
                source_id=r[0],
                title=r[1],
                url=f"https://www.google.com/about/careers/applications/jobs/results/{r[0]}",
                location=" / ".join(loc[0] for loc in (r[9] or [])),
                posted_at=datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else "",
                description=desc,
            )
        if len(rows) < (data[3] or 20):
            break


# ---------------------------------------------------------------- Amazon
def amazon(company: dict):
    offset = 0
    while True:
        data = get_json(
            "https://www.amazon.jobs/en/search.json",
            params={"normalized_country_code[]": "TWN", "result_limit": 100, "offset": offset, "sort": "recent"},
        )
        for j in data.get("jobs", []):
            yield Job(
                company_id=company["id"],
                source_id=j.get("id_icims") or j["id"],
                title=j.get("title", ""),
                url="https://www.amazon.jobs" + j.get("job_path", ""),
                location=j.get("location", ""),
                department=j.get("business_category", "") or j.get("job_category", ""),
                posted_at=_parse_date(j.get("posted_date", "")),
                description=html_to_text(
                    " ".join(j.get(k) or "" for k in ("description", "basic_qualifications", "preferred_qualifications"))
                ),
            )
        offset += 100
        if offset >= data.get("hits", 0):
            break


def _parse_date(s: str) -> str:
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return ""


# ---------------------------------------------------------------- Eightfold
def eightfold(company: dict):
    """Eightfold PCS API. Microsoft uses /api/pcsx/search; older tenants use /api/apply/v2/jobs."""
    p = company["params"]
    host, domain = p["host"], p["domain"]
    pcsx = p.get("api", "pcsx") == "pcsx"
    start = 0
    while True:
        if pcsx:
            data = get_json(f"https://{host}/api/pcsx/search", params={"domain": domain, "query": "", "location": "Taiwan", "start": start})["data"]
            rows, total = data.get("positions", []), data.get("count", 0)
        else:
            data = get_json(f"https://{host}/api/apply/v2/jobs", params={"domain": domain, "location": "Taiwan", "start": start, "num": 10})
            rows, total = data.get("positions", []), data.get("count", 0)
        if not rows:
            break
        for r in rows:
            locs = r.get("locations") or [r.get("location", "")]
            ts = r.get("postedTs") or r.get("t_create") or 0
            url = r.get("canonicalPositionUrl") or r.get("positionUrl") or f"https://{host}/careers/job/{r['id']}"
            if url.startswith("/"):
                url = f"https://{host}{url}"
            yield Job(
                company_id=company["id"],
                source_id=str(r.get("displayJobId") or r.get("display_job_id") or r["id"]),
                title=r.get("name", ""),
                url=url,
                location=" / ".join(locs),
                department=r.get("department", ""),
                posted_at=datetime.fromtimestamp(ts, timezone.utc).date().isoformat() if ts else "",
                description=html_to_text(r.get("job_description", "")),
                extra={"pid": r["id"]},
            )
        start += len(rows)
        if start >= total:
            break


def eightfold_detail(company: dict, job: Job) -> dict:
    p = company["params"]
    if p.get("api", "pcsx") == "pcsx":
        d = get_json(f"https://{p['host']}/api/pcsx/position_details", params={"position_id": job.extra["pid"], "domain": p["domain"]})
        d = d.get("data", d)
    else:
        d = get_json(f"https://{p['host']}/api/apply/v2/jobs/{job.extra['pid']}", params={"domain": p["domain"]})
    return {"description": html_to_text(d.get("jobDescription") or d.get("job_description") or "")}
