"""Interviewer identity (name, title, company) used in the opening and the speaker persona."""
from typing import Any, Dict

# The interviewer is always the Linh avatar shown in the web client, in every language.
INTERVIEWER_NAME = "Linh"

_ROUND_TITLES = {
    "vi": {
        "hr": "chuyên viên tuyển dụng",
        "technical": "trưởng nhóm kỹ thuật",
        "hiring_manager": "quản lý tuyển dụng",
        "combined": "trưởng nhóm kỹ thuật",
    },
    "en": {
        "hr": "recruiter",
        "technical": "technical lead",
        "hiring_manager": "hiring manager",
        "combined": "technical lead",
    },
}


def _lang(language: str) -> str:
    return "vi" if str(language).lower().startswith("vi") else "en"


def interviewer_name(session_id: str, language: str = "vi") -> str:
    """Same name for every session and turn: the opening and the persona both introduce Linh."""
    return INTERVIEWER_NAME


def interviewer_title(spec: Dict[str, Any], interview_round: str = "", language: str = "vi") -> str:
    """Spoken title: first part of the generated persona ("Tech Lead / Staff" -> "Tech Lead"), else by round."""
    persona = " ".join(str(spec.get("interviewer_persona") or "").split())
    for separator in ("/", "|", "(", ","):
        persona = persona.split(separator)[0].strip()
    if persona and len(persona) <= 60:
        return persona
    titles = _ROUND_TITLES[_lang(language)]
    round_key = str(interview_round or spec.get("interview_round") or "combined").strip().lower()
    return titles.get(round_key, titles["combined"])
