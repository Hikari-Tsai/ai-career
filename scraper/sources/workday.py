"""Workday career sites (`*.myworkdayjobs.com`), used by NVIDIA, Intel, Micron, AMAT, ...

params: {host, tenant, site}
The adapter auto-discovers the Taiwan location facet from the search response,
so no per-tenant facet ids need to be configured.
"""
from __future__ import annotations

from ..http import post_json, get_json
from ..models import Job, html_to_text, TW_PATTERN

PAGE = 20


def _api(p) -> str:
    return f"https://{p['host']}/wday/cxs/{p['tenant']}/{p['site']}"


def _taiwan_facets(facets: list) -> dict:
    """Find facet values whose descriptor looks like Taiwan. Prefer country-level facets."""
    found: dict[str, list[str]] = {}

    def walk(items, param=None):
        for f in items or []:
            fp = f.get("facetParameter", param)
            for v in f.get("values", []) or []:
                if "values" in v:  # nested group
                    walk([v], fp)
                    continue
                desc = v.get("descriptor", "")
                if TW_PATTERN.search(desc) and v.get("id"):
                    found.setdefault(fp, []).append(v["id"])

    walk(facets)
    if not found:
        return {}
    country = [k for k in found if "country" in k.lower()]
    key = country[0] if country else max(found, key=lambda k: len(found[k]))
    return {key: found[key]}


def fetch(company: dict):
    p = company["params"]
    api = _api(p)
    first = post_json(f"{api}/jobs", {"appliedFacets": {}, "limit": PAGE, "offset": 0, "searchText": ""})
    if company.get("all_taiwan"):  # site is a Taiwan entity whose locations are bare city names
        applied, search_text = {}, ""
    else:
        applied = p.get("applied_facets") or _taiwan_facets(first.get("facets", []))
        search_text = "" if applied else "Taiwan"
    offset, total = 0, None
    while total is None or offset < total:
        data = post_json(f"{api}/jobs", {"appliedFacets": applied, "limit": PAGE, "offset": offset, "searchText": search_text})
        if total is None:
            total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for jp in postings:
            path = jp.get("externalPath", "")
            loc = jp.get("locationsText", "")
            if (applied or company.get("all_taiwan")) and not TW_PATTERN.search(loc):
                loc = f"Taiwan ({loc})"  # facet already restricted to Taiwan; "3 Locations" etc.
            yield Job(
                company_id=company["id"],
                source_id=(jp.get("bulletFields") or [path])[0],
                title=jp.get("title", ""),
                url=f"https://{p['host']}/{p.get('locale', 'en-US')}/{p['site']}{path}",
                location=loc,
                extra={"path": path, "posted_text": jp.get("postedOn", "")},
            )
        offset += PAGE


def detail(company: dict, job: Job) -> dict:
    data = get_json(f"{_api(company['params'])}{job.extra['path']}")
    info = data.get("jobPostingInfo", {})
    locs = [info.get("location", "")] + list(info.get("additionalLocations", []) or [])
    return {
        "description": html_to_text(info.get("jobDescription")),
        "location": " / ".join(l for l in locs if l),
        "posted_at": (info.get("startDate") or "")[:10],
    }
