// Face-to-face view: Linh (Live2D interviewer) across the desk, the candidate in a self-view tile.
import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { GESTURES, type Gesture } from "../gestures";
import { GESTURE_INFO, LinhAvatar } from "../linh/avatar";
import { interviewSceneLayout } from "../sceneLayout";

export interface LinhHandle {
  /** Gesture from the interviewer's reply; does not cut the greeting short. */
  play: (gesture: Gesture) => void;
  /** Canvas rendered by Live2D, used to compose an interview recording without screen capture. */
  getCanvas: () => HTMLCanvasElement | null;
}

interface Props {
  speaking: boolean;
  recording: boolean; // the candidate is answering
  waiting: boolean; // answer sent, the reply is being prepared
  mouthLevel: () => number;
  greet: boolean; // wave once as soon as Linh is ready
  onGreeted: () => void;
  candidateName?: string;
  micLevel: number;
  showSelfView?: boolean;
  seated?: boolean;
}

// Reactions while Linh is not speaking: active listening, then taking notes / thinking while waiting.
const AMBIENT: Record<"listening" | "thinking", { pool: Gesture[]; first: number }> = {
  listening: { pool: ["nod", "smile", "nod"], first: 1200 },
  thinking: { pool: ["write", "think"], first: 400 },
};

export const LinhStage = forwardRef<LinhHandle, Props>(function LinhStage(props, ref) {
  const { speaking, greet, recording, waiting, micLevel } = props;
  const stage = useRef<HTMLDivElement>(null);
  const avatar = useRef<LinhAvatar | null>(null);
  const mouth = useRef(props.mouthLevel);
  mouth.current = props.mouthLevel;
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [attempt, setAttempt] = useState(0);
  const [action, setAction] = useState<Gesture>("listen");
  const [panel, setPanel] = useState(false);
  const [tableClip, setTableClip] = useState<string>();

  useLayoutEffect(() => {
    const element = stage.current;
    if (!props.seated || !element) return;
    const update = () => setTableClip(interviewSceneLayout(element.clientWidth, element.clientHeight).clipPath);
    const observer = new ResizeObserver(update);
    observer.observe(element);
    update();
    return () => observer.disconnect();
  }, [props.seated]);

  useImperativeHandle(ref, () => ({
    play: (gesture) => avatar.current?.play(gesture),
    getCanvas: () => avatar.current?.captureCanvas() ?? null,
  }), []);

  useEffect(() => {
    if (!stage.current) return;
    const linh = new LinhAvatar(stage.current, () => mouth.current(), props.seated);
    let active = true;
    linh.onAction = setAction;
    avatar.current = linh;
    setState("loading");
    linh.load().then(() => { if (active) setState("ready"); }, (error) => {
      if (active) { console.error(error); setState("error"); }
    });
    const hide = () => { if (document.hidden) void linh.perform("listen"); };
    document.addEventListener("visibilitychange", hide);
    return () => {
      active = false;
      document.removeEventListener("visibilitychange", hide);
      linh.destroy();
      if (avatar.current === linh) avatar.current = null;
    };
  }, [attempt, props.seated]);

  useEffect(() => {
    if (state !== "ready" || !greet) return;
    void avatar.current?.perform("wave");
    props.onGreeted();
  }, [state, greet]); // eslint-disable-line react-hooks/exhaustive-deps

  const mood = recording ? "listening" : waiting && !speaking ? "thinking" : null;
  useEffect(() => {
    if (state !== "ready" || !mood) return;
    const { pool, first } = AMBIENT[mood];
    let timer = window.setTimeout(function tick() {
      avatar.current?.ambient(pool[Math.floor(Math.random() * pool.length)]);
      timer = window.setTimeout(tick, 3500 + Math.random() * 3000);
    }, first + Math.random() * 800);
    return () => window.clearTimeout(timer);
  }, [state, mood]);

  return (
    <section className={`linh-room${speaking ? " speaking" : ""}`} aria-label="Phòng phỏng vấn">
      <div className="window-light" /><div className="plant"><span /><span /><span /></div>
      <div className="linh-stage" ref={stage} />
      <div className="desk" />
      {props.seated && <div className="interview-table-foreground" style={{ clipPath: tableClip }} aria-hidden="true" />}

      <div className="linh-caption">
        <span className="live-dot" />
        <div><strong>Linh</strong><span>{speaking ? "Đang nói…" : `Người phỏng vấn · ${GESTURE_INFO[action].label.toLowerCase()}`}</span></div>
        <span className="sound-bars" aria-hidden="true"><i /><i /><i /><i /></span>
      </div>

      {props.showSelfView !== false && <SelfView name={props.candidateName} recording={recording} level={micLevel} />}

      <div className={`gesture-dock${panel ? " open" : ""}`}>
        <button type="button" className="gesture-toggle" aria-expanded={panel} disabled={state !== "ready"}
                onClick={() => setPanel(!panel)} title="Cử chỉ của Linh">
          <span aria-hidden="true">✋</span> Cử chỉ <span aria-hidden="true">{panel ? "▴" : "▾"}</span>
        </button>
        {panel && (
          <div className="gesture-panel" role="group" aria-label="Cử chỉ của Linh">
            {GESTURES.map((gesture) => (
              <button key={gesture} type="button" aria-pressed={action === gesture}
                      onClick={() => void avatar.current?.perform(gesture)}>
                <span aria-hidden="true">{GESTURE_INFO[gesture].icon}</span>
                <strong>{GESTURE_INFO[gesture].label}</strong><small>{GESTURE_INFO[gesture].hint}</small>
              </button>
            ))}
          </div>
        )}
      </div>

      {state !== "ready" && (
        <div className="linh-loading" role="status">
          {state === "loading" ? <><span className="spinner" /><strong>Linh đang vào phòng…</strong></> : <>
            <strong>Chưa tải được nhân vật</strong>
            <button type="button" onClick={() => setAttempt(attempt + 1)}>Thử tải lại</button>
          </>}
        </div>
      )}
    </section>
  );
});

