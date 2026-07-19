"""Deterministic, title-based classification for NITC notices."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

CLASSIFICATION_VERSION = 1
# Priority at or above which a notice counts as urgent. Keep SQL filters and
# Classification.urgent reading from this single constant.
URGENT_PRIORITY = 3

CATEGORY_LABELS = {
    "deadline": "⏰ Deadline",
    "workshop": "🛠️ Workshop",
    "placement": "💼 Placement",
    "admin": "🏛️ Admin",
}
CATEGORY_COLORS = {
    "deadline": 0xE74C3C,
    "workshop": 0x3498DB,
    "placement": 0x2ECC71,
    "admin": 0x9B59B6,
}
CATEGORY_ORDER = tuple(CATEGORY_LABELS)

# These rules intentionally favor precision. They classify only from the title;
# linked PDFs are not downloaded by the watcher.
_PATTERNS: dict[str, tuple[str, ...]] = {
    "deadline": (
        r"\bdeadline\b",
        r"\blast date\b",
        r"\bclosing date\b",
        r"\bdue date\b",
        r"\bsubmit(?:ted)? by\b",
        r"\bapplications? close[sd]?\b",
        r"\bregistration close[sd]?\b",
        r"\bextended (?:up )?to\b",
        r"\bextension of (?:the )?(?:last date|deadline)\b",
        r"\bfee payment\b",
    ),
    "workshop": (
        r"\bworkshops?\b",
        r"\bseminars?\b",
        r"\bwebinars?\b",
        r"\bconferences?\b",
        r"\btraining programmes?\b",
        r"\btraining programs?\b",
        r"\bhackathons?\b",
        r"\bfaculty development programmes?\b",
        r"\bfaculty development programs?\b",
        r"\bfdp\b",
        r"\bshort term (?:course|programme|program)\b",
        r"\bsttp\b",
    ),
    "placement": (
        r"\bplacements?\b",
        r"\brecruitments?\b",
        r"\binternships?\b",
        r"\bcampus (?:hiring|drive|recruitment)\b",
        r"\bcareer development cent(?:re|er)\b",
        r"\bcent(?:re|er) for career development\b",
        r"\bcareer guidance and placement\b",
        r"\bccd\b",
        r"\bjob openings?\b",
        r"\baptitude tests?\b",
        r"\bpre placement talks?\b",
    ),
    "admin": (
        r"\bcirculars?\b",
        r"\boffice orders?\b",
        r"\bacademic registration\b",
        r"\bcourse registration\b",
        r"\bexamination (?:schedule|timetable|registration|notification)\b",
        r"\bexam (?:schedule|timetable|registration|notification)\b",
        r"\bscholarships?\b",
        r"\bhostel (?:allotment|admission|registration|fee|fees|notice)\b",
        r"\btuition fee(?:s)?\b",
        r"\bsemester fee(?:s)?\b",
        r"\bid cards?\b",
        r"\bno dues\b",
        r"\bconvocation\b",
    ),
}
_COMPILED = {
    category: tuple(re.compile(pattern) for pattern in patterns)
    for category, patterns in _PATTERNS.items()
}


@dataclass(frozen=True)
class Classification:
    tags: tuple[str, ...]
    priority: int
    version: int = CLASSIFICATION_VERSION

    @property
    def urgent(self) -> bool:
        return self.priority >= URGENT_PRIORITY


def normalize_title(title: str) -> str:
    """Normalize Unicode and punctuation while retaining word boundaries."""
    text = unicodedata.normalize("NFKC", title).casefold()
    text = re.sub(r"[-–—_/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_notice(title: str) -> Classification:
    text = normalize_title(title)
    tags = tuple(
        category
        for category in CATEGORY_ORDER
        if any(pattern.search(text) for pattern in _COMPILED[category])
    )
    if "deadline" in tags:
        priority = 3
    elif "placement" in tags:
        priority = 2
    elif tags:
        priority = 1
    else:
        priority = 0
    return Classification(tags=tags, priority=priority)


def tag_labels(tags: tuple[str, ...] | list[str]) -> str:
    return " · ".join(CATEGORY_LABELS[tag] for tag in CATEGORY_ORDER if tag in tags)


def primary_color(tags: tuple[str, ...] | list[str], fallback: int = 0x5865F2) -> int:
    for tag in CATEGORY_ORDER:
        if tag in tags:
            return CATEGORY_COLORS[tag]
    return fallback
