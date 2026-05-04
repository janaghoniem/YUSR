import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


_WORD_RE = re.compile(r"[a-zA-Z\u0600-\u06FF0-9_]+")


@dataclass
class IntentMatch:
    label: str
    score: float


def normalize_text(text: str) -> str:
    value = (text or "").strip().lower()
    value = value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    value = value.replace("ى", "ي").replace("ة", "ه")
    value = re.sub(r"\s+", " ", value)
    return value


def tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(normalize_text(text))


def _fuzzy(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def semantic_phrase_score(text: str, candidates: Sequence[str]) -> float:
    norm = normalize_text(text)
    if not norm:
        return 0.0

    score = 0.0
    for candidate in candidates:
        c = normalize_text(candidate)
        if not c:
            continue
        if c in norm or norm in c:
            score = max(score, 1.0)
            continue
        score = max(score, _fuzzy(norm, c))

        # Token overlap gives semantic signal for longer phrases.
        source_tokens = set(tokenize(norm))
        cand_tokens = set(tokenize(c))
        if source_tokens and cand_tokens:
            overlap = len(source_tokens & cand_tokens) / len(source_tokens | cand_tokens)
            score = max(score, overlap)

    return min(score, 1.0)


def classify_polar_intent(text: str) -> IntentMatch:
    yes_candidates = (
        "yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "proceed", "approve", "confirmed",
        "نعم", "ايوه", "ايوا", "تمام", "موافق", "اكيد", "اتفضل",
    )
    no_candidates = (
        "no", "nope", "not now", "skip", "cancel", "stop", "dont", "do not", "negative",
        "لا", "مش", "الغاء", "الغيه", "وقف", "مش لازم", "تخطي",
    )

    yes_score = semantic_phrase_score(text, yes_candidates)
    no_score = semantic_phrase_score(text, no_candidates)

    if yes_score >= 0.72 and yes_score > no_score:
        return IntentMatch("affirmative", yes_score)
    if no_score >= 0.72 and no_score > yes_score:
        return IntentMatch("negative", no_score)
    return IntentMatch("unknown", max(yes_score, no_score))


def classify_draft_reading_intent(text: str) -> IntentMatch:
    read_candidates = (
        "read", "read aloud", "listen", "go ahead", "continue reading", "say it",
        "اقرا", "اقراه", "اقرأ", "اسمع", "كمل", "اقراها",
    )
    skip_candidates = (
        "skip reading", "do not read", "no need", "not necessary", "skip", "dont read",
        "مش لازم", "لا تقرا", "تخطي القراءة", "ملهاش لازمه",
    )
    approve_candidates = (
        "approve", "that is enough", "enough", "looks good", "confirm", "finalize",
        "وافق", "كفايه", "خلاص", "اعتمد", "تمام",
    )
    continue_candidates = (
        "continue", "go on", "keep reading", "continue reading", "not yet", "next page",
        "كمل", "استمر", "كمل قراءة", "لسه", "الصفحه التاليه",
    )

    scores = {
        "read": semantic_phrase_score(text, read_candidates),
        "skip": semantic_phrase_score(text, skip_candidates),
        "approve": semantic_phrase_score(text, approve_candidates),
        "continue": semantic_phrase_score(text, continue_candidates),
    }

    label, score = max(scores.items(), key=lambda item: item[1])
    if score < 0.7:
        return IntentMatch("unknown", score)
    return IntentMatch(label, score)


def infer_task_type(prompt: str, context: Optional[str] = None) -> str:
    norm = normalize_text(f"{prompt or ''} {context or ''}")
    if any(k in norm for k in ("presentation", "powerpoint", "slides", "slide deck", "ppt")):
        return "presentation"
    if any(k in norm for k in ("essay", "document", "word", "article", "report", "summary")):
        return "document"
    if any(k in norm for k in ("excel", "spreadsheet", "sheet", "table", "csv")):
        return "excel"
    return "generic"


def paginate_content(content: str, task_type: str = "generic", page_size: int = 2000) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
    text = (content or "").strip()
    if not text:
        return {"content": {"pages": []}}

    pages: List[str] = []

    if task_type == "presentation":
        chunks = [chunk.strip() for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]
        for chunk in chunks:
            bullets = [line.strip(" -•\t") for line in chunk.splitlines() if line.strip()]
            if bullets:
                pages.append("\n".join(f"• {line}" for line in bullets[:6]))
    elif task_type == "excel":
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        group_size = max(6, min(20, math.floor(page_size / 40)))
        for i in range(0, len(rows), group_size):
            pages.append("\n".join(rows[i:i + group_size]))
    else:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        current = []
        current_len = 0
        for paragraph in paragraphs:
            p_len = len(paragraph)
            if current and current_len + p_len + 2 > page_size:
                pages.append("\n\n".join(current))
                current = [paragraph]
                current_len = p_len
            else:
                current.append(paragraph)
                current_len += p_len + 2
        if current:
            pages.append("\n\n".join(current))

    if not pages:
        pages = [text[i:i + page_size] for i in range(0, len(text), page_size)]

    return {
        "content": {
            "pages": [
                {
                    "content": page,
                    "pageNumber": idx + 1,
                }
                for idx, page in enumerate(pages)
            ]
        }
    }


def relevance_score(user_input: str, reference_text: str) -> float:
    src = set(tokenize(user_input))
    ref = set(tokenize(reference_text))
    if not src or not ref:
        return 0.0

    overlap = len(src & ref) / len(src | ref)
    fuzzy = _fuzzy(normalize_text(user_input), normalize_text(reference_text))
    return max(overlap, fuzzy * 0.8)


def is_relevant_to_task(user_input: str, reference_text: str, threshold: float = 0.26) -> bool:
    return relevance_score(user_input, reference_text) >= threshold


def classify_interrupt_semantic(text: str, command_map: Dict[str, str]) -> Optional[str]:
    norm = normalize_text(text)
    if not norm:
        return None

    # Exact/prefix first, semantic fallback second.
    for phrase, command in command_map.items():
        p = normalize_text(phrase)
        if norm == p or norm.startswith(p):
            return command

    best_command = None
    best_score = 0.0
    for phrase, command in command_map.items():
        score = semantic_phrase_score(norm, (phrase,))
        if score > best_score:
            best_score = score
            best_command = command

    return best_command if best_score >= 0.82 else None
