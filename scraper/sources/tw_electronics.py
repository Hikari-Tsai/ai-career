"""Official career sites of Taiwanese electronics / telecom companies.

foxconn:   recruit.foxconn.com HH Recruit portal_api (JSON)
wistron:   jobs.wistron.com /v1/vacancy (JSON)
twm:       Taiwan Mobile hrweb TWMHRMS (JSON)
synology:  career.synology.com /api (JSON)
"""
from __future__ import annotations

import re

from ..http import get_json, get_text, post_json
from ..models import Job, html_to_text

# ---------------------------------------------------------------- Foxconn
_FOX = "https://recruit.foxconn.com/hh_recruit_tw_api/portal_api"


def foxconn(company: dict):
    for j in get_json(f"{_FOX}/JobVacancies"):
        if j.get("loc") not in ("TA", "台灣"):
            continue
        yield Job(
            company_id=company["id"],
            source_id=j["id"],
            title=j.get("job_name", ""),
            url="https://recruit.foxconn.com/isite-web-tw/main/jobsearch",  # the site has no per-job deep link
            location="台灣" if j.get("loc_name") in (None, "", "台灣") else f"台灣 {j['loc_name']}",
            department=j.get("job_type_name", ""),
            description=html_to_text(j.get("desc_4")),
            extra={"job_no": j.get("job_no", "")},
        )


def foxconn_detail(company: dict, job: Job) -> dict:
    d = get_json(f"{_FOX}/JobVacancies/{job.source_id}")
    parts = [d.get(k) or "" for k in ("desc_4", "desc_3", "edu_level_name_and_desc")]
    return {
        "description": html_to_text("\n".join(p for p in parts if isinstance(p, str))),
        "location": f"台灣 {d.get('factory') or ''}".strip(),
        "posted_at": str(d.get("expect_date") or "")[:10],
    }


# ---------------------------------------------------------------- Wistron
_WISTRON_TW = {"TPE": "台北", "NTPC": "新北", "TYN": "桃園", "HSZ": "新竹", "HSZ_HOUKU": "新竹湖口", "HSZ_ZHUBEI": "竹北", "KHH": "高雄", "TNN": "台南"}


def wistron(company: dict):
    body = {"page": 1, "keywords": None, "job_type": [], "sort_prop": None, "sort_type": None,
            "work_experience": None, "area": [], "duty_location": [], "pagesize": 200}
    seen: set[str] = set()
    while True:
        rows = (post_json("https://jobs.wistron.com/v1/vacancy", body).get("data") or {}).get("list") or []
        new = [r for r in rows if str(r["id"]) not in seen]
        if not new:
            break
        for r in new:
            seen.add(str(r["id"]))
            loc = r.get("duty_location") or ""
            if loc not in _WISTRON_TW or r.get("status", "O") != "O":
                continue
            yield Job(
                company_id=company["id"],
                source_id=str(r.get("code") or r["id"]),
                title=r.get("position_title", ""),
                url="https://jobs.wistron.com/#/app/vacancy",
                location=f"台灣 {_WISTRON_TW[loc]}",
                department=str(r.get("job_type") or ""),
                posted_at=str(r.get("updated_at") or r.get("created_at") or "")[:10],
                description=html_to_text(f"{r.get('job_desc') or ''}\n{r.get('other') or ''}"),
            )
        body["page"] += 1


# ---------------------------------------------------------------- Taiwan Mobile
_TWM = "https://hrweb.taiwanmobile.com/TWMHRMS/P/ToBackend/Vacancies"


def twm(company: dict):
    data = post_json(f"{_TWM}/FindVacancies", {"VacanciesTypeID": 0, "OfficeID": 0, "key": ""})
    for v in data.get("Vacancies") or []:
        vid, tid, tname = v["VacanciesID"], v.get("VacanciesTypeID", 1), v.get("VacanciesTypeName", "")
        yield Job(
            company_id=company["id"],
            source_id=str(vid),
            title=v.get("VacanciesName", ""),
            url=f"https://hrweb.taiwanmobile.com/TWMHRMS/#/job-view/vacancy-detail/{vid}/1/{tid}/{tname}",
            location=f"台灣 {v.get('officeArea') or ''}".strip(),
            department=tname,
        )


def twm_detail(company: dict, job: Job) -> dict:
    d = post_json(f"{_TWM}/GetVacancies", {"VacanciesID": int(job.source_id)})
    d = d.get("Vacancies", d) if isinstance(d, dict) else {}
    if isinstance(d, list):
        d = d[0] if d else {}
    return {"description": html_to_text(f"{d.get('JobDesc') or ''}\n{d.get('MainReq') or ''}")}


