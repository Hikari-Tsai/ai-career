"""Stage 2 relevance (optional): structured classification with Claude.

Runs only when ANTHROPIC_API_KEY is set, only on rule-stage candidates, and
only for jobs whose content hash isn't already in the cache — so a daily run
classifies just the handful of new / edited postings.

Env:
  ANTHROPIC_API_KEY   enables this stage
  LLM_MODEL           default claude-opus-5-5 (claude-haiku-5-5 is ~40x cheaper)
  LLM_MAX_PER_RUN     safety cap on API calls per run (default 400)
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

MODEL = os.environ.get("LLM_MODEL", "claude-opus-5-5")
MAX_PER_RUN = int(os.environ.get("LLM_MAX_PER_RUN", "400"))
PROMPT_VERSION = "v1"

Category = Literal[
    "llm_genai", "ml", "cv_nlp", "mlops_infra", "ai_hw", "data_sci", "ai_adjacent", "not_ai"
]


class JobAssessment(BaseModel):
    relevance: int = Field(description="0-100: how much the day-to-day work is building/training/deploying AI/ML/LLM systems")
    category: Category
    seniority: Literal["intern", "junior", "mid", "senior", "staff_plus", "manager", "unknown"]
    skills: list[str] = Field(description="up to 6 key technical skills, as written in the posting")
    summary_zh: str = Field(description="一句話繁體中文摘要，40 字以內，說明這個職位實際在做什麼")


SYSTEM = """You classify job postings for a Taiwan job board focused on AI / ML / LLM careers.

Score `relevance` by what the person will actually do day to day, not by buzzwords:
- 85-100: core AI/ML/LLM work — training or fine-tuning models, LLM/RAG/agent systems, ML research, CV/NLP/speech modeling, ML inference/compiler/kernel optimization.
- 60-84: substantial AI component — ML platform/MLOps, data science with modeling, AI accelerator architecture, AI solution/application engineering that builds on models.
- 30-59: AI-adjacent — AI server/GPU hardware design, AI product/program management, software that integrates AI APIs.
- 0-29: not AI work, even if the company or product mentions AI (sales, sourcing, mechanical, manufacturing, generic IT).

category: llm_genai | ml | cv_nlp | mlops_infra | ai_hw | data_sci | ai_adjacent | not_ai.
Postings may be in English or Traditional Chinese. summary_zh must be Traditional Chinese."""


def _client():
    import anthropic

    return anthropic.Anthropic(max_retries=4)


def _classify_one(client, job: dict) -> dict | None:
    import anthropic

    text = (
        f"Company: {job['company_name']}\nTitle: {job['title']}\nDepartment: {job.get('department','')}\n"
        f"Location: {job.get('location','')}\n\nDescription:\n{job.get('description','')[:8000]}"
    )
    try:
        resp = client.messages.parse(
            model=MODEL,
            max_tokens=2000,
            system=SYSTEM,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": text}],
            output_format=JobAssessment,
        )
    except anthropic.APIStatusError as exc:
        print(f"  llm error {job['uid']}: {exc.status_code} {exc.message}", file=sys.stderr)
        return None
    except anthropic.APIConnectionError as exc:
        print(f"  llm connection error {job['uid']}: {exc}", file=sys.stderr)
        return None
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return None
    out = resp.parsed_output.model_dump()
    out["skills"] = out["skills"][:6]
    out["model"] = MODEL
    out["prompt"] = PROMPT_VERSION
    return out


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def classify(jobs: list[dict], cache: dict) -> int:
    """Fill cache[content_hash] for jobs not yet classified. Returns number of API calls."""
    todo = [j for j in jobs if (cache.get(j["content_hash"]) or {}).get("prompt") != PROMPT_VERSION]
    todo.sort(key=lambda j: -j["rule_score"])
    todo = todo[:MAX_PER_RUN]
    if not todo or not enabled():
        return 0
    client = _client()
    print(f"LLM: classifying {len(todo)} postings with {MODEL}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        for job, result in zip(todo, pool.map(lambda j: _classify_one(client, j), todo)):
            if result:
                cache[job["content_hash"]] = result
    return len(todo)


def load_cache(path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
