"""Official career sites of Taiwanese semiconductor companies.

mediatek:        careers.mediatek.com tRPC JSON API
realtek:         recruit.realtek.com JSON API
umc:             careers.umc.com PHP listing (single page)
successfactors:  SAP SuccessFactors Career Site Builder, params {host}  (Nuvoton, Winbond)
talentsite:      ASP.NET "TalentSite" recruiting sites, params {host, country}  (GUC, VIS)
"""
from __future__ import annotations

import json
import re
from urllib.parse import unquote

from ..http import get_json, get_text
from ..models import Job, html_to_text, is_taiwan

# ---------------------------------------------------------------- MediaTek
_MTK_TW = {"hsinchu", "chupei", "taipei", "zhubei", "tainan", "taichung"}


def mediatek(company: dict):
    page, total_pages = 1, 1
    while page <= total_pages:
        query = {"0": {"json": {
            "locales": "en_US", "page": page, "jobQueryInfo": {},
            "filters": {"categorys": [], "workExperiences": [], "locations": [], "programs": []},
            "sortBy": "publishedDate", "order": "DESC", "limit": 50,
        }}}
        data = get_json(
            "https://careers.mediatek.com/api/trpc/job.getJobs",
            params={"batch": "1", "input": json.dumps(query, separators=(",", ":"))},
        )[0]["result"]["data"]["json"]
        total_pages = (data.get("pagination") or {}).get("total_pages", 1)
        for j in data.get("jobs", []):
            loc = ((j.get("properties") or {}).get("location") or {})
            city = str(loc.get("code") or loc.get("label") or "").strip()  # fields are swapped upstream
            if city.lower() not in _MTK_TW:
                continue
            yield Job(
                company_id=company["id"],
                source_id=str(j["id"]),
                title=j.get("title", ""),
                url=f"https://careers.mediatek.com/en/jobs/{j['id']}",
                location=f"{city}, Taiwan",
                posted_at=str(j.get("publishedDate") or "")[:10],
                description=html_to_text(j.get("description")),
            )
        page += 1


# ---------------------------------------------------------------- Realtek
def realtek(company: dict):
    for j in get_json("https://recruit.realtek.com/Job/GetAllJobList").get("Data") or []:
        yield Job(
            company_id=company["id"],
            source_id=str(j["JobOppId"]),
            title=j.get("JobTitle", ""),
            url=f"https://recruit.realtek.com/Job/JobDetail?jobid={j['JobOppId']}&lang=en",
            location=f"台灣 {j.get('Location', '')}",
            department=j.get("JobType", ""),
        )


def realtek_detail(company: dict, job: Job) -> dict:
    d = get_json("https://recruit.realtek.com/Job/GetVacancyDetail", params={"JobOppId": job.source_id}).get("Data") or {}
    text = " ".join(str(v) for k, v in d.items() if isinstance(v, str) and len(v) > 20)
    return {"description": html_to_text(text)}


# ---------------------------------------------------------------- UMC
_UMC_ROW = re.compile(r'<li id="myli\d+".*?</li>', re.S)
_UMC_CELL = re.compile(r'<div><a href="jobin\.php\?mid=(\d+)">(.*?)</a></div>', re.S)
_UMC_TW = re.compile(r"園區|台北|臺北|新竹|台南|臺南|南科|竹科|台灣", re.I)


def umc(company: dict):
    page = get_text("https://careers.umc.com/jobsearch.php")
    for row in _UMC_ROW.findall(page):
        cells = _UMC_CELL.findall(row)
        if len(cells) < 6:
            continue
        mid = cells[0][0]
        title, category, _exp, _edu, region, opened = (html_to_text(c[1]) for c in cells[:6])
        if not _UMC_TW.search(region):
            continue
        yield Job(
            company_id=company["id"],
            source_id=mid,
            title=title,
            url=f"https://careers.umc.com/jobin.php?mid={mid}",
            location=f"台灣 {region}",
            department=category,
            posted_at=opened if re.match(r"\d{4}-\d\d-\d\d$", opened) else "",
        )


def umc_detail(company: dict, job: Job) -> dict:
    text = html_to_text(get_text(job.url))
    i = text.find("工作說明")
    return {"description": text[i : i + 6000] if i >= 0 else ""}


