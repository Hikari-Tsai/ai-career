"""Stage 1 relevance: transparent, free, deterministic keyword scoring.

Every job gets a rule score 0-100 and a coarse category. Jobs above
CANDIDATE_THRESHOLD go on to the (optional) LLM stage in llm.py, which
refines the score and adds structured fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# (category, weight, pattern). Title hits count x3, description hits x1 (unique terms).
TERMS: list[tuple[str, float, str]] = [
    # LLM / GenAI
    ("llm_genai", 5, r"\bLLMs?\b|large language model|大型語言模型|大語言模型"),
    ("llm_genai", 4, r"generative ai|\bgen ?ai\b|生成式\s*AI|生成式人工智慧"),
    ("llm_genai", 3, r"\bRAG\b|retrieval[- ]augmented|prompt engineer|\bagentic\b|ai agents?\b|foundation models?"),
    ("llm_genai", 2, r"\btransformers?\b|fine[- ]?tun|\bRLHF\b|\bLoRA\b|vLLM|\bLangChain\b|embedding models?"),
    # ML engineering / research
    ("ml", 5, r"machine learning|deep learning|機器學習|深度學習"),
    ("ml", 4, r"\bML\b|\bAI\s*(?:/|and)?\s*ML\b|\bDL\b|neural networks?|類神經|神經網路"),
    ("ml", 3, r"applied scientist|research scientist|\bAI engineer|AI 工程師|AI工程師|人工智慧|AI\s*演算法"),
    ("ml", 4, r"\bAI\s*(?:/\s*ML\s*)?(?:軟體|software|應用|開發|研發|模型|model|algorithm|researcher|scientist|developer)"),
    ("ml", 2, r"\bPyTorch\b|\bTensorFlow\b|\bJAX\b|scikit-learn|\bXGBoost\b|model training|模型訓練"),
    # Perception / language / speech
    ("cv_nlp", 4, r"computer vision|電腦視覺|影像辨識|\bNLP\b|natural language|自然語言|speech recognition|語音辨識"),
    ("cv_nlp", 2, r"object detection|image recognition|\bASR\b|\bTTS\b|multimodal|多模態|perception"),
    # ML infra / systems
    ("mlops_infra", 4, r"\bMLOps\b|\bLLMOps\b|model serving|inference (?:engine|server|optimi[sz]ation)|distributed training"),
    ("mlops_infra", 3, r"\bCUDA\b|TensorRT|\bTriton\b|\bONNX\b|GPU kernel|\bNCCL\b|AI compiler|\bMLIR\b|\bTVM\b|quantization|量化"),
    # AI hardware (relevant but different track)
    ("ai_hw", 3, r"\bNPU\b|AI accelerator|AI chip|AI 晶片|AI晶片|\bTPU\b|tensor core"),
    ("ai_hw", 1.5, r"AI (?:server|ultraserver|infrastructure|systems?|platform)|GPU server|AI 伺服器|AI伺服器"),
    # Data science
    ("data_sci", 3, r"data scien|資料科學|數據科學|資料科學家"),
    ("data_sci", 1.5, r"\bAI\b|\bA\.I\.\b"),
]
_COMPILED = [(c, w, re.compile(p, re.I)) for c, w, p in TERMS]

# Title-only penalties for roles that mention AI but aren't building it.
NEGATIVE = re.compile(
    r"\bsales\b|account (?:manager|executive)|business development|\bBD\b|marketing|recruit|talent acquisition|"
    r"\bHR\b|human resources|accountant|accounting|finance manager|legal|counsel|procurement|buyer|sourcing|"
    r"customer service|admin(?:istrative)? assistant|業務|行銷|會計|法務|採購|人資|招募|客服|助理",
    re.I,
)
HW_ONLY = re.compile(r"mechanical|thermal|layout|package|packaging|\bPCB\b|\bNPI\b|manufactur|quality|supplier|機構|散熱|製造|品保", re.I)

CANDIDATE_THRESHOLD = 15
DISPLAY_THRESHOLD = 32  # without LLM; below this the rule stage is mostly "mentions AI tools" / AI-server hardware

CATEGORY_LABELS = {
    "llm_genai": "LLM / 生成式 AI",
    "ml": "機器學習 / AI 工程",
    "cv_nlp": "電腦視覺 / NLP / 語音",
    "mlops_infra": "ML 系統 / 推論優化",
    "ai_hw": "AI 晶片 / 硬體",
    "data_sci": "資料科學",
}


@dataclass
class RuleScore:
    score: float
    category: str
    matched: list[str]


def rule_score(title: str, description: str, department: str = "") -> RuleScore:
    head = f"{title} {department}"
    body = description or ""
    per_cat: dict[str, float] = {}
    matched: list[str] = []
    title_pts = desc_pts = 0.0
    for cat, w, rx in _COMPILED:
        th = rx.search(head)
        dh = rx.search(body) if not th else None
        if th:
            title_pts += w
            per_cat[cat] = per_cat.get(cat, 0) + 3 * w
            matched.append(th.group(0))
        elif dh:
            desc_pts += w
            per_cat[cat] = per_cat.get(cat, 0) + w
            matched.append(dh.group(0))
    score = 9 * title_pts + 3 * min(desc_pts, 14)
    if NEGATIVE.search(title):
        score *= 0.35
    if HW_ONLY.search(title) and title_pts < 4:
        score *= 0.5
    score = max(0.0, min(100.0, score))
    category = max(per_cat, key=per_cat.get) if per_cat else "none"
    return RuleScore(round(score, 1), category, sorted(set(m.strip() for m in matched), key=str.lower))
