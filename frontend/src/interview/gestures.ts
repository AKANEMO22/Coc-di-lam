// Avatar gesture tags in interviewer text ("[nod] Cảm ơn bạn."). Same rules as core_agent/core/interview/gestures.py:
// every short "[...]" / "{...}" span and tag-like "<...>" is removed, only known names become gestures.

export const GESTURES = ["nod", "smile", "think", "write", "clarify", "disagree", "wave", "listen"] as const;
export type Gesture = (typeof GESTURES)[number];

const ALIASES: Record<string, Gesture> = {
  nods: "nod", nodding: "nod", head_nod: "nod", "gật đầu": "nod", "gat dau": "nod",
  smiles: "smile", smiling: "smile", happy: "smile", "mỉm cười": "smile", "cười": "smile",
  thinking: "think", thoughtful: "think", "suy nghĩ": "think",
  writing: "write", note: "write", notes: "write", take_note: "write", "ghi chép": "write",
  tilt: "clarify", head_tilt: "clarify", curious: "clarify", confused: "clarify", "nghiêng đầu": "clarify",
  shake: "disagree", head_shake: "disagree", "lắc đầu": "disagree",
  greet: "wave", hello: "wave", bye: "wave", goodbye: "wave", "vẫy tay": "wave", "chào": "wave",
  neutral: "listen", idle: "listen", reset: "listen", listening: "listen", "lắng nghe": "listen",
};
const PREFIX = /^(?:gesture|gestures|action|g|cử chỉ|cu chi)\s*[:=]\s*/;
const OPENERS = "[<{";
const CLOSERS = "]>}";
const MAX_TAG_CHARS = 40;

export function gestureName(content: string): Gesture | null {
  const trimmed = content.replace(/^[\s/[<{\]>}]+|[\s/[<{\]>}]+$/g, "").toLowerCase().split(/\s+/).join(" ");
  const key = trimmed.replace(PREFIX, "").replace(/^[\s/]+|[\s/]+$/g, "");
  if ((GESTURES as readonly string[]).includes(key)) return key as Gesture;
  return ALIASES[key] ?? ALIASES[key.replace(/ /g, "_")] ?? ALIASES[key.replace(/_/g, " ")] ?? null;
}

const looksLikeTag = (content: string) => /^[\p{L}\s_\-:=/,|+]*$/u.test(content);

export interface Parsed {
  text: string;
  gestures: Gesture[];
}

/**
 * Split a (possibly still streaming) reply into display text and gestures.
 * `final: false` hides an unclosed tag at the end ("Chào [smi") until more tokens arrive.
 */
export function parseGestures(raw: string, final = true): Parsed {
  let text = "";
  const gestures: Gesture[] = [];
  let afterTag = false;
  let i = 0;
  while (i < raw.length) {
    const char = raw[i];
    if (afterTag && CLOSERS.includes(char)) { i++; continue; } // "[[nod]]"
    if (!OPENERS.includes(char)) { afterTag = false; text += char; i++; continue; }
    let close = -1;
    for (let j = i + 1; j < Math.min(raw.length, i + 1 + MAX_TAG_CHARS); j++) {
      if (CLOSERS.includes(raw[j]) || raw[j] === "\n") { close = j; break; }
    }
    if (close >= 0 && raw[close] !== "\n" && (char !== "<" || looksLikeTag(raw.slice(i + 1, close)))) {
      for (const part of raw.slice(i + 1, close).split(/[,|+;]/)) {
        const name = gestureName(part);
        if (name) gestures.push(name);
      }
      afterTag = true;
      i = close + 1;
      continue;
    }
    const pending = raw.length - i <= MAX_TAG_CHARS && !raw.slice(i).includes("\n");
    if (pending && (!final || looksLikeTag(raw.slice(i + 1)))) break; // dangling "[smi"
    afterTag = false;
    text += char;
    i++;
  }
  return { text: tidy(text), gestures };
}

export const stripGestures = (raw: string, final = true) => parseGestures(raw, final).text;

function tidy(text: string): string {
  return text
    .replace(/[ \t]+([,.!?…;:])/g, "$1")
    .replace(/[ \t]{2,}/g, " ")
    .split("\n").map((line) => line.trim()).join("\n")
    .trim();
}
