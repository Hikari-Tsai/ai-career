"""Verify one career-site config before adding it to companies.yaml.

  python -m scraper.probe workday '{"host":"nvidia.wd5.myworkdayjobs.com","tenant":"nvidia","site":"NVIDIAExternalCareerSite"}'
  python -m scraper.probe --company nvidia
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys

from .models import is_taiwan
from .sources import SOURCES


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?")
    ap.add_argument("params", nargs="?", default="{}")
    ap.add_argument("--company", help="probe an entry already in companies.yaml")
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args()

    if args.company:
        from .config import load_companies
        company = next(c for c in load_companies() if c["id"] == args.company)
    else:
        company = {"id": "probe", "source": args.source, "params": json.loads(args.params)}

    fetch, detail = SOURCES[company["source"]]
    jobs = list(itertools.islice(fetch(company), args.limit))
    tw = [j for j in jobs if is_taiwan(j.location) or company.get("all_taiwan")]
    print(f"fetched={len(jobs)} taiwan={len(tw)}")
    for j in tw[:8]:
        print(f"  - {j.title} | {j.location} | {j.posted_at} | {j.url}")
    if tw and detail and not tw[0].description:
        d = detail(company, tw[0])
        print("detail sample:", (d.get("description") or "")[:200].replace("\n", " "))
    elif tw:
        print("desc sample:", tw[0].description[:200].replace("\n", " "))
    return 0 if tw else 1


if __name__ == "__main__":
    sys.exit(main())
