"""Daily pipeline: crawl official career sites -> Taiwan filter -> details -> relevance -> data/jobs.json

  python -m scraper.run                 # all companies
  python -m scraper.run --only nvidia,google
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

from . import llm
from .config import CACHE, DATA, GROUP_LABELS, LINK_ONLY_REASONS, load_companies, load_link_only
from .http import RateLimited
from .models import Job, is_taiwan
from .relevance import CANDIDATE_THRESHOLD, CATEGORY_LABELS, DISPLAY_THRESHOLD, rule_score
from .sources import SOURCES

DETAIL_CAP = 300  # detail requests per company per run; the cache fills the rest on later runs
DETAILS_FILE = CACHE / "details.json.gz"
LLM_CACHE_FILE = DATA / "llm_cache.json"
JOBS_FILE = DATA / "jobs.json"
HISTORY_FILE = DATA / "history.json"


def _load_json(path, default):
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt") as f:
                return json.load(f)
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, EOFError):
        return default


def _snippet(text: str, terms: list[str], width: int = 260) -> str:
    """Excerpt around the first AI term in the description, instead of the company boilerplate intro."""
    if not text:
        return ""
    import re

    def pos(term: str) -> int:
        pat = re.escape(term) if not term.isascii() else rf"(?<![A-Za-z]){re.escape(term)}(?![A-Za-z])"
        m = re.search(pat, text, re.I)
        return m.start() if m else -1

    specific = [t for t in terms if len(t) > 3] or terms  # "machine learning" beats "AI"
    hits = [i for t in specific if (i := pos(t)) >= 0]
    if not hits:
        return text[:width]
    start = max(0, min(hits) - 80)
    cut = max(text.rfind(". ", 0, start), text.rfind("。", 0, start))
    if cut >= 0 and start - cut < 120:
        start = cut + 1
    return ("…" if start else "") + text[start : start + width].strip()


def crawl_company(company: dict, details: dict) -> tuple[list[Job], str | None]:
    fetch, detail = SOURCES[company["source"]]
    jobs: dict[str, Job] = {}
    try:
        for job in fetch(company):
            if company.get("all_taiwan") or is_taiwan(job.location):
                jobs.setdefault(job.uid, job)
    except Exception as exc:  # one broken site must not sink the whole run
        return [], f"{type(exc).__name__}: {exc}"

    fetched = 0
    rate_limited = False
    for job in jobs.values():
        cached = details.get(job.uid)
        if job.description:
            continue
        if cached:
            job.description = cached.get("description", "")
            job.location = cached.get("location") or job.location
            job.posted_at = job.posted_at or cached.get("posted_at", "")
            continue
        if detail and fetched < DETAIL_CAP and not rate_limited:
            try:
                d = detail(company, job)
                fetched += 1
            except RateLimited as exc:
                rate_limited = True
                print(f"  {company['id']}: rate limited, using titles only this run ({exc})", file=sys.stderr)
                continue
            except Exception as exc:
                print(f"  detail failed {job.uid}: {exc}", file=sys.stderr)
                continue
            job.description = d.get("description", "")
            job.location = d.get("location") or job.location
            job.posted_at = job.posted_at or d.get("posted_at", "") or ""
    for job in jobs.values():
        if job.description:
            details[job.uid] = {"description": job.description[:8000], "location": job.location, "posted_at": job.posted_at}
    return list(jobs.values()), None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated company ids")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    all_companies = load_companies()
    companies = all_companies
    if args.only:
        wanted = set(args.only.split(","))
        companies = [c for c in all_companies if c["id"] in wanted]
    by_id = {c["id"]: c for c in all_companies}

    DATA.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    details = _load_json(DETAILS_FILE, {})
    llm_cache = llm.load_cache(LLM_CACHE_FILE)
    previous = {j["uid"]: j for j in _load_json(JOBS_FILE, {}).get("jobs", [])}
    prev_seen = _load_json(DATA / "seen.json", {})  # uid -> first_seen for every TW job ever seen
    bootstrap = not prev_seen
    today = date.today().isoformat()

    t0 = time.time()
    all_jobs: list[Job] = []
    status: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(crawl_company, c, details): c for c in companies}
        for fut in as_completed(futures):
            c = futures[fut]
            jobs, err = fut.result()
            status[c["id"]] = {"ok": err is None, "taiwan_jobs": len(jobs), "error": err}
            print(f"{'OK ' if not err else 'ERR'} {c['id']:<22} {len(jobs):>4} TW jobs {err or ''}")
            all_jobs.extend(jobs)

    # ---- relevance
    records = []
    for job in all_jobs:
        c = by_id[job.company_id]
        rs = rule_score(job.title, job.description, job.department)
        rec = job.to_dict()
        rec.update(
            company_name=c["name"],
            company_name_zh=c.get("name_zh", ""),
            group=c["group"],
            content_hash=job.content_hash,
            rule_score=rs.score,
            rule_category=rs.category,
            matched=rs.matched,
        )
        records.append(rec)

    candidates = [r for r in records if r["rule_score"] >= CANDIDATE_THRESHOLD]
    calls = llm.classify(candidates, llm_cache)

    out = []
    for r in records:
        a = llm_cache.get(r["content_hash"])
        if a:
            score = round(0.75 * a["relevance"] + 0.25 * r["rule_score"], 1)
            keep = a["relevance"] >= 40 and a["category"] != "not_ai"
            category = a["category"]
        else:
            score, category = r["rule_score"], r["rule_category"]
            keep = r["rule_score"] >= DISPLAY_THRESHOLD
        if not keep:
            continue
        first_seen = prev_seen.get(r["uid"]) or (previous.get(r["uid"]) or {}).get("first_seen")
        if not first_seen:  # on the very first run, don't flag every job as new
            first_seen = (r["posted_at"] or "2000-01-01") if bootstrap else today
        prev_seen.setdefault(r["uid"], first_seen)
        out.append(
            {
                "uid": r["uid"],
                "company_id": r["company_id"],
                "company": r["company_name"],
                "company_zh": r["company_name_zh"],
                "group": r["group"],
                "title": r["title"],
                "url": r["url"],
                "location": r["location"],
                "city": r["city"],
                "department": r["department"],
                "posted_at": r["posted_at"],
                "first_seen": first_seen,
                "score": score,
                "rule_score": r["rule_score"],
                "category": category,
                "matched": r["matched"][:8],
                "seniority": (a or {}).get("seniority", ""),
                "skills": (a or {}).get("skills", []),
                "summary": (a or {}).get("summary_zh", ""),
                "snippet": _snippet(r["description"], r["matched"]),
                "llm": bool(a),
            }
        )
    out.sort(key=lambda j: (-j["score"], j["company"]))

    # ---- persist
    for r in records:
        prev_seen.setdefault(r["uid"], (r["posted_at"] or "2000-01-01") if bootstrap else today)
    live = {r["uid"] for r in records}
    # Companies that failed this run, or weren't crawled (--only), keep yesterday's listings and first_seen,
    # so a flaky site doesn't empty its section or reset "new" badges.
    prev_status = {c["id"]: c for c in _load_json(JOBS_FILE, {}).get("companies", [])}
    for cid in by_id.keys() - status.keys():
        old = prev_status.get(cid, {})
        status[cid] = {k: old[k] for k in ("ok", "taiwan_jobs", "error") if k in old}
    failed = {cid for cid, s in status.items() if not s.get("ok")} | (by_id.keys() - {c["id"] for c in companies})
    prev_seen = {k: v for k, v in prev_seen.items() if k in live or k.split(":")[0] in failed}
    if failed:  # carry over yesterday's listings for companies whose site failed today
        out += [j for j in previous.values() if j["company_id"] in failed]
    details = {k: v for k, v in details.items() if k in live or k.split(":")[0] in failed}

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "llm_enabled": llm.enabled(),
        "llm_model": llm.MODEL if llm.enabled() else None,
        "groups": GROUP_LABELS,
        "categories": {**CATEGORY_LABELS, "ai_adjacent": "AI 相關應用 / 管理"},
        "companies": [
            {
                "id": c["id"],
                "name": c["name"],
                "name_zh": c.get("name_zh", ""),
                "group": c["group"],
                "careers_url": c.get("careers_url", ""),
                "in_salary_survey": bool(c.get("in_salary_survey")),
                **status.get(c["id"], {}),
                "ai_jobs": sum(1 for j in out if j["company_id"] == c["id"]),
            }
            for c in all_companies
        ],
        "external": [
            {
                "id": c["id"], "name": c["name"], "name_zh": c.get("name_zh", ""), "group": c["group"],
                "careers_url": c["careers_url"], "reason": LINK_ONLY_REASONS.get(c.get("reason"), ""),
                "in_salary_survey": bool(c.get("in_salary_survey")),
            }
            for c in load_link_only()
        ],
        "stats": {"taiwan_jobs_scanned": sum((s.get("taiwan_jobs") or 0) for s in status.values()), "ai_jobs": len(out), "llm_calls": calls},
        "jobs": out,
    }
    JOBS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    (DATA / "seen.json").write_text(json.dumps(prev_seen, sort_keys=True))
    LLM_CACHE_FILE.write_text(json.dumps({k: llm_cache[k] for k in sorted(llm_cache)}, ensure_ascii=False, indent=0))
    with gzip.open(DETAILS_FILE, "wt") as f:
        json.dump(details, f, ensure_ascii=False)

    history = _load_json(HISTORY_FILE, [])
    history = [h for h in history if h["date"] != today] + [
        {"date": today, "taiwan_jobs": len(records), "ai_jobs": len(out), "new_ai_jobs": sum(1 for j in out if j["first_seen"] == today)}
    ]
    HISTORY_FILE.write_text(json.dumps(history[-365:], indent=0))

    print(f"\n{len(records)} Taiwan jobs scanned, {len(out)} AI-related, {calls} LLM calls, {time.time()-t0:.0f}s")
    return 0 if any(s.get("ok") for s in status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
