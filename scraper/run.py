"""Daily pipeline: crawl official career sites -> Taiwan filter -> details -> relevance -> data/jobs.json

  python -m scraper.run                 # all companies
  python -m scraper.run --only nvidia,google
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
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
DETAIL_CAP_BY_SOURCE = {"workday": 40}  # Workday rate-limits hard; spread the backlog over several days
# Only fetch descriptions for roles that could plausibly be AI work: skips technicians, sales, finance, ...
TECH_TITLE = re.compile(
    r"engineer|scientist|develop|research|architect|software|data|machine|learning|\bAI\b|\bML\b|algorithm|"
    r"intern|graduate|analyst|program|product|技術|工程|研發|研究|演算法|軟體|資料|數據|開發|人工智慧|實習",
    re.I,
)
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


def _write_step_summary(stats: dict, run: dict, day: dict, history: list[dict]) -> None:
    """Usage table on the GitHub Actions run page (no-op locally)."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    last30 = history[-30:]
    month_cost = sum((h.get("llm") or {}).get("cost_usd", 0) or 0 for h in last30)
    month_calls = sum((h.get("llm") or {}).get("calls", 0) or 0 for h in last30)
    usd = lambda v: "n/a" if v is None else f"${v:.4f}"
    lines = [
        "## Job crawl",
        f"- Taiwan jobs scanned: **{stats['taiwan_jobs_scanned']:,}**, AI-related: **{stats['ai_jobs']:,}**",
        "",
        "## LLM usage" + (f" ({run['model']})" if run.get("model") else ""),
        "| | calls | input tokens | output tokens | cost |",
        "|---|---:|---:|---:|---:|",
        f"| this run | {run['calls']} | {run['input_tokens']:,} | {run['output_tokens']:,} | {usd(run['cost_usd'])} |",
        f"| today | {day['calls']:.0f} | {day['input_tokens']:,.0f} | {day['output_tokens']:,.0f} | {usd(day['cost_usd'])} |",
        f"| last {len(last30)} days | {month_calls:.0f} | | | {usd(month_cost)} |",
        "",
        f"Still pending classification: {run.get('pending', 0)}",
    ]
    with open(path, "a") as f:
        f.write("\n".join(lines) + "\n")


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
        cap = DETAIL_CAP_BY_SOURCE.get(company["source"], DETAIL_CAP)
        if detail and fetched < cap and not rate_limited and TECH_TITLE.search(job.title):
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
    ap.add_argument("--rebaseline", action="store_true", help="reset first_seen of all current jobs to their posted date")
    ap.add_argument(
        "--mode", choices=("all", "cloud", "local"), default="all",
        help="cloud: skip companies marked local_only (they keep their last listings); local: only those",
    )
    args = ap.parse_args()
    if args.rebaseline and (args.only or args.mode == "local"):
        ap.error("--rebaseline needs a full run (without --only or --mode local)")

    all_companies = load_companies()
    companies = all_companies
    if args.mode == "cloud":
        companies = [c for c in all_companies if not c.get("local_only")]
    elif args.mode == "local":
        companies = [c for c in all_companies if c.get("local_only")]
    if args.only:
        wanted = set(args.only.split(","))
        companies = [c for c in companies if c["id"] in wanted]
    by_id = {c["id"]: c for c in all_companies}

    DATA.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    details = _load_json(DETAILS_FILE, {})
    llm_cache = llm.load_cache(LLM_CACHE_FILE)
    previous = {j["uid"]: j for j in _load_json(JOBS_FILE, {}).get("jobs", [])}
    prev_seen = _load_json(DATA / "seen.json", {})  # uid -> first_seen for every TW job ever seen
    # A company crawled for the first time (or everything, with --rebaseline) is a baseline: its existing
    # openings get first_seen = posted date instead of today, so they aren't all flagged "new".
    known = set() if args.rebaseline else {uid.split(":", 1)[0] for uid in prev_seen}
    if args.rebaseline:
        prev_seen = {}

    def baseline_date(r: dict) -> str:
        return (r["posted_at"] or "2000-01-01") if r["company_id"].split(":", 1)[0] not in known else today
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
    dynamic: dict[str, dict] = {}  # companies discovered through a job board entry (e.g. "yourator:jkopay")
    official = [  # English names match on word boundaries so "Arm"/"HP"/"LINE" don't hit unrelated startups
        re.compile(rf"(?<![A-Za-z]){re.escape(n)}(?![A-Za-z])" if n.isascii() else re.escape(n), re.I)
        for c in all_companies if not c.get("multi_company")
        for n in (c["name"].split(" (")[0].split(" / ")[0], c.get("name_zh")) if n and len(n) >= 2
    ]
    for job in all_jobs:
        c = by_id.get(job.company_id) or dynamic.get(job.company_id)
        if c is None:
            entry = by_id[job.company_id.split(":", 1)[0]]
            name = job.extra["company"]["name"]
            if any(o.search(name) for o in official):  # official careers site already covers it
                continue
            c = dynamic[job.company_id] = {
                "id": job.company_id, "name": name, "name_zh": "", "group": entry["group"],
                "careers_url": job.extra["company"]["careers_url"], "via": entry["name"],
            }
        rs = rule_score(job.title, job.description, job.department)
        rec = job.to_dict()
        rec.update(
            company_name=c["name"],
            company_name_zh=c.get("name_zh", ""),
            group=c["group"],
            via=c.get("via", ""),
            content_hash=job.content_hash,
            rule_score=rs.score,
            rule_category=rs.category,
            matched=rs.matched,
        )
        records.append(rec)

    candidates = [r for r in records if r["rule_score"] >= CANDIDATE_THRESHOLD]
    llm_usage = llm.classify(candidates, llm_cache)

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
        first_seen = prev_seen.get(r["uid"]) or (None if args.rebaseline else (previous.get(r["uid"]) or {}).get("first_seen"))
        if not first_seen:
            first_seen = baseline_date(r)
        prev_seen.setdefault(r["uid"], first_seen)
        out.append(
            {
                "uid": r["uid"],
                "company_id": r["company_id"],
                "company": r["company_name"],
                "company_zh": r["company_name_zh"],
                "group": r["group"],
                "via": r["via"],
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
        prev_seen.setdefault(r["uid"], baseline_date(r))
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
        out += [j for j in previous.values() if j["uid"].split(":")[0] in failed]
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
                "ai_jobs": sum(1 for j in out if j["uid"].split(":")[0] == c["id"]),  # board entries count all their startups
            }
            for c in all_companies
        ] + [
            {"id": c["id"], "name": c["name"], "name_zh": "", "group": c["group"], "careers_url": c["careers_url"],
             "via": c["via"], "ok": True, "ai_jobs": sum(1 for j in out if j["company_id"] == c["id"])}
            for c in dynamic.values()
        ],
        "external": [
            {
                "id": c["id"], "name": c["name"], "name_zh": c.get("name_zh", ""), "group": c["group"],
                "careers_url": c["careers_url"], "reason": LINK_ONLY_REASONS.get(c.get("reason"), ""),
                "in_salary_survey": bool(c.get("in_salary_survey")),
            }
            for c in load_link_only()
        ],
        "stats": {"taiwan_jobs_scanned": sum((s.get("taiwan_jobs") or 0) for s in status.values()), "ai_jobs": len(out)},
        "llm_usage": llm_usage,
        "jobs": out,
    }
    JOBS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    (DATA / "seen.json").write_text(json.dumps(prev_seen, sort_keys=True))
    LLM_CACHE_FILE.write_text(json.dumps({k: llm_cache[k] for k in sorted(llm_cache)}, ensure_ascii=False, indent=0))
    with gzip.open(DETAILS_FILE, "wt") as f:
        json.dump(details, f, ensure_ascii=False)

    history = _load_json(HISTORY_FILE, [])
    prev_today = next((h for h in history if h["date"] == today), {})
    llm_day = {  # a day can have several runs (schedule + manual/push), so LLM usage accumulates
        k: round((prev_today.get("llm") or {}).get(k, 0) + (llm_usage.get(k) or 0), 4)
        for k in ("calls", "classified", "input_tokens", "output_tokens", "cost_usd")
    }
    history = [h for h in history if h["date"] != today] + [
        {"date": today, "taiwan_jobs": payload["stats"]["taiwan_jobs_scanned"], "ai_jobs": len(out),
         "new_ai_jobs": sum(1 for j in out if j["first_seen"] == today), "llm": llm_day}
    ]
    HISTORY_FILE.write_text(json.dumps(history[-365:], indent=0))
    _write_step_summary(payload["stats"], llm_usage, llm_day, history)

    print(f"\n{len(records)} Taiwan jobs scanned, {len(out)} AI-related, {llm_usage['calls']} LLM calls, {time.time()-t0:.0f}s")
    return 0 if any(s.get("ok") for s in status.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