# ---------------------------------------------------------------- Synology
def synology(company: dict):
    loc_key = company["params"].get("location", "HQ")
    data = get_json(f"https://career.synology.com/api/enum/{loc_key}")
    for j in data.get("list") or []:
        yield Job(
            company_id=company["id"],
            source_id=str(j.get("id_position") or j["id"]),
            title=j.get("name", ""),
            url=f"https://career.synology.com/en-global/{loc_key}/",
            location=j.get("location") or j.get("place") or "Taipei, Taiwan",
            department=j.get("job_category_en", ""),
            description=html_to_text(j.get("description")),
        )


# ---------------------------------------------------------------- Chunghwa Telecom (Big5 JSP)
_CHT = "https://rmis.cht.com.tw/portal/jobs/"
_CHT_GROUP = re.compile(r"cht-Bjob-jobcard-group-header.*?<h6[^>]*>(.*?)</h6>.*?徵才時間：(\d{4})/(\d{2})/(\d{2})(.*?)(?=cht-Bjob-jobcard-group-header|$)", re.S)
_CHT_CARD = re.compile(
    r"cht-jobcard-code'>代碼：(\w+)</div>\s*<h5[^>]*>(.*?)</h5>.*?cht-jobcard-info'>(.*?)</div>.*?href='(jobdetail\.jsp\?[^']+)'", re.S
)


def _big5(path: str) -> str:
    from ..http import request

    return request("GET", _CHT + path).content.decode("big5", errors="replace")


def cht(company: dict):
    page = _big5("joblist.jsp")
    for g in _CHT_GROUP.finditer(page):
        round_name, y, m, d, body = g.groups()
        for code, title, info, href in _CHT_CARD.findall(body):
            spans = [html_to_text(s) for s in re.findall(r"<span>(.*?)</span>", info, re.S)]
            yield Job(
                company_id=company["id"],
                source_id=code,
                title=html_to_text(title),
                url=_CHT + href,
                location=f"台灣 {spans[0] if spans else ''}".strip(),
                department=html_to_text(round_name),
                posted_at=f"{y}-{m}-{d}",
                extra={"href": href},
            )


def cht_detail(company: dict, job: Job) -> dict:
    return {"description": html_to_text(_big5(job.extra["href"]))[:6000]}


# ---------------------------------------------------------------- Far EasTone (Liferay portlet)
_FET_P = "_FETRECopportunity_WAR_FETRECopportunityportlet_"
_FET_BASE = (
    "https://erecruit.fareastone.com.tw/zh_TW/web/guest/home?p_p_id=FETRECopportunity_WAR_FETRECopportunityportlet"
    f"&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view&p_p_col_id=column-1&p_p_col_count=1&{_FET_P}isSearch=true"
    f"&{_FET_P}mvcPath=%2Fhtml%2Fjob%2Ffront%2Fview.jsp"
)
_FET_ID = re.compile(_FET_P + r'jobId"\s+type="hidden" value="(\d+)"')
_FET_JOB = re.compile(r"([A-Z]\d{3,5})\s+(.*?)\s+工作內容\s*[:：]\s*(.*?)\s*工作地點\s*[:：]\s*(\S+)", re.S)


def fet(company: dict):
    seen: set[str] = set()
    for page in range(1, 60):
        html = get_text(f"{_FET_BASE}&{_FET_P}curPage={page}")
        start = html.find('id="jobList"')
        listing = html[start:] if start >= 0 else html
        marks = list(_FET_ID.finditer(listing))
        new, prev = 0, 0
        for mk in marks:
            jid, block = mk.group(1), html_to_text(listing[prev:mk.start()])
            prev = mk.end()
            m = _FET_JOB.search(block)
            if jid in seen or not m:
                continue
            seen.add(jid)
            new += 1
            code, title, desc, loc = m.groups()
            yield Job(
                company_id=company["id"],
                source_id=jid,
                title=title.strip(),
                url=(f"https://erecruit.fareastone.com.tw/zh_TW/web/guest/home?p_p_id=FETRECopportunity_WAR_FETRECopportunityportlet"
                     f"&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view&{_FET_P}jobId={jid}"
                     f"&{_FET_P}mvcPath=%2Fhtml%2Fjob%2Ffront%2FjobDetailForInternet.jsp"),
                location=f"台灣 {loc}",
                department=code,
                description=desc.strip(),
            )
        if not new:
            break
