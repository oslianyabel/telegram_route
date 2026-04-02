from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class PendingSurvey:
    contact_id: str
    experience_id: str
    slot_id: str
    ticket_id: str


@dataclass(slots=True)
class SurveyFeedback:
    rating: int
    comment: str | None = None


_pending_surveys: dict[str, PendingSurvey] = {}

_EXPLICIT_RATING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:^|\b)(?:mi\s+)?(?:calificacion|calificación|valoracion|valoración|puntuacion|puntuación|puntaje|rating|le\s+doy|doy|un)\s*[:=-]?\s*([1-5])(?:\s*/\s*5|\s+de\s+5|\s+estrellas?)?(?:\b|$)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|\b)([1-5])\s*/\s*5(?:\b|$)", re.IGNORECASE),
    re.compile(r"(?:^|\b)([1-5])\s+de\s+5(?:\b|$)", re.IGNORECASE),
    re.compile(r"(?:^|\b)([1-5])\s+estrellas?(?:\b|$)", re.IGNORECASE),
)

_INFERRED_RATING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (
        re.compile(
            r"\b(excelente|espectacular|increible|increíble|maravillos[ao]|buenisim[ao]|buenísim[ao]|me\s+encant[oó]|nos\s+encant[oó])\b",
            re.IGNORECASE,
        ),
        5,
    ),
    (
        re.compile(
            r"\b(muy\s+buen[ao]|muy\s+lind[ao]|me\s+gust[oó]\s+mucho|recomendable|muy\s+recomendable|muy\s+disfrutable)\b",
            re.IGNORECASE,
        ),
        4,
    ),
    (
        re.compile(
            r"\b(estuvo\s+bien|bien\b|normal|regular|mas\s+o\s+menos|m[aá]s\s+o\s+menos)\b",
            re.IGNORECASE,
        ),
        3,
    ),
    (
        re.compile(
            r"\b(mal[ao]|floj[ao]|decepcionante|decepcion[oó]|no\s+me\s+gust[oó])\b",
            re.IGNORECASE,
        ),
        2,
    ),
    (
        re.compile(
            r"\b(horrible|p[eé]sim[ao]|terrible|espantos[ao])\b",
            re.IGNORECASE,
        ),
        1,
    ),
)

_COMMENT_PREFIX_RE = re.compile(
    r"^(?:mi\s+)?(?:calificacion|calificación|valoracion|valoración|puntuacion|puntuación|puntaje|rating|es|fue|un|una)\s*[:=-]?\s*",
    re.IGNORECASE,
)


def set_pending_survey(phone: str, survey: PendingSurvey) -> None:
    _pending_surveys[phone] = survey


def get_pending_survey(phone: str) -> PendingSurvey | None:
    return _pending_surveys.get(phone)


def clear_pending_survey(phone: str) -> None:
    _pending_surveys.pop(phone, None)


def extract_survey_feedback(message: str) -> SurveyFeedback | None:
    normalized = " ".join(message.split())
    if not normalized:
        return None

    if normalized in {"1", "2", "3", "4", "5"}:
        return SurveyFeedback(rating=int(normalized))

    leading_rating_match = re.match(r"^([1-5])(?:\s+|[,:;.-]+\s*)(.+)$", normalized)
    if leading_rating_match:
        comment = _clean_comment(leading_rating_match.group(2))
        if comment and _looks_like_feedback_text(comment):
            return SurveyFeedback(
                rating=int(leading_rating_match.group(1)), comment=comment
            )

    for pattern in _EXPLICIT_RATING_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue

        rating = int(match.group(1))
        comment = _clean_comment(
            f"{normalized[: match.start()]} {normalized[match.end() :]}"
        )
        return SurveyFeedback(rating=rating, comment=comment)

    for pattern, rating in _INFERRED_RATING_PATTERNS:
        if pattern.search(normalized):
            return SurveyFeedback(rating=rating, comment=normalized)

    return None


def _clean_comment(raw_comment: str) -> str | None:
    comment = _COMMENT_PREFIX_RE.sub("", raw_comment.strip(" ,.;:-"))
    comment = comment.strip(" ,.;:-")
    return comment or None


def _looks_like_feedback_text(text: str) -> bool:
    feedback_hints = (
        "experiencia",
        "actividad",
        "servicio",
        "recomend",
        "gust",
        "encant",
        "excelente",
        "espectacular",
        "maravill",
        "bien",
        "mal",
        "horrible",
        "terrible",
        "pesim",
        "pésim",
        "regular",
    )
    lowered = text.lower()
    if any(hint in lowered for hint in feedback_hints):
        return True
    return any(pattern.search(text) for pattern, _rating in _INFERRED_RATING_PATTERNS)