# ---------------------------------------------------------------- SAP SuccessFactors CSB
# Works for both the table layout (Nuvoton, Winbond) and the tile layout (Acer).
_SF_LINK = re.compile(r'<a[^>]*?(?:class="jobTitle-link[^"]*"[^>]*?href="(/[^"]*?job/[^"]+/(\d+)/)"|href="(/[^"]*?job/[^"]+/(\d+)/)"[^>]*?class="jobTitle-link[^"]*")[^>]*>(.*?)</a>', re.S)
_SF_LOC = re.compile(r'(?:<span class="jobLocation">|section-field location[^>]*>.*?<div[^>]*>)(.*?)</(?:span|div)>', re.S)
_SF_DATE = re.compile(r'class="[^"]*(?:jobDate|date)[^"]*"[^>]*>(?:\s*<[^>]+>)*\s*(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})', re.S)
_SF_DESC = re.compile(r'<span[^>]*itemprop="description"[^>]*>(.*?)</span>\s*</div>', re.S)


def successfactors(company: dict):
    host = company["params"]["host"]
    seen: set[str] = set()
    for start in range(0, 2000, 25):
        page = get_text(
            f"https://{host}{company['params'].get('path', '')}/search/",
            params={"q": "", "locationsearch": company["params"].get("locationsearch", ""), "startrow": start},
        )
        links = [(m, m.group(2) or m.group(4)) for m in _SF_LINK.finditer(page)]
        new = 0
        for i, (m, jid) in enumerate(links):
            path = m.group(1) or m.group(3)
            if jid in seen:
                continue
            seen.add(jid)
            new += 1
            # a job appears twice (desktop + phone markup): its block runs to the next *different* job's link
            nxt = next((n.start() for n, other in links[i + 1:] if other != jid), m.end() + 6000)
            block = page[m.end():nxt]
            loc = html_to_text((_SF_LOC.search(block) or [None, ""])[1])
            slug = unquote(path).split("job/", 1)[1].split("/")[0]
            if not (is_taiwan(loc) or loc.endswith(", TW") or is_taiwan(slug)):
                continue
            d = _SF_DATE.search(block)
            yield Job(
                company_id=company["id"],
                source_id=jid,
                title=html_to_text(m.group(5)),
                url=f"https://{host}{path}",
                location=loc if is_taiwan(loc) else f"Taiwan, {loc or slug.split('-')[0]}",
                posted_at=f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}" if d else "",
            )
        if not new:
            break


_SF_POSTED = re.compile(r'itemprop="datePosted" content="\w{3} (\w{3}) (\d{1,2}) [\d:]+ \w+ (\d{4})"')


def successfactors_detail(company: dict, job: Job) -> dict:
    page = get_text(job.url)
    m = _SF_DESC.search(page)
    out = {"description": html_to_text(m.group(1)) if m else ""}
    if d := _SF_POSTED.search(page):
        from datetime import datetime

        out["posted_at"] = datetime.strptime(" ".join(d.groups()), "%b %d %Y").date().isoformat()
    return out


# ---------------------------------------------------------------- TalentSite (ASP.NET)
_TS_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_TS_CELL = re.compile(r'<td data-label="([^"]*)"[^>]*>(.*?)</td>', re.S)
_TS_SN = re.compile(r'/Jobs/Detail\?sn=([\w-]+)')


def talentsite(company: dict):
    p = company["params"]
    seen: set[str] = set()
    for page in range(1, 200):
        query = dict(p.get("query") or {"CountryTw": p.get("country", "")})
        html = get_text(f"https://{p['host']}/Jobs", params={**query, "page": page})
        new = 0
        for row in _TS_ROW.findall(html):
            sn = _TS_SN.search(row)
            if not sn or sn.group(1) in seen:
                continue
            seen.add(sn.group(1))
            new += 1
            cells = {k: html_to_text(v).replace("\xa0", " ").strip() for k, v in _TS_CELL.findall(row)}
            title = cells.get("職缺名稱") or cells.get("Job Title") or cells.get("職務名稱") or ""
            loc = cells.get("工作地區") or cells.get("Location") or cells.get("工作地點") or ""
            yield Job(
                company_id=company["id"],
                source_id=sn.group(1),
                title=re.sub(r"^[A-Z]*\d{4,}\s*[-–]?\s*", "", title),  # drop requisition prefix "G240049-", "RD21957 "
                url=f"https://{p['host']}/Jobs/Detail?sn={sn.group(1)}",
                location=loc if is_taiwan(loc) else f"Taiwan {loc}",
                department=cells.get("職務類別", ""),
            )
        if not new:
            break


def talentsite_detail(company: dict, job: Job) -> dict:
    text = html_to_text(get_text(job.url))
    i = text.find("工作說明")
    return {"description": text[i : i + 6000] if i >= 0 else text[:4000]}