/** The candidate's side of the table: live camera (local preview only, never sent) or an initial with the mic level. */
function SelfView({ name, recording, level }: { name?: string; recording: boolean; level: number }) {
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Attach the stream as soon as the <video> exists, and start it (autoplay can be skipped silently).
  const attach = useCallback((video: HTMLVideoElement | null) => {
    if (!video || !stream) return;
    if (video.srcObject !== stream) video.srcObject = stream;
    void video.play().catch(() => {});
  }, [stream]);

  useEffect(() => () => stream?.getTracks().forEach((track) => track.stop()), [stream]);

  async function toggle() {
    if (stream) { setStream(null); return; }
    if (!navigator.mediaDevices?.getUserMedia) { setError("Trình duyệt không hỗ trợ camera"); return; }
    setBusy(true);
    try {
      setStream(await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" }, audio: false }));
      setError(null);
    } catch (e) {
      setError((e as Error).name === "NotAllowedError" ? "Chưa cho phép dùng camera" : "Không mở được camera");
    } finally {
      setBusy(false);
    }
  }

  const initial = (name?.trim().split(/\s+/).at(-1)?.[0] ?? "B").toUpperCase();
  const label = stream ? "Tắt camera" : "Bật camera";
  return (
    <div className={`self-view${recording ? " recording" : ""}${stream ? " live" : ""}`} style={{ ["--level" as string]: String(level) }}>
      {stream ? <video ref={attach} autoPlay muted playsInline /> : <span className="initial">{initial}</span>}
      <span className="self-name">{error ?? (recording ? "Bạn · đang nói" : "Bạn")}</span>
      <button type="button" className={`camera${stream ? " on" : ""}`} onClick={toggle} disabled={busy}
              title={label} aria-label={label} aria-pressed={!!stream}>
        <svg viewBox="0 0 24 24" width="15" height="15" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M15 10l5-3v10l-5-3z" /><rect x="3" y="6" width="12" height="12" rx="2" />
          {!stream && <path d="M2 2l20 20" />}
        </svg>
      </button>
    </div>
  );
}
