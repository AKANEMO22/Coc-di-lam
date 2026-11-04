"""Cut streamed interviewer tokens into sentences for TTS, so speech starts before the turn ends."""
from __future__ import annotations

import re

_END = re.compile(r"[.!?…]+[\"'”)\]]*(?=\s)|\n+")
_MARKDOWN = re.compile(r"[*_`#>|]+|\[(.*?)\]\(.*?\)")
FIRST_MIN_CHARS = 8  # avoid speaking "Vâng." alone, then a long gap
SOFT_MAX_CHARS = 220  # cut long run-ons at a comma/space so one TTS call stays short


def clean_for_speech(text: str) -> str:
    text = _MARKDOWN.sub(lambda m: m.group(1) or " ", text)
    return re.sub(r"\s+", " ", text).strip()


class SentenceSplitter:
    def __init__(self) -> None:
        self.buffer = ""
        self.emitted = 0

    def feed(self, token: str) -> list[str]:
        self.buffer += token
        out: list[str] = []
        while True:
            cut = self._cut()
            if cut is None:
                break
            sentence = clean_for_speech(self.buffer[:cut])
            self.buffer = self.buffer[cut:]
            if sentence:
                out.append(sentence)
                self.emitted += 1
        return out

    def flush(self) -> list[str]:
        sentence = clean_for_speech(self.buffer)
        self.buffer = ""
        if sentence:
            self.emitted += 1
            return [sentence]
        return []

    def _cut(self) -> int | None:
        minimum = FIRST_MIN_CHARS if self.emitted == 0 else 1
        for match in _END.finditer(self.buffer):
            if len(self.buffer[:match.end()].strip()) >= minimum:
                return match.end()
        if len(self.buffer) > SOFT_MAX_CHARS:
            head = self.buffer[:SOFT_MAX_CHARS]
            at = max(head.rfind(", "), head.rfind("; "))
            at = at + 1 if at > SOFT_MAX_CHARS // 2 else head.rfind(" ")
            return at if at > 0 else SOFT_MAX_CHARS
        return None
