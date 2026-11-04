import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Bell, ChevronDown, ChevronRight, House } from "lucide-react";
import Sidebar from "../../components/Sidebar";
import { api, TERMINAL, type Message, type SessionView } from "../api";
import { stripGestures } from "../gestures";
import { preloadLinh } from "../linh/avatar";
import { useInterview } from "../useInterview";
import { useToast } from "../toast";
import { InterviewRoom } from "./InterviewRoom";
import type { LinhHandle } from "./LinhStage";
import { ReportView } from "./ReportView";
import { Setup } from "./Setup";
import { StatusBadge } from "./StatusBadge";

const PREPARING = new Set(["INITIALIZED", "PROFILING"]);
const BACKGROUND = new Set(["DIALOGUE_COMPLETED", "ASSESSING", "SYNTHESIZING", "ENDING"]);
const ACTIVE = new Set(["STAGE_ACTIVE", "STAGE_TRANSITIONING"]);
// Mirrors PROFILING_STEPS in core_agent/core/profiler/agent.py; "queued" means waiting for a profiler slot.
const PROFILING_STEPS: [string, string][] = [
  ["profile", "Đọc CV và trích xuất hồ sơ ứng viên"],
  ["research", "Tìm hiểu công ty và tra cứu kiến thức chuyên môn"],
  ["company", "Tổng hợp thông tin công ty"],
  ["spec", "Xác định tiêu chí phỏng vấn"],
  ["plan", "Lập kế hoạch các phần và câu hỏi"],
  ["finalize", "Hoàn thiện và lưu kế hoạch"],
];

const PROFILING_HINTS: Record<string, string> = {
  profile: "Đang phân tích thông tin...",
  research: "Đang tra cứu công ty và kiến thức chuyên môn...",
  company: "Đang tổng hợp thông tin công ty...",
  spec: "Đang xác định những năng lực cần đánh giá...",
  plan: "Đang xây dựng câu hỏi phù hợp với hồ sơ...",
  finalize: "Đang hoàn thiện kế hoạch phỏng vấn...",
};

