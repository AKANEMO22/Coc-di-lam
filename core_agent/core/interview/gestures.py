"""Gesture tags the speaker embeds in its reply to animate the Linh avatar, e.g. "[smile] Chào bạn.".

Tags are presentation only. The streamed tokens keep them for the client, but the stored
transcript, assessment, summaries and reports use `strip_gestures(text)`. Parsing is
lenient because the model sometimes writes "[[nod]]", "<nod/>", "{gesture: nod}" or an
unknown name: every short "[...]" / "{...}" span and tag-like "<...>" is removed; only known
names become gestures.
A copy of this module lives in the gateway (backend/app/gestures.py); keep them in sync.
"""
from __future__ import annotations

import re
from typing import List, Tuple, Union

GESTURES = ("nod", "smile", "think", "write", "clarify", "disagree", "wave", "listen")

_ALIASES = {
    "nods": "nod", "nodding": "nod", "head_nod": "nod", "gật đầu": "nod", "gat dau": "nod",
    "smiles": "smile", "smiling": "smile", "happy": "smile", "mỉm cười": "smile", "cười": "smile",
    "thinking": "think", "thoughtful": "think", "suy nghĩ": "think",
    "writing": "write", "note": "write", "notes": "write", "take_note": "write", "ghi chép": "write",
    "tilt": "clarify", "head_tilt": "clarify", "curious": "clarify", "confused": "clarify", "nghiêng đầu": "clarify",
    "shake": "disagree", "head_shake": "disagree", "lắc đầu": "disagree",
    "greet": "wave", "hello": "wave", "bye": "wave", "goodbye": "wave", "vẫy tay": "wave", "chào": "wave",
    "neutral": "listen", "idle": "listen", "reset": "listen", "listening": "listen", "lắng nghe": "listen",
}
_PREFIX = re.compile(r"^(?:gesture|gestures|action|g|cử chỉ|cu chi)\s*[:=]\s*")
_OPENERS = "[<{"
_CLOSERS = "]>}"
MAX_TAG_CHARS = 40  # an opener without a closer this close is ordinary text

Segment = Union[str, Tuple[str, str]]  # text, or ("gesture", name)


def gesture_name(content: str) -> str | None:
    """Known gesture for a tag body ("nod", " Gesture: Nod ", "/nod"), else None."""
    key = _PREFIX.sub("", " ".join(content.strip(" /\t" + _OPENERS + _CLOSERS).casefold().split()))
    key = key.strip(" /")
    if key in GESTURES:
        return key
    return _ALIASES.get(key) or _ALIASES.get(key.replace(" ", "_")) or _ALIASES.get(key.replace("_", " "))


def _tag_names(content: str) -> List[str]:
    names = (gesture_name(part) for part in re.split(r"[,|+;]", content))
    return [name for name in names if name]


class GestureStream:
    """Incremental tag filter for streamed tokens: `feed` returns text and gestures in order.

    Text after an unclosed opener is held back until the tag closes or clearly is not one,
    so a tag split across tokens ("[no" + "d]") never leaks into speech or the screen.
    """

    def __init__(self) -> None:
        self.buffer = ""
        self._after_tag = False

    def feed(self, token: str) -> List[Segment]:
        self.buffer += token
        return self._drain(final=False)

    def flush(self) -> List[Segment]:
        return self._drain(final=True)

    def _drain(self, final: bool) -> List[Segment]:
        out: List[Segment] = []
        text = ""
        buf = self.buffer
        i = 0
        while i < len(buf):
            char = buf[i]
            if self._after_tag and char in _CLOSERS:  # "[[nod]]": stray closers after a tag
                i += 1
                continue
            if char not in _OPENERS:
                self._after_tag = False
                text += char
                i += 1
                continue
            close = next((j for j in range(i + 1, min(len(buf), i + 1 + MAX_TAG_CHARS))
                          if buf[j] in _CLOSERS or buf[j] == "\n"), None)
            # "<...>" must look like a tag ("x < 5 > y" is speech); "[1]" / "{x}" are never spoken.
            if close is not None and buf[close] != "\n" and (char != "<" or _looks_like_tag(buf[i + 1:close])):
                if text:
                    out.append(text)
                    text = ""
                out.extend(("gesture", name) for name in _tag_names(buf[i + 1:close]))
                self._after_tag = True
                i = close + 1
                continue
            pending = len(buf) - i <= MAX_TAG_CHARS and "\n" not in buf[i:]
            if pending and not final:
                break  # wait for more tokens
            if pending and final and _looks_like_tag(buf[i + 1:]):
                i = len(buf)  # dangling "[smi" at the end of the reply
                break
            self._after_tag = False
            text += char  # not a tag: keep the opener as text
            i += 1
        self.buffer = buf[i:]
        if text:
            out.append(text)
        return out


def _looks_like_tag(content: str) -> bool:
    return all(c.isalpha() or c in " _-:=/,|+" for c in content)


def split_gestures(text: str) -> Tuple[str, List[str]]:
    """(text without tags, gestures in order) for a complete reply."""
    stream = GestureStream()
    segments = [*stream.feed(text), *stream.flush()]
    plain = "".join(s for s in segments if isinstance(s, str))
    return tidy(plain), [s[1] for s in segments if not isinstance(s, str)]


def strip_gestures(text: str) -> str:
    return split_gestures(text)[0]


def tidy(text: str) -> str:
    """Collapse the gaps a removed tag leaves ("bạn  ." -> "bạn.") without touching line breaks."""
    text = re.sub(r"[ \t]+([,.!?…;:])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()
