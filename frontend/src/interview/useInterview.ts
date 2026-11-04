// Voice interview over the gateway WebSocket: microphone-toggle answers, streamed interviewer text and audio.
// Interviewer text keeps its avatar gesture tags here (callers strip them for display); gestures are
// played through `onGesture` when the sentence that carries them starts to be heard. While a reply is
// voiced, `voiced` hides it; the whole reply appears once Linh has finished saying it.
import { useCallback, useEffect, useRef, useState } from "react";
import { Microphone, PcmPlayer, audioContext } from "./audio";
import { clearSession, getToken } from "./auth";
import type { Fluency, Message } from "./api";
import { GESTURES, type Gesture } from "./gestures";

export type Phase = "connecting" | "idle" | "recording" | "transcribing" | "waiting" | "speaking";

const REFRESH_EVENTS = new Set(["turn_complete", "stage_transition", "session_failed", "session_cancelled",
  "report_ready", "dialogue_completed", "state_changed", "session_ended"]);
const timestampMs = (value: unknown): number | null => {
  if (typeof value === "number") return value * 1000;
  const parsed = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isNaN(parsed) ? null : parsed;
};

// Attached-document text sent with an answer. Core takes at most 20 000 characters per answer (gateway
// MAX_ANSWER_CHARS), and the spoken or typed answer itself needs room too.
export const ANSWER_CONTEXT_LIMIT = 15_000;

const turnOf = (turnId: unknown): number | null => {
  const match = String(turnId ?? "").match(/(\d+)/);
  return match ? Number(match[1]) : null;
};

interface Options {
  sessionId: string;
  enabled: boolean; // open the socket (session not terminal)
  afterSequence: number;
  onRefresh: () => void; // re-read the session over REST
  onGesture?: (gesture: Gesture) => void; // avatar gesture, timed with the interviewer audio
}

