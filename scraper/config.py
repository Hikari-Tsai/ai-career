from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPANIES_FILE = ROOT / "companies.yaml"
DATA = ROOT / "data"
CACHE = ROOT / ".cache"

GROUP_LABELS = {
    "tw_semi": "台灣半導體",
    "tw_electronics": "台灣電子 / 系統 / 電信",
    "tw_software": "台灣軟體 / 網路 / 金融",
    "global_tech": "國際科技大廠",
    "foreign_branch": "日美星外商在台",
}


def load_companies(include_disabled: bool = False) -> list[dict]:
    items = yaml.safe_load(COMPANIES_FILE.read_text())["companies"]
    out = []
    for c in items:
        c.setdefault("params", {})
        if (c.get("disabled") or c.get("link_only")) and not include_disabled:
            continue
        out.append(c)
    return out


LINK_ONLY_REASONS = {
    "blocked": "官網阻擋自動化存取",
    "job_board": "官網職缺刊登於求職平台",
    "no_api": "官網無公開職缺資料",
}


def load_link_only() -> list[dict]:
    items = yaml.safe_load(COMPANIES_FILE.read_text())["companies"]
    return [c for c in items if c.get("link_only") and not c.get("disabled")]
