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
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

MODEL = os.environ.get("LLM_MODEL") or "claude-opus-5-5"  # empty when the Actions variable is unset
MAX_PER_RUN = int(os.environ.get("LLM_MAX_PER_RUN") or "400")
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


# USD per million tokens (input, output), Anthropic first-party API. Cache reads bill at 0.1x input,
# 5-minute cache writes at 1.25x. Unknown models report tokens with cost=None.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),  # prompts up to 100K tokens
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-fable-5-2": (8.00, 40.00),
}


class Usage:
    """Thread-safe accumulator for response.usage across a run."""

    FIELDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")

    def __init__(self):
        self._lock = threading.Lock()
        self.calls = self.ok = 0
        self.tokens = dict.fromkeys(self.FIELDS, 0)

    def add(self, u) -> None:
        with self._lock:
            for f in self.FIELDS:
                self.tokens[f] += getattr(u, f, 0) or 0

    def cost_usd(self) -> float | None:
        price = PRICES.get(MODEL)
        if not price:
            return None
        pin, pout = price
        t = self.tokens
        usd = (t["input_tokens"] * pin + t["cache_read_input_tokens"] * pin * 0.1
               + t["cache_creation_input_tokens"] * pin * 1.25 + t["output_tokens"] * pout) / 1e6
        return round(usd, 4)

    def summary(self) -> dict:
        return {"model": MODEL if self.calls else None, "calls": self.calls, "classified": self.ok,
                **self.tokens, "cost_usd": self.cost_usd() if self.calls else 0.0}


usage = Usage()


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
    usage.add(resp.usage)
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return None
    out = resp.parsed_output.model_dump()
    out["skills"] = out["skills"][:6]
    out["model"] = MODEL
    out["prompt"] = PROMPT_VERSION
    return out


def enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def classify(jobs: list[dict], cache: dict) -> dict:
    """Fill cache[content_hash] for jobs not yet classified. Returns this run's usage summary."""
    todo = [j for j in jobs if (cache.get(j["content_hash"]) or {}).get("prompt") != PROMPT_VERSION]
    todo.sort(key=lambda j: -j["rule_score"])
    pending = len(todo)
    todo = todo[:MAX_PER_RUN]
    if not todo or not enabled():
        return {**usage.summary(), "pending": pending if enabled() else 0}
    client = _client()
    print(f"LLM: classifying {len(todo)} of {pending} pending postings with {MODEL}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        for job, result in zip(todo, pool.map(lambda j: _classify_one(client, j), todo)):
            usage.calls += 1
            if result:
                usage.ok += 1
                cache[job["content_hash"]] = result
    s = {**usage.summary(), "pending": pending - usage.ok}
    cost = f"${s['cost_usd']:.4f}" if s["cost_usd"] is not None else "unknown (no price for model)"
    print(
        f"LLM usage: {s['calls']} calls ({s['classified']} ok), {s['input_tokens']:,} in / {s['output_tokens']:,} out tokens, "
        f"cost {cost}, {s['pending']} still pending"
    )
    return s


def load_cache(path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