function ProfilingIcon({ name }: { name: string }) {
  const common = { fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  return (
    <svg viewBox="0 0 24 24" width="23" height="23" aria-hidden="true" {...common}>
      {name === "profile" && <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h8" /></>}
      {name === "research" && <><circle cx="10.8" cy="10.8" r="6.8" /><path d="m16 16 5 5" /></>}
      {name === "company" && <><path d="M3 21h18M5 21V7l7-4v18M12 10h7v11M8 9h1M8 13h1M8 17h1M15 14h1M15 17h1" /></>}
      {name === "spec" && <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /><path d="m13 11 7-7" /></>}
      {name === "plan" && <><path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8A8.5 8.5 0 0 1 8.7 4a8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8z" /><path d="M8 10h8M8 14h5" /></>}
      {name === "finalize" && <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h7" /><path d="M14 2v6h6M8 13h7M8 17h4" /><circle cx="18" cy="18" r="3" /><path d="m17 18 1 1 2-2" /></>}
    </svg>
  );
}

function ProfilingProgress({ step, startedAt }: { step?: string; startedAt?: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const reportedStep = PROFILING_STEPS.findIndex(([key]) => key === step);
  const current = reportedStep < 0 ? 0 : reportedStep;
  const elapsed = startedAt ? Math.max(0, Math.round(now - startedAt)) : null;
  const elapsedLabel = elapsed === null ? "" : elapsed < 60
    ? `Đã chạy ${elapsed}s.`
    : `Đã chạy ${Math.floor(elapsed / 60)} phút ${elapsed % 60}s.`;
  return (
    <section className="profiling-panel" aria-labelledby="profiling-title">
      <div className="profiling-spinner" role="status" aria-label="Đang chuẩn bị phiên phỏng vấn"><span /></div>
      <h2 id="profiling-title">Đang chuẩn bị buổi phỏng vấn...</h2>
      <p className="profiling-intro">Mình đang phân tích hồ sơ của bạn và tạo kế hoạch phỏng vấn phù hợp.</p>
      <p className="profiling-time">{step === "queued" ? "Đang chờ tới lượt xử lý..." : "Thường mất dưới một phút."} {elapsedLabel}</p>
      <ol className="profiling-steps">
        {PROFILING_STEPS.map(([key, label], i) => {
          const state = i < current ? "done" : i === current ? "now" : "todo";
          return (
            <li key={key} className={`profiling-step ${state}`} aria-current={state === "now" ? "step" : undefined}>
              <span className="profiling-step-state" aria-hidden="true">{state === "done" ? "✓" : state === "now" ? <span /> : null}</span>
              <span className="profiling-step-icon"><ProfilingIcon name={key} /></span>
              <span className="profiling-step-copy">
                <span>{label}</span>
                {state === "now" && <small>{step === "queued" ? "Đang xếp hàng chờ xử lý..." : PROFILING_HINTS[key]}</small>}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

export function SessionPage({ sessionId, onBack }: { sessionId: string; onBack: () => void }) {
  const [view, setView] = useState<SessionView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [greet, setGreet] = useState(false); // "Bắt đầu phỏng vấn" pressed: Linh waves once on arrival
  const loading = useRef(false);
  const linh = useRef<LinhHandle>(null);

  const load = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try { setView(await api.session(sessionId)); setError(null); }
    catch (e) { setError((e as Error).message); }
    finally { loading.current = false; }
  }, [sessionId]);

  useEffect(() => { setView(null); void load(); }, [load]);

  const status = view?.session.status;
  const wasAssessing = useRef(false);
  const wsEnabled = !!status && !TERMINAL.has(status) && !PREPARING.has(status);
  const interview = useInterview({ sessionId, enabled: wsEnabled, afterSequence: view?.last_sequence ?? 0, onRefresh: load,
                                  onGesture: (gesture) => linh.current?.play(gesture) });

  useEffect(() => { if (status === "READY") preloadLinh(); }, [status]);

  // Poll while the core works in the background; the socket drives refreshes during the interview.
  useEffect(() => {
    if (!status || TERMINAL.has(status)) return;
    const every = PREPARING.has(status) || BACKGROUND.has(status) ? 2500 : 15000;
    const timer = window.setInterval(load, every);
    return () => window.clearInterval(timer);
  }, [status, load]);

  useEffect(() => { if (view) interview.reconcile(view.messages); }, [view, interview.reconcile]);

  const messages = useMemo(() => {
    const saved = view?.messages ?? [];
    // Gesture tags drive the avatar only; a tag still streaming in ("[smi") stays hidden.
    // A reply Linh is still saying shows only the sentences already heard.
    const shown = (m: Message, text: string): Message | null => {
      const heard = interview.voiced.get(m.turn);
      const visible = heard === undefined ? text : heard;
      return visible ? { ...m, text: visible, live: m.live || heard !== undefined } : null;
    };
    const merged: Message[] = [];
    for (const m of saved) {
      const message = m.role === "interviewer" ? shown(m, stripGestures(m.text)) : m;
      if (message) merged.push(message);
    }
    const savedInterviewer = new Set(saved.filter((m) => m.role === "interviewer").map((m) => m.turn));
    for (const m of interview.live.values()) {
      const message = savedInterviewer.has(m.turn) ? null : shown(m, stripGestures(m.text, !m.live));
      if (message) merged.push(message);
    }
    if (interview.sent && !saved.some((m) => m.role === "candidate" && m.turn === interview.sent!.turn)) merged.push(interview.sent);
    return merged.sort((a, b) => a.turn - b.turn || (a.role === "candidate" ? -1 : 1));
  }, [view, interview.live, interview.sent, interview.voiced]);
  // A reply exists but none of it has been heard yet: keep the "thinking" bubble up.
  const preparing = [...interview.voiced.values()].some((text) => !text);

  async function endInterview() {
    if (!confirmEnd) { setConfirmEnd(true); window.setTimeout(() => setConfirmEnd(false), 4000); return; }
    setConfirmEnd(false);
    try { await api.end(sessionId); await load(); } catch (e) { setError((e as Error).message); }
  }

  // Session problems and the "assessing" step float as toasts instead of shifting the room.
  // Errors and core failures stay until they clear (or are closed), so a failed session keeps its reason.
  const coreFailure = view?.session.failure_message ?? view?.session.metadata?.failure_message
    ?? view?.session.failure_code ?? view?.session.metadata?.failure_code;
  useToast(view ? error : null, { id: "session-error", tone: "error", duration: 0 });
  useToast(coreFailure ? `Core lỗi: ${coreFailure}` : null, { id: "session-core-failure", tone: "error", duration: 0 });
  useToast(status && BACKGROUND.has(status) ? "Phỏng vấn đã xong. Core đang đánh giá và viết báo cáo…" : null,
           { id: "session-assessing", tone: "info", duration: 6000 });
  useToast(status === "REPORT_READY" && wasAssessing.current ? "Báo cáo đã sẵn sàng!" : null,
           { id: "session-report-ready", tone: "success" });
  if (status && BACKGROUND.has(status)) wasAssessing.current = true;

  if (!view) {
    return <main className="page"><button className="link" onClick={onBack}>← Danh sách</button>
      {error ? <div className="notice error"><p>{error}</p><button onClick={load}>Thử lại</button></div> : <p className="muted">Đang tải phiên…</p>}</main>;
  }
  const form = view.form ?? {};

  if (status && PREPARING.has(status)) {
    return (
      <main className="profiling-screen">
        <div className="profiling-backdrop" aria-hidden="true" />
        <div className="profiling-layout">
          <header className="profiling-header">
            <button className="profiling-back" onClick={onBack}>
              <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="m15 18-6-6 6-6M9 12h12" /></svg>
              <span>Danh sách</span>
            </button>
            <div className="profiling-session-title">
              <h1>{form.candidate_name || sessionId}</h1>
              <p>{[form.target_role, form.target_company].filter(Boolean).join(" · ") || "AI interview"}</p>
            </div>
            <span className="profiling-status">
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3ZM19 16l1 2.5 2.5 1-2.5 1L19 23l-1-2.5-2.5-1 2.5-1L19 16Z" /></svg>
              {status === "PROFILING" ? "Đang phân tích hồ sơ" : "Đang khởi tạo phiên"}
            </span>
          </header>
          {error && <p className="profiling-error" role="status">Mất kết nối tạm thời. Đang thử tải lại trạng thái phiên...</p>}
          <ProfilingProgress step={view.session.metadata?.profiling_step} startedAt={view.session.created_at} />
          <p className="profiling-footer">Cóc Đi Làm <span>·</span> Luyện tập phỏng vấn cùng AI</p>
        </div>
      </main>
    );
  }

  if (status === "READY") {
    const candidateName = form.candidate_name || sessionId;
    const startInterview = async () => {
      if (!(await interview.prepareAudio())) throw new Error("Vui lòng cho phép sử dụng micro để bắt đầu phỏng vấn bằng giọng nói.");
      setGreet(true);
      await api.start(sessionId);
      await load();
    };

    return (
      <div className="ready-workspace">
        <div className="ready-workspace-backdrop" aria-hidden="true" />
        <Sidebar activeInterview />
        <main className="ready-workspace-main">
          <header className="ready-workspace-header">
            <nav className="ready-breadcrumb" aria-label="Breadcrumb">
              <button type="button" onClick={onBack}><House size={17} />Danh sách</button>
              <ChevronRight size={16} />
              <span>{candidateName}</span>
            </nav>
            <div className="ready-account-actions">
              <span className="ready-ready-pill"><span />Sẵn sàng</span>
              <button type="button" className="ready-notification" aria-label="Thông báo"><Bell size={19} /></button>
              <div className="ready-account-pill">
                <span className="ready-account-avatar">{String(candidateName).slice(0, 1).toUpperCase()}</span>
                <strong>{candidateName}</strong>
                <ChevronDown size={16} />
              </div>
            </div>
          </header>
          {error && <p className="ready-error" role="alert">{error}</p>}
          <Setup
            view={view}
            connected={interview.connected}
            onChanged={load}
            micLevel={interview.level}
            onMicTest={interview.prepareAudio}
            onStart={startInterview}
          />
        </main>
      </div>
    );
  }

  const liveRoom = !!status && (ACTIVE.has(status) || BACKGROUND.has(status));
  const reportReady = status === "REPORT_READY";
  return (
    <main className={`page session${liveRoom ? " live-session" : ""}${reportReady ? " report-session" : ""}`}>
      <header className={`session-head${liveRoom ? " live-session-head" : ""}`}>
        {liveRoom ? (
          <div className="live-session-brand">
            <button type="button" className="live-brand-logo" onClick={onBack} aria-label="Về danh sách phỏng vấn">
              <img src="/images/coc-logo-transparent.png" alt="Cóc Đi Làm" />
              <span>Cóc Đi Làm</span>
            </button>
            <span className="live-brand-chevron" aria-hidden="true">›</span>
            <div className="live-session-title">
              <h1>{form.candidate_name || sessionId}</h1>
              <p>{[form.target_role, form.target_company].filter(Boolean).join(" · ") || "AI interview"}</p>
            </div>
          </div>
        ) : <>
          <button className="link" onClick={onBack}>← Danh sách</button>
          <div>
            <h1>{form.candidate_name || sessionId}</h1>
            <p className="muted">{[form.target_role, form.target_company].filter(Boolean).join(" · ")}</p>
          </div>
        </>}
        <div className="head-actions">
          <StatusBadge status={status} />
          {wsEnabled && <span className={`dot ${interview.connected ? "on" : ""}`} title={interview.connected ? "Đã kết nối" : "Mất kết nối"} />}
        </div>
      </header>


      {status && PREPARING.has(status) && (
        <ProfilingProgress step={view.session.metadata?.profiling_step} startedAt={view.session.created_at} />
      )}

      {status && (ACTIVE.has(status) || BACKGROUND.has(status)) && (
        <InterviewRoom
          messages={messages}
          phase={interview.phase}
          connected={interview.connected}
          active={ACTIVE.has(status)}
          interim={interview.interim}
          level={interview.level}
          error={interview.error}
          micError={interview.micError}
          onPttStart={interview.pttStart}
          onPttEnd={interview.pttEnd}
          onText={interview.sendText}
          onAnswerContext={interview.setAnswerContext}
          answerTurn={interview.sent?.turn ?? null}
          sessionId={sessionId}
          onStopSpeaking={interview.stopSpeaking}
          startRecordingAudio={interview.startRecordingAudio}
          stopRecordingAudio={interview.stopRecordingAudio}
          onRetry={interview.retry}
          linh={linh}
          mouthLevel={interview.mouthLevel}
          greet={greet}
          onGreeted={() => setGreet(false)}
          candidateName={form.candidate_name}
          preparing={preparing}
          onEndInterview={endInterview}
          confirmEnd={confirmEnd}
        />
      )}

      {reportReady && <ReportView sessionId={sessionId} candidateName={form.candidate_name} onBack={onBack} />}

      {status && TERMINAL.has(status) && messages.length > 0 && (
        // The report comes first; the finished transcript stays available but collapsed.
        <details className="card transcript-review" open={status !== "REPORT_READY"}>
          <summary>Xem lại hội thoại ({messages.filter((m) => m.role === "candidate").length} câu trả lời)</summary>
          <InterviewRoom messages={messages} phase={interview.phase} connected={false} active={false} review
            interim="" level={0} error={null} micError={null} onPttStart={() => {}} onPttEnd={() => {}}
            onText={() => {}} onStopSpeaking={() => {}} startRecordingAudio={async () => new MediaStream()}
            stopRecordingAudio={() => {}} onRetry={() => {}} onEndInterview={async () => {}} confirmEnd={false} />
        </details>
      )}
    </main>
  );
}
