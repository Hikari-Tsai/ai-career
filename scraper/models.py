from __future__ import annotations

import hashlib
import html
import re
from dataclasses import asdict, dataclass, field

TW_PATTERN = re.compile(
    r"taiwan|taipei|hsinchu|hsin-chu|taichung|tainan|kaohsiung|taoyuan|zhubei|chupei|jhubei|linkou|"
    r"xizhi|hsichih|neihu|banqiao|new taipei|\bTWN?\b|台灣|臺灣|台北|臺北|新竹|竹北|台中|臺中|台南|臺南|高雄|桃園|新北",
    re.I,
)

CITY_MAP = [
    ("新竹", r"hsinchu|hsin-chu|zhubei|chupei|jhubei|新竹|竹北|竹科"),
    ("新北", r"new taipei|banqiao|xizhi|hsichih|linkou|tamsui|新北|汐止|板橋|林口"),
    ("台北", r"taipei|neihu|台北|臺北|內湖|\bTPE\b"),
    ("桃園", r"taoyuan|桃園|龜山|guishan"),
    ("台中", r"taichung|台中|臺中"),
    ("台南", r"tainan|台南|臺南"),
    ("高雄", r"kaohsiung|高雄"),
]


def is_taiwan(location: str) -> bool:
    return bool(location) and bool(TW_PATTERN.search(location))


def city_of(location: str) -> str:
    for city, pat in CITY_MAP:
        if re.search(pat, location or "", re.I):
            return city
    return "台灣" if is_taiwan(location) else "其他"


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", raw)
    text = html.unescape(_TAG.sub(" ", text))
    return _WS.sub(" ", text).strip()


@dataclass
class Job:
    company_id: str
    source_id: str  # id unique within the company's career site
    title: str
    url: str
    location: str = ""
    department: str = ""
    posted_at: str = ""  # ISO date if known
    description: str = ""  # plain text, may be empty if the source has no detail endpoint
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.company_id}:{self.source_id}"

    @property
    def content_hash(self) -> str:
        blob = f"{self.title}\n{self.description[:6000]}"
        return hashlib.sha1(blob.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["uid"] = self.uid
        d["city"] = city_of(self.location)
        return d