export function useInterview({ sessionId, enabled, afterSequence, onRefresh, onGesture }: Options) {
  const [phase, setPhase] = useState<Phase>("connecting");
  const [connected, setConnected] = useState(false);
  const [interim, setInterim] = useState("");
  const [live, setLive] = useState<Map<number, Message>>(new Map()); // interviewer text not yet saved
  const [sent, setSent] = useState<Message | null>(null); // candidate answer not yet saved
  const [lastFluency, setLastFluency] = useState<Fluency | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [micError, setMicError] = useState<string | null>(null);
  const [level, setLevel] = useState(0);
  const [voiced, setVoiced] = useState<Map<number, string>>(new Map()); // turn -> sentences heard so far

  const ws = useRef<WebSocket | null>(null);
  const mic = useRef(new Microphone());
  const player = useRef(new PcmPlayer());
  const recording = useRef(false);
  const sequence = useRef(afterSequence);
  const waiting = useRef(false);
  const ttsBusy = useRef(false);
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;
  const gesture = useRef(onGesture);
  gesture.current = onGesture;
  const gestureTimers = useRef(new Set<number>());
  const ttsEnabled = useRef(false);
  const readyAt = useRef(0);
  const nextAnswerContext = useRef("");
  sequence.current = Math.max(sequence.current, afterSequence);

  const settle = useCallback(() => {
    if (recording.current) return;
    setPhase(waiting.current ? "waiting" : ttsBusy.current || player.current.playing ? "speaking" : "idle");
  }, []);

  useEffect(() => {
    player.current.onIdle = () => { if (!ttsBusy.current) settle(); };
  }, [settle]);

  /** Run `action` once the audio queued so far has been heard; cancelled by `silence`. */
  const afterAudio = useCallback((action: () => void, extraMs = 0) => {
    const timer = window.setTimeout(() => {
      gestureTimers.current.delete(timer);
      action();
    }, player.current.queuedSeconds() * 1000 + extraMs);
    gestureTimers.current.add(timer);
  }, []);

  /** Play gestures when the next sentence starts, i.e. once the audio queued so far has been heard. */
  const scheduleGestures = useCallback((names: unknown) => {
    if (!Array.isArray(names)) return;
    let delay = 60;
    for (const name of names) {
      if (!(GESTURES as readonly unknown[]).includes(name)) continue;
      afterAudio(() => gesture.current?.(name as Gesture), delay);
      delay += 1200; // two tags in one sentence: one after the other
    }
  }, [afterAudio]);

  const holdTurn = useCallback((turn: number) => {
    setVoiced((prev) => (prev.has(turn) ? prev : new Map(prev).set(turn, "")));
  }, []);

  const releaseTurn = useCallback((turn: number) => {
    setVoiced((prev) => {
      if (!prev.has(turn)) return prev;
      const next = new Map(prev);
      next.delete(turn);
      return next;
    });
  }, []);

  const releaseAll = useCallback(() => setVoiced((prev) => (prev.size ? new Map() : prev)), []);

  /** Barge-in or stop: the interviewer stops talking and gesturing at once; its text shows in full. */
  const silence = useCallback(() => {
    player.current.stop();
    for (const timer of gestureTimers.current) window.clearTimeout(timer);
    gestureTimers.current.clear();
    releaseAll();
  }, [releaseAll]);

  const react = useCallback((name: Gesture) => gesture.current?.(name), []);

  // ---- socket ----
  useEffect(() => {
    if (!enabled) return;
    let closed = false;
    let attempts = 0;
    let timer: number | undefined;
    const safetyTimers = new Set<number>();

    const open = () => {
      const url = new URL(`/ws/sessions/${encodeURIComponent(sessionId)}`, location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.searchParams.set("after", String(sequence.current));
      const token = getToken();
      if (token) url.searchParams.set("token", token);
      const socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";
      ws.current = socket;
      setPhase("connecting");

      socket.onmessage = (message) => {
        if (message.data instanceof ArrayBuffer) {
          // Audio already in flight when the candidate took the floor: never play it over their answer.
          if (recording.current) return;
          ttsBusy.current = true;
          player.current.push(message.data);
          if (!waiting.current) setPhase("speaking");
          return;
        }
        let data: any;
        try { data = JSON.parse(message.data); } catch { return; }
        switch (data.type) {
          case "ready":
            attempts = 0;
            ttsEnabled.current = data.tts_enabled !== false;
            readyAt.current = Date.now();
            // A new connection starts clean: an answer in flight on the old one either reached core (the
            // replay shows it) or can be given again; a key held across the drop no longer records.
            recording.current = false;
            waiting.current = false;
            setConnected(true);
            setError(null);
            if (nextAnswerContext.current) socket.send(JSON.stringify({ type: "answer_context", text: nextAnswerContext.current }));
            settle();
            break;
          case "core": handleCore(data.event); break;
          case "stt_interim": setInterim(data.text || ""); break;
          case "stt_final":
            setInterim(data.text || "");
            if (!data.text) {
              setError("Không nghe rõ câu trả lời. Bật micro và thử nói lại nhé.");
              waiting.current = false;
              settle();
              react("disagree");
            }
            else if (data.fluency) setLastFluency(data.fluency);
            break;
          case "answer_sent":
            nextAnswerContext.current = "";
            waiting.current = true;
            setSent({ turn: data.expected_turn, role: "candidate", text: data.text, fluency: data.fluency ?? undefined, live: true });
            setPhase("waiting");
            break;
          case "answer_accepted":
            waiting.current = false;
            settle();
            break;
          case "tts_sentence": {
            player.current.setRate(data.sample_rate);
            ttsBusy.current = true;
            scheduleGestures(data.gestures);
            const turn = turnOf(data.turn_id);
            if (turn != null) holdTurn(turn);
            break;
          }
          case "gesture": scheduleGestures(data.gestures); break;
          case "tts_idle":
            ttsBusy.current = false;
            // Everything is queued: the whole reply appears once Linh has finished saying it.
            afterAudio(releaseAll, 80);
            if (!player.current.playing) settle();
            break;
          case "tts_stop": ttsBusy.current = false; silence(); break;
          case "error":
            setError(data.detail || "Có lỗi xảy ra.");
            if (data.code === "STT_FAILED") { waiting.current = false; settle(); react("disagree"); }
            break;
        }
      };
      socket.onclose = (event) => {
        if (ws.current === socket) ws.current = null;
        setConnected(false);
        recording.current = false;
        if (closed) return;
        setPhase("connecting");
        if (event.code === 4401) { closed = true; clearSession(); return; }
        if (event.code === 4403 || event.code === 4404) {
          closed = true;
          setError(event.code === 4404 ? "Không tìm thấy phiên này trong tài khoản của bạn." : "Địa chỉ trang này không được phép kết nối.");
          return;
        }
        const delay = event.code === 4429 ? 30000 : Math.min(15000, 1000 * 2 ** attempts++);
        timer = window.setTimeout(open, delay);
      };
    };

    const handleCore = (event: any) => {
      if (event.sequence) sequence.current = Math.max(sequence.current, Number(event.sequence));
      const payload = event.payload || {};
      if (event.type === "response_token") {
        const turn = payload.turn_number ?? turnOf(event.turn_id);
        if (turn == null) return;
        // A reply generated now will be spoken: hide it until heard (replayed history shows at once).
        const at = timestampMs(event.timestamp);
        if (ttsEnabled.current && at !== null && at >= readyAt.current - 1500) holdTurn(turn);
        setLive((prev) => {
          const next = new Map(prev);
          const current = next.get(turn) ?? { turn, role: "interviewer" as const, text: "", live: true };
          next.set(turn, { ...current, text: current.text + (payload.token || "") });
          return next;
        });
      } else if (event.type === "turn_complete") {
        const turn = payload.turn_number ?? turnOf(event.turn_id) ?? 0;
        setLive((prev) => new Map(prev).set(turn, { turn, role: "interviewer", text: payload.interviewer_response || prev.get(turn)?.text || "", live: false }));
        if (payload.interrupted) releaseTurn(turn);
        // Safety net: never keep a reply hidden when no audio ever comes for it.
        const safety = window.setTimeout(() => {
          safetyTimers.delete(safety);
          if (!ttsBusy.current && !player.current.playing) releaseTurn(turn);
        }, 15000);
        safetyTimers.add(safety);
      } else if (event.type === "error" && payload.code && !["TURN_IN_PROGRESS", "GENERATION_BUSY"].includes(payload.code)) {
        setError(`Core: ${payload.message || payload.code}`);
        waiting.current = false;
        settle();
      }
      if (REFRESH_EVENTS.has(event.type)) refresh.current();
    };

    open();
    return () => {
      closed = true;
      window.clearTimeout(timer);
      for (const safety of safetyTimers) window.clearTimeout(safety);
      safetyTimers.clear();
      ws.current?.close();
      ws.current = null;
      silence();
    };
  }, [enabled, sessionId, settle, scheduleGestures, silence, react, holdTurn, releaseTurn, releaseAll, afterAudio]);

  // ---- microphone ----
  useEffect(() => {
    const m = mic.current;
    m.onFrame = (frame) => {
      if (recording.current && ws.current?.readyState === WebSocket.OPEN) ws.current.send(frame);
    };
    const meter = window.setInterval(() => setLevel(recording.current ? m.level : 0), 80);
    return () => { window.clearInterval(meter); m.close(); };
  }, []);

  const prepareAudio = useCallback(async () => {
    audioContext();
    try { await mic.current.open(); setMicError(null); return true; }
    catch (e) { setMicError(`Không mở được micro: ${(e as Error).message}`); return false; }
  }, []);

  const startRecordingAudio = useCallback(async () => {
    if (!(await prepareAudio())) throw new Error("Hãy cấp quyền micro để ghi âm buổi phỏng vấn.");
    const stream = mic.current.mediaStream;
    if (!stream) throw new Error("Không tìm thấy luồng âm thanh micro.");
    return player.current.startRecordingAudio(stream);
  }, [prepareAudio]);

  const stopRecordingAudio = useCallback(() => player.current.stopRecordingAudio(), []);
  const canAnswer = connected && !waiting.current;

  const pttStart = useCallback(async () => {
    if (recording.current || !ws.current || ws.current.readyState !== WebSocket.OPEN || waiting.current) return;
    if (!mic.current.ready && !(await prepareAudio())) return;
    recording.current = true;
    silence(); // barge-in: the interviewer stops talking right away
    ttsBusy.current = false;
    setInterim("");
    setError(null);
    ws.current.send(JSON.stringify({ type: "ptt_start" }));
    setPhase("recording");
  }, [prepareAudio, silence]);

  const pttEnd = useCallback(() => {
    if (!recording.current) return;
    recording.current = false;
    // Let the last ~150 ms of audio in the worklet arrive before closing the turn.
    window.setTimeout(() => ws.current?.send(JSON.stringify({ type: "ptt_end" })), 150);
    waiting.current = true;
    setPhase("transcribing");
  }, [settle, react]);

  const sendText = useCallback((text: string) => {
    if (!text.trim() || ws.current?.readyState !== WebSocket.OPEN || waiting.current) return;
    silence();
    ws.current.send(JSON.stringify({ type: "tts_stop" }));
    ws.current.send(JSON.stringify({ type: "text_answer", text }));
    waiting.current = true;
    setPhase("waiting");
  }, [silence]);

  const setAnswerContext = useCallback((text: string) => {
    nextAnswerContext.current = text.slice(0, ANSWER_CONTEXT_LIMIT);
    if (ws.current?.readyState === WebSocket.OPEN) {
      ws.current.send(JSON.stringify({ type: "answer_context", text: nextAnswerContext.current }));
    }
  }, []);

  const send = useCallback((message: object) => {
    if (ws.current?.readyState === WebSocket.OPEN) ws.current.send(JSON.stringify(message));
  }, []);
  const retry = useCallback(() => send({ type: "retry_answer" }), [send]);
  const stopSpeaking = useCallback(() => {
    silence();
    ttsBusy.current = false;
    send({ type: "tts_stop" });
    settle();
  }, [settle, silence, send]);

  const mouthLevel = useCallback(() => player.current.level(), []);

  /** Drop local copies once the saved transcript contains them. */
  const reconcile = useCallback((saved: Message[]) => {
    const savedInterviewer = new Set(saved.filter((m) => m.role === "interviewer").map((m) => m.turn));
    setLive((prev) => {
      const next = new Map([...prev].filter(([turn]) => !savedInterviewer.has(turn)));
      return next.size === prev.size ? prev : next;
    });
    setSent((prev) => (prev && saved.some((m) => m.role === "candidate" && m.turn === prev.turn) ? null : prev));
  }, []);

  return { phase, connected, interim, live, sent, voiced, lastFluency, error, micError, level, canAnswer, mouthLevel,
           pttStart, pttEnd, sendText, setAnswerContext, retry, stopSpeaking, prepareAudio, startRecordingAudio,
           stopRecordingAudio, reconcile, clearError: () => setError(null) };
}
