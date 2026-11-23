"""More hosted platforms / custom APIs used by global companies.

jibe:      iCIMS Jibe career sites, params {host}                       (AMD, Keysight)
oracle:    Oracle HCM Candidate Experience, params {host, site, slug}   (Dell, Texas Instruments)
radancy:   Radancy TalentBrew search fragments, params {host}           (Synopsys, Arm)
apple:     jobs.apple.com server-rendered search state
asml:      asml.com Sitecore Search (public short-lived token)
ibm:       IBM careers search API
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from ..http import get_json, get_text, post_json
from ..models import Job, html_to_text, is_taiwan


# ---------------------------------------------------------------- Jibe
def jibe(company: dict):
    host = company["params"]["host"]
    page, total = 1, None
    while total is None or (page - 1) * 100 < total:
        data = get_json(f"https://{host}/api/jobs", params={"country": "Taiwan", "limit": 100, "page": page})
        total = data.get("totalCount", 0)
        rows = data.get("jobs") or []
        if not rows:
            break
        for r in rows:
            d = r.get("data", r)
            yield Job(
                company_id=company["id"],
                source_id=str(d.get("req_id") or d.get("slug")),
                title=d.get("title", ""),
                url=f"https://{host}/careers-home/jobs/{d.get('slug') or d.get('req_id')}",
                location=d.get("full_location") or ", ".join(filter(None, (d.get("city"), "Taiwan"))),
                department=", ".join(c.get("name", "") if isinstance(c, dict) else str(c) for c in d.get("categories") or []),
                posted_at=str(d.get("posted_date") or "")[:10],
                description=html_to_text(" ".join(d.get(k) or "" for k in ("description", "responsibilities", "qualifications"))),
            )
        page += 1


# ---------------------------------------------------------------- Oracle HCM
def oracle(company: dict):
    p = company["params"]
    base = f"https://{p['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"

    def query(extra: str, offset: int = 0):
        finder = f"findReqs;siteNumber={p['site']},facetsList=LOCATIONS,limit=25,offset={offset},sortBy=POSTING_DATES_DESC,{extra}"
        return get_json(base, params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder})["items"][0]

    tw_id = p.get("taiwan_location_id")
    if not tw_id:
        facets = query("keyword=Taiwan").get("locationsFacet") or []
        tw_id = next((f["Id"] for f in facets if f.get("Name") == "Taiwan"), None)
    if not tw_id:
        return
    offset, total = 0, None
    while total is None or offset < total:
        page = query(f"selectedLocationsFacet={tw_id}", offset)
        total = page.get("TotalJobsCount", 0)
        rows = page.get("requisitionList") or []
        if not rows:
            break
        for r in rows:
            locs = [r.get("PrimaryLocation", "")] + [s.get("Name", "") for s in r.get("secondaryLocations") or []]
            yield Job(
                company_id=company["id"],
                source_id=str(r["Id"]),
                title=r.get("Title", ""),
                url=f"https://{p['host']}/hcmUI/CandidateExperience/en/sites/{p['slug']}/job/{r['Id']}",
                location=" / ".join(l for l in locs if l and is_taiwan(l)) or "Taiwan",
                posted_at=str(r.get("PostedDate") or "")[:10],
                description=html_to_text(
                    " ".join(r.get(k) or "" for k in ("ShortDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
                ),
            )
        offset += len(rows)


def oracle_detail(company: dict, job: Job) -> dict:
    p = company["params"]
    d = get_json(
        f"https://{p['host']}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
        params={"onlyData": "true", "finder": f'ById;Id="{job.source_id}",siteNumber={p["site"]}'},
    )["items"]
    return {"description": html_to_text((d[0] if d else {}).get("ExternalDescriptionStr"))}


# ---------------------------------------------------------------- Radancy / TalentBrew
_RAD_LINK = re.compile(r'<a[^>]+href="(/job/[^"]+?/(\d+))"[^>]*>(.*?)</a>', re.S)
_RAD_LOC = re.compile(r'class="[^"]*job-location[^"]*"[^>]*>(.*?)</span>', re.S)
_RAD_DATE = re.compile(r'class="[^"]*job-date-posted[^"]*"[^>]*>\s*(\d{1,2})/(\d{1,2})/(\d{4})', re.S)
_RAD_TITLE = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S)


def radancy(company: dict):
    host = company["params"]["host"]
    seen: set[str] = set()
    for page in range(1, 40):
        data = get_json(
            f"https://{host}/search-jobs/results",
            params={"ActiveFacetID": 0, "CurrentPage": page, "RecordsPerPage": 50, "Keywords": "Taiwan",
                    "SearchResultsModuleName": "Search Results", "SearchFiltersModuleName": "Search Filters",
                    "SearchType": 5, "IsPagination": "True"},
        )
        frag = data.get("results") or ""
        links = list(_RAD_LINK.finditer(frag))
        new = 0
        for i, m in enumerate(links):
            path, jid = m.group(1), m.group(2)
            if jid in seen:
                continue
            seen.add(jid)
            new += 1
            block = frag[m.start(): links[i + 1].start() if i + 1 < len(links) else len(frag)]
            loc = html_to_text((_RAD_LOC.search(block) or [None, ""])[1]) or path.split("/")[2].replace("-", " ")
            if not is_taiwan(loc):
                continue
            title = html_to_text((_RAD_TITLE.search(block) or [None, m.group(3)])[1])
            d = _RAD_DATE.search(block)
            yield Job(
                company_id=company["id"],
                source_id=jid,
                title=title,
                url=f"https://{host}{path}",
                location=loc,
                posted_at=f"{d.group(3)}-{int(d.group(1)):02d}-{int(d.group(2)):02d}" if d else "",
            )
        if not new:
            break


_RAD_DESC = re.compile(r'class="[^"]*(?:ats-description|job-description|jd-description)[^"]*"[^>]*>(.*?)</(?:div|section)>\s*</', re.S)


def radancy_detail(company: dict, job: Job) -> dict:
    page = get_text(job.url)
    m = _RAD_DESC.search(page)
    if m:
        return {"description": html_to_text(m.group(1))}
    ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S)
    try:
        return {"description": html_to_text(json.loads(ld.group(1)).get("description"))} if ld else {}
    except (json.JSONDecodeError, AttributeError):
        return {}


# ---------------------------------------------------------------- Apple
_APPLE_STATE = re.compile(r'__staticRouterHydrationData = JSON\.parse\("(.*?)"\);', re.S)


def apple(company: dict):
    seen: set[str] = set()
    for page in range(1, 30):
        html = get_text("https://jobs.apple.com/en-us/search", params={"location": "taiwan-TWN", "page": page})
        m = _APPLE_STATE.search(html)
        if not m:
            break
        state = json.loads(json.loads(f'"{m.group(1)}"'))
        search = state.get("loaderData", {}).get("search", {})
        rows = search.get("searchResults") or []
        new = 0
        for r in rows:
            pid = str(r.get("positionId") or r.get("reqId"))
            if pid in seen:
                continue
            seen.add(pid)
            new += 1
            yield Job(
                company_id=company["id"],
                source_id=pid,
                title=r.get("postingTitle", ""),
                url=f"https://jobs.apple.com/en-us/details/{pid}/{r.get('transformedPostingTitle', '')}",
                location=" / ".join(l.get("name", "") for l in r.get("locations") or []) or "Taiwan",
                department=(r.get("team") or {}).get("teamName", "") if isinstance(r.get("team"), dict) else "",
                posted_at=str(r.get("postDateInGMT") or "")[:10],
                description=html_to_text(r.get("jobSummary")),
            )
        if not new:
            break


# ---------------------------------------------------------------- ASML
def asml(company: dict):
    token = post_json("https://www.asml.com/api/search-token", {}).get("accessToken")
    offset, total = 0, None
    while total is None or offset < total:
        body = {
            "context": {"page": {"uri": "https://www.asml.com/en/careers/find-your-job"}, "locale": {"country": "us", "language": "en"}},
            "widget": {"items": [{"entity": "content", "rfk_id": "asml_job_search", "search": {
                "limit": 100, "offset": offset, "content": {},
                "filter": {"type": "and", "filters": [{"name": "job_country", "values": ["Taiwan"], "type": "anyOf"}]},
            }}]},
        }
        from ..http import request

        w = request("POST", "https://discover-euc1.sitecorecloud.io/discover/v2/126200477", json=body,
                    headers={"Authorization": f"Bearer {token}"}).json()["widgets"][0]
        total = w.get("total_item", 0)
        rows = w.get("content") or []
        if not rows:
            break
        for r in rows:
            yield Job(
                company_id=company["id"],
                source_id=str(r.get("job_id") or r.get("id")),
                title=r.get("name", ""),
                url=r.get("url", ""),
                location=r.get("job_location") or "Taiwan",
                department=", ".join(r.get("job_teams") or []) if isinstance(r.get("job_teams"), list) else str(r.get("job_teams") or ""),
                posted_at=str(r.get("job_date_posted") or "")[:10],
                description=html_to_text(r.get("description")),
            )
        offset += len(rows)


# ---------------------------------------------------------------- IBM
def ibm(company: dict):
    frm, total = 0, None
    while total is None or frm < total:
        data = post_json("https://www-api.ibm.com/search/api/v2", {
            "appId": "careers", "scopes": ["careers2"],
            "query": {"bool": {"must": [{"term": {"field_keyword_05": "Taiwan"}}]}},
            "size": 100, "from": frm, "sort": [{"dcdate": "desc"}], "lang": "zz",
            "_source": ["title", "url", "description", "dcdate", "field_keyword_05", "field_keyword_08", "field_keyword_19", "field_text_01"],
        })
        hits = data.get("hits", {})
        total = (hits.get("total") or {}).get("value", 0)
        rows = hits.get("hits") or []
        if not rows:
            break
        for h in rows:
            s = h.get("_source", {})
            yield Job(
                company_id=company["id"],
                source_id=str(s.get("field_text_01") or h.get("_id")),
                title=s.get("title", ""),
                url=s.get("url", ""),
                location=s.get("field_keyword_19") or "Taiwan",
                department=s.get("field_keyword_08", ""),
                posted_at=str(s.get("dcdate") or "")[:10],
                description=html_to_text(s.get("description")),
            )
        frm += len(rows)
