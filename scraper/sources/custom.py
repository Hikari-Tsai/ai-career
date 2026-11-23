"""Smaller hosted ATSs and company-specific career APIs.

manatal: {slug}        api.manatal.com career-page API (KKCompany)
jazzhr:  {subdomain}   {subdomain}.applytojob.com HTML listing (SHOPLINE)
esun:    {}            E.SUN Bank recruit.esunbank.com.tw JSON API
"""
from __future__ import annotations

import re
from datetime import datetime

from ..http import get_json, get_text, request
from ..models import Job, html_to_text


def manatal(company: dict):
    slug = company["params"]["slug"]
    url = f"https://api.manatal.com/open/v3/career-page/{slug}/jobs/?page_size=100"
    while url:
        data = get_json(url)
        for j in data.get("results", []):
            yield Job(
                company_id=company["id"],
                source_id=str(j.get("hash") or j["id"]),
                title=j.get("position_name", ""),
                url=f"https://www.careers-page.com/{slug}/job/{j.get('hash')}",
                location=j.get("location_display", "") or ", ".join(filter(None, (j.get("city"), j.get("country")))),
                department=j.get("organization_name", ""),
                description=html_to_text(j.get("description")),
            )
        url = data.get("next")


_JAZZ_ROW = re.compile(
    r"<li class=['\"]list-group-item['\"]>.*?href=['\"](?P<url>https://[^'\"]+/apply/(?P<id>[^/'\"]+)/[^'\"]*)['\"][^>]*>(?P<title>.*?)</a>"
    r".*?fa-map-marker['\"]></i>(?P<loc>[^<]*)",
    re.S,
)
_JAZZ_DESC = re.compile(r"<div[^>]*id=['\"]job-description['\"][^>]*>(.*?)(?:<form|id=['\"]resumator-|<footer)", re.S)


def jazzhr(company: dict):
    sub = company["params"]["subdomain"]
    page = get_text(f"https://{sub}.applytojob.com/apply")
    for m in _JAZZ_ROW.finditer(page):
        yield Job(
            company_id=company["id"],
            source_id=m["id"],
            title=html_to_text(m["title"]),
            url=m["url"],
            location=html_to_text(m["loc"]),
        )


def jazzhr_detail(company: dict, job: Job) -> dict:
    m = _JAZZ_DESC.search(get_text(job.url))
    return {"description": html_to_text(m.group(1)) if m else ""}


_ESUN = "https://recruit.esunbank.com.tw/ahr_recruit_api/StaffAcquisition"


def esun(company: dict):
    cats = request("POST", f"{_ESUN}/GetEliteItem").json().get("ReturnData") or []
    for cat in cats:
        rows = request("POST", f"{_ESUN}/GetEliteItemDetail", params={"eliteItemID": cat["EliteItemID"]}).json().get("ReturnData") or []
        for r in rows:
            loc = r.get("WorkLocation") or ""
            if "海外" in loc:
                continue
            posted = ""
            try:
                posted = datetime.strptime(r.get("CreatedDateTime", ""), "%m/%d/%Y %H:%M:%S").date().isoformat()
            except ValueError:
                pass
            yield Job(
                company_id=company["id"],
                source_id=str(r["CapacitySettingID"]),
                title=r.get("CapacitySettingName", ""),
                url="https://recruit.esunbank.com.tw/Recruit/StaffAcquisition",
                location=f"台灣 {loc}".strip(),
                department=r.get("EliteItemName", "") or cat.get("EliteItemName", ""),
                posted_at=posted,
                description=html_to_text((r.get("Descrption") or "") + "\n" + (r.get("Condition") or "")),
            )


# ---------------------------------------------------------------- Sea (Shopee / Garena) workatsea ATS
def sea(company: dict):
    """params: {region_ids: 28, url_base: https://careers.shopee.tw/job-detail/}"""
    p = company["params"]
    offset = 0
    while True:
        data = get_json(
            "https://ats.workatsea.com/ats/api/v1/user/job/list/",
            params={"limit": 50, "offset": offset, "region_ids": p.get("region_ids", 28)},
        )["data"]
        rows = data.get("job_list") or []
        for j in rows:
            yield Job(
                company_id=company["id"],
                source_id=str(j["id"]),
                title=j.get("job_name", ""),
                url=f"{p.get('url_base', 'https://careers.shopee.tw/job-detail/')}{j['id']}",
                location="Taipei, Taiwan",
                description=html_to_text(
                    " ".join(j.get(k) or "" for k in ("job_description", "requirements", "sub_team_description"))
                ),
            )
        offset += len(rows)
        if not rows or offset >= data.get("total_count", 0):
            break


# ---------------------------------------------------------------- LINE (Gatsby static page-data)
_TW_CITIES = {"Taipei", "Taichung", "Kaohsiung", "Hsinchu"}


def line(company: dict):
    data = get_json("https://careers.linecorp.com/page-data/jobs/page-data.json")
    for edge in data["result"]["data"]["allStrapiJobs"]["edges"]:
        n = edge["node"]
        cities = [c.get("name", "") for c in n.get("cities") or []]
        if not _TW_CITIES.intersection(cities):
            continue
        yield Job(
            company_id=company["id"],
            source_id=str(n["strapiId"]),
            title=n.get("title_en") or n.get("title", ""),
            url=f"https://careers.linecorp.com/jobs/{n['strapiId']}/",
            location=", ".join(cities) + ", Taiwan",
            department=", ".join(c.get("name", "") for c in n.get("companies") or []),
            posted_at=(n.get("start_date") or "")[:10],
            extra={"sid": n["strapiId"]},
        )


def line_detail(company: dict, job: Job) -> dict:
    d = get_json(f"https://careers.linecorp.com/page-data/jobs/{job.extra['sid']}/page-data.json")
    return {"description": html_to_text(d["result"]["data"]["strapiJobs"].get("content"))}


# ---------------------------------------------------------------- Yourator (job board, many companies)
_YOURATOR = "https://www.yourator.co"
_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.S)


def yourator(company: dict):
    """Keyword search across the board. Each job belongs to its own (startup) company, encoded in
    company_id as "<entry id>:<company slug>"; extra["company"] carries the name and page URL."""
    from ..models import is_taiwan

    seen: set[int] = set()
    for term in company["params"]["terms"]:
        for page in range(1, company["params"].get("max_pages", 15) + 1):
            data = get_json(f"{_YOURATOR}/api/v4/jobs", params={"term[]": term, "page": page})["payload"]
            for j in data.get("jobs") or []:
                if j["id"] in seen or not is_taiwan(j.get("location", "")):
                    continue
                seen.add(j["id"])
                c = j.get("company") or {}
                slug = c.get("enName") or str(j.get("companyId"))
                yield Job(
                    company_id=f"{company['id']}:{slug}",
                    source_id=str(j["id"]),
                    title=html_to_text(j.get("name", "")),
                    url=_YOURATOR + j["path"],
                    location=f"台灣 {j.get('location', '')}",
                    extra={"company": {"name": c.get("brand", slug), "careers_url": _YOURATOR + c.get("path", "")},
                           "tags": j.get("tags") or []},
                )
            if not data.get("hasMore"):
                break


def yourator_detail(company: dict, job: Job) -> dict:
    import json as _json

    for block in _LD_JSON.findall(get_text(job.url)):
        try:
            d = _json.loads(block)
        except ValueError:
            continue
        if d.get("@type") == "JobPosting":
            return {
                "description": html_to_text(d.get("description")) + " " + " ".join(job.extra.get("tags", [])),
                "posted_at": str(d.get("datePosted") or "")[:10],
            }
    return {}
