import { useEffect, useRef, useState, type RefObject } from "react";
import { Camera, CameraOff, Circle, Download, FileText, FolderUp, MessageCircle, Mic, MicOff, Paperclip, PhoneOff, Send, Square, Upload, X } from "lucide-react";
import { api, type Message, type UploadedDocument } from "../api";
import { drawInterviewRecordingFrame } from "../recordingCanvas";
import { useToast } from "../toast";
import { ANSWER_CONTEXT_LIMIT, type Phase } from "../useInterview";
import { FluencyChips } from "./FluencyChips";
import { LinhStage, type LinhHandle } from "./LinhStage";

interface Props {
  messages: Message[];
  phase: Phase;
  connected: boolean;
  active: boolean; // session accepts answers (STAGE_ACTIVE)
  review?: boolean; // finished session: transcript only, no answer controls
  interim: string;
  level: number;
  error: string | null;
  micError: string | null;
  onPttStart: () => void | Promise<void>;
  onPttEnd: () => void;
  onText: (text: string) => void;
  onAnswerContext?: (text: string) => void;
  answerTurn?: number | null;
  sessionId?: string;
  onStopSpeaking: () => void;
  startRecordingAudio: () => Promise<MediaStream>;
  stopRecordingAudio: () => void;
  onRetry: () => void;
  linh?: RefObject<LinhHandle>;
  mouthLevel?: () => number;
  greet?: boolean;
  onGreeted?: () => void;
  candidateName?: string;
  preparing?: boolean;
  onEndInterview: () => Promise<void>;
  confirmEnd: boolean;
}

interface PendingDocument {
  id: string;
  filename: string;
  extractedText: string;
  extractedCharacters: number;
  truncated: boolean;
}

// Same limit as the hook, which sends this text with the next answer.
const MAX_ATTACHMENTS = 3;
const PHASE_LABEL: Record<Phase, string> = {
  connecting: "Đang kết nối phòng phỏng vấn…",
  idle: "Bật micro để trả lời câu hỏi",
  recording: "Micro đang bật · bấm lần nữa để gửi câu trả lời",
  transcribing: "Đang nhận dạng câu trả lời…",
  waiting: "Linh đang chuẩn bị câu hỏi tiếp theo…",
  speaking: "Linh đang nói · bật micro để trả lời hoặc ngắt lời",
};

function contextFor(documents: PendingDocument[]): string {
  let remaining = ANSWER_CONTEXT_LIMIT;
  const sections: string[] = [];
  for (const document of documents) {
    if (remaining <= 0) break;
    const heading = `Tài liệu ứng viên đính kèm: ${document.filename}\n`;
    const body = document.extractedText.slice(0, Math.max(0, remaining - heading.length));
    if (body) sections.push(`${heading}${body}`);
    remaining -= heading.length + body.length + 2;
  }
  return sections.join("\n\n").slice(0, ANSWER_CONTEXT_LIMIT);
}

export function InterviewRoom(props: Props) {
  const { messages, phase, connected, active, interim, level, error, micError } = props;
  const [text, setText] = useState("");
  const [camera, setCamera] = useState<MediaStream | null>(null);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<PendingDocument[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadMenuOpen, setUploadMenuOpen] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [recordingBusy, setRecordingBusy] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  // Room problems float as toasts instead of pushing the stage and controls around. The room error
  // (and its "Gửi lại" action) stays until the error clears, so an unreceived answer can always be resent.
  const resend = Boolean(error?.includes("chưa được core nhận"));
  useToast(error, { id: "room-error", tone: "error", duration: 0,
                    action: resend ? { label: "Gửi lại", onClick: props.onRetry } : undefined });
  useToast(micError, { id: "room-mic", tone: "warn" });
  useToast(cameraError, { id: "room-camera", tone: "warn" });
  useToast(recordError, { id: "room-record", tone: "error" });
  useToast(uploadError, { id: "room-upload", tone: "error" });
  const [recordUrl, setRecordUrl] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const uploadMenu = useRef<HTMLDivElement>(null);
  const candidateVideo = useRef<HTMLVideoElement | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const sceneStream = useRef<MediaStream | null>(null);
  const drawTimer = useRef<number | null>(null);
  const recordChunks = useRef<BlobPart[]>([]);
  const recordUrlRef = useRef<string | null>(null);
  const onAnswerContextRef = useRef(props.onAnswerContext);
  const stopRecordingAudioRef = useRef(props.stopRecordingAudio);
  const enabled = connected && active && phase !== "waiting" && phase !== "transcribing" && phase !== "connecting";
  const spaceHeld = useRef(false);
  const spaceStartPending = useRef(false);
  const keyboardState = useRef({ enabled, review: props.review, phase, onPttStart: props.onPttStart, onPttEnd: props.onPttEnd });
  const frameState = useRef({ messages, interim, candidateName: props.candidateName, speaking: phase === "speaking" });
  const lastAnswerTurn = useRef<number | null>(null);

  recordUrlRef.current = recordUrl;
  onAnswerContextRef.current = props.onAnswerContext;
  stopRecordingAudioRef.current = props.stopRecordingAudio;
  keyboardState.current = { enabled, review: props.review, phase, onPttStart: props.onPttStart, onPttEnd: props.onPttEnd };
  frameState.current = { messages, interim, candidateName: props.candidateName, speaking: phase === "speaking" };
  useEffect(() => () => camera?.getTracks().forEach((track) => track.stop()), [camera]);
  useEffect(() => { folderInput.current?.setAttribute("webkitdirectory", ""); }, []);
  useEffect(() => { props.onAnswerContext?.(contextFor(attachments)); }, [attachments, props.onAnswerContext]);
  useEffect(() => {
    if (props.answerTurn == null || props.answerTurn === lastAnswerTurn.current) return;
    lastAnswerTurn.current = props.answerTurn;
    setAttachments([]);
    setUploadError(null);
  }, [props.answerTurn]);
  useEffect(() => {
    return () => {
      onAnswerContextRef.current?.("");
      if (recorder.current && recorder.current.state !== "inactive") recorder.current.stop();
      if (drawTimer.current !== null) window.clearInterval(drawTimer.current);
      sceneStream.current?.getTracks().forEach((track) => track.stop());
      stopRecordingAudioRef.current();
      if (recordUrlRef.current) URL.revokeObjectURL(recordUrlRef.current);
    };
  }, []);

  useEffect(() => {
    const endSpaceAnswer = () => {
      if (!spaceHeld.current) return;
      spaceHeld.current = false;
      if (!spaceStartPending.current) keyboardState.current.onPttEnd();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target;
      if (target instanceof Element && target.closest('input, textarea, select, button, a, [contenteditable="true"], [role="button"], [role="menuitem"]')) return;
      const state = keyboardState.current;
      if (!state.enabled || state.review || state.phase === "recording") return;
      event.preventDefault();
      spaceHeld.current = true;
      spaceStartPending.current = true;
      Promise.resolve().then(state.onPttStart).catch(() => undefined).finally(() => {
        spaceStartPending.current = false;
        if (!spaceHeld.current) keyboardState.current.onPttEnd();
      });
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (event.code !== "Space" || !spaceHeld.current) return;
      event.preventDefault();
      endSpaceAnswer();
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", endSpaceAnswer);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", endSpaceAnswer);
      endSpaceAnswer();
    };
  }, []);

  async function addFiles(files: FileList | null) {
    if (!files?.length || uploading || !props.sessionId) return;
    const picked = Array.from(files);
    const supported = picked.filter((file) => /\.(pdf|docx|txt)$/i.test(file.name));
    const available = Math.max(0, MAX_ATTACHMENTS - attachments.length);
    const selected = supported.slice(0, available);
    if (!selected.length) {
      setUploadError(available === 0
        ? `Mỗi lượt trả lời đính kèm tối đa ${MAX_ATTACHMENTS} tài liệu.`
        : "Chỉ hỗ trợ tài liệu PDF, DOCX hoặc TXT.");
      return;
    }
    const skipped = picked.length - selected.length;
    setUploading(true);
    setUploadError(null);
    const next = [...attachments];
    try {
      for (const file of selected) {
        const result: UploadedDocument = await api.uploadDocument(props.sessionId, file);
        next.push({
          id: String(Date.now()) + "-" + String(Math.random()),
          filename: result.filename || file.name,
          extractedText: result.extracted_text,
          extractedCharacters: result.extracted_characters,
          truncated: result.truncated,
        });
        setAttachments([...next]);
      }
      if (skipped) setUploadError(`Đã bỏ qua ${skipped} tệp không hỗ trợ hoặc vượt quá giới hạn ${MAX_ATTACHMENTS} tài liệu.`);
    } catch (cause) {
      setUploadError((cause as Error).message || "Không tải được tài liệu lên.");
    } finally {
      setUploading(false);
      if (fileInput.current) fileInput.current.value = "";
      if (folderInput.current) folderInput.current.value = "";
      setUploadMenuOpen(false);
    }
  }

  const transcript = (
    <section className={`transcript card${props.review ? " transcript-review-only" : " active-transcript"}`} aria-label="Nội dung phỏng vấn">
      {!props.review && (
        <header className="conversation-head">
          <span className="conversation-icon"><MessageCircle size={21} aria-hidden="true" /></span>
          <div><h2>Câu hỏi hiện tại</h2><p>Câu hỏi và câu trả lời của bạn</p></div>
        </header>
      )}
      <div className="transcript-scroll">
        {!messages.length && <div className="conversation-empty"><MessageCircle size={27} aria-hidden="true" /><strong>Câu hỏi đầu tiên sẽ xuất hiện ở đây.</strong><span>Linh sẽ bắt đầu buổi phỏng vấn trong giây lát.</span></div>}
        {messages.map((m) => (
          <article key={`${m.turn}-${m.role}`} className={`bubble ${m.role}${m.live ? " live" : ""}`}>
            <header>{m.role === "candidate" ? "Bạn" : "Linh"} <small>· lượt {m.turn}</small></header>
            <p>{m.text}{m.live && m.role === "interviewer" && <span className="cursor" />}</p>
            {m.role === "candidate" && <FluencyChips fluency={m.fluency} />}
          </article>
        ))}
        {(phase === "recording" || phase === "transcribing") && (
          <article className="bubble candidate live"><header>Bạn <small>· đang nói</small></header><p>{interim || "Đang nghe câu trả lời…"}</p></article>
        )}
        {props.preparing && (
          <article className="bubble interviewer thinking" aria-live="polite">
            <header>Linh</header>
            <p><span className="dots" aria-hidden="true"><i /><i /><i /></span> {phase === "speaking" ? "Đang nói…" : "Đang suy nghĩ…"}</p>
          </article>
        )}
        <div ref={bottom} />
      </div>
      {!props.review && (
        <div className="transcript-composer">
          {attachments.length > 0 && <div className="attachment-list" aria-label="Tài liệu đính kèm">
            {attachments.map((document) => (
              <span className="attachment-chip" key={document.id} title={`${document.extractedCharacters.toLocaleString()} ký tự${document.truncated ? " · nội dung dài đã được rút gọn" : ""}`}>
                <FileText size={15} aria-hidden="true" /><span>{document.filename}</span>
                {document.truncated && <small>đã rút gọn</small>}
                <button type="button" aria-label={`Bỏ tài liệu ${document.filename}`} onClick={() => setAttachments((previous) => previous.filter((item) => item.id !== document.id))}><X size={14} /></button>
              </span>
            ))}
          </div>}
          <form className="transcript-form" onSubmit={(event) => {
            event.preventDefault();
            if ((text.trim() || attachments.length) && enabled && !uploading) {
              const outgoing = text.trim() || `Mình gửi tài liệu ${attachments.map((item) => item.filename).join(", ")} để Linh tham khảo cho câu hỏi tiếp theo.`;
              props.onText(outgoing);
              setText("");
            }
          }}>
            <input ref={fileInput} className="visually-hidden" type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" multiple
                   onChange={(event) => void addFiles(event.currentTarget.files)} aria-label="Chọn tài liệu PDF, DOCX hoặc TXT" />
            <input ref={folderInput} className="visually-hidden" type="file" accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain" multiple
                   onChange={(event) => void addFiles(event.currentTarget.files)} aria-label="Chọn thư mục tài liệu" />
            <button className="attach-button" type="button" aria-label="Gửi file hoặc tài liệu" title="Gửi file hoặc tài liệu (PDF, DOCX, TXT)"
                    disabled={!active || uploading || attachments.length >= MAX_ATTACHMENTS} onClick={() => fileInput.current?.click()}>
              {uploading ? <span className="small-spinner" aria-label="Đang tải tài liệu" /> : <Paperclip size={20} aria-hidden="true" />}
            </button>
            <input value={text} onChange={(event) => setText(event.target.value)} placeholder="Nhập tin nhắn hoặc đính kèm tài liệu…"
                   aria-label="Nhập câu trả lời bằng văn bản" disabled={!enabled} />
            <button type="submit" aria-label="Gửi câu trả lời" disabled={!enabled || uploading || (!text.trim() && !attachments.length)}><Send size={19} aria-hidden="true" /></button>
          </form>
        </div>
      )}
    </section>
  );

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, messages.at(-1)?.text, interim, props.preparing]);

  if (props.review) return <div className="room">{transcript}</div>;

  async function toggleCamera() {
    if (camera) { setCamera(null); setCameraError(null); return; }
    if (!navigator.mediaDevices?.getUserMedia) { setCameraError("Trình duyệt không hỗ trợ camera."); return; }
    setCameraBusy(true);
    try {
      setCamera(await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: "user" }, audio: false }));
      setCameraError(null);
    } catch (cause) {
      setCameraError((cause as Error).name === "NotAllowedError" ? "Bạn chưa cấp quyền dùng camera." : "Không mở được camera.");
    } finally { setCameraBusy(false); }
  }

  async function toggleRecording() {
    if (recorder.current && recorder.current.state !== "inactive") {
      setRecordingBusy(true);
      recorder.current.stop();
      return;
    }
    if (typeof MediaRecorder === "undefined" || !HTMLCanvasElement.prototype.captureStream) {
      setRecordError("Trình duyệt không hỗ trợ ghi video phỏng vấn tại máy.");
      return;
    }
    setRecordError(null);
    setRecordingBusy(true);
    let videoStream: MediaStream | null = null;
    try {
      const avatar = props.linh?.current?.getCanvas() ?? null;
      if (!avatar?.width || !avatar.height) throw new Error("Linh đang tải, hãy thử ghi lại sau giây lát.");
      const audioStream = await props.startRecordingAudio();
      const canvas = document.createElement("canvas");
      canvas.width = 1600;
      canvas.height = 900;
      const context = canvas.getContext("2d");
      if (!context) throw new Error("Không tạo được khung video phỏng vấn.");
      const background = new Image();
      background.src = "/images/interview-background.png";
      await background.decode().catch(() => undefined);
      videoStream = canvas.captureStream(30);
      sceneStream.current = new MediaStream([
        ...videoStream.getVideoTracks(),
        ...audioStream.getAudioTracks(),
      ]);
      if (recordUrl) {
        URL.revokeObjectURL(recordUrl);
        recordUrlRef.current = null;
        setRecordUrl(null);
      }
      const drawFrame = () => drawInterviewRecordingFrame(
        context,
        background,
        props.linh?.current?.getCanvas() ?? avatar,
        candidateVideo.current,
        frameState.current,
      );
      drawFrame();
      drawTimer.current = window.setInterval(drawFrame, 1000 / 30);

      const mimeType = ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"]
        .find((type) => MediaRecorder.isTypeSupported(type));
      const activeRecorder = new MediaRecorder(sceneStream.current, mimeType ? { mimeType } : undefined);
      recordChunks.current = [];
      recorder.current = activeRecorder;
      activeRecorder.ondataavailable = (event) => {
        if (event.data.size) recordChunks.current.push(event.data);
      };
      activeRecorder.onerror = () => setRecordError("Ghi video gặp lỗi. Hãy dừng và thử lại.");
      activeRecorder.onstop = () => {
        if (drawTimer.current !== null) window.clearInterval(drawTimer.current);
        drawTimer.current = null;
        sceneStream.current?.getTracks().forEach((track) => track.stop());
        sceneStream.current = null;
        stopRecordingAudioRef.current();
        const blob = new Blob(recordChunks.current, { type: activeRecorder.mimeType || "video/webm" });
        if (blob.size) {
          const url = URL.createObjectURL(blob);
          if (recordUrlRef.current) URL.revokeObjectURL(recordUrlRef.current);
          recordUrlRef.current = url;
          setRecordUrl(url);
          const session = (props.sessionId || "interview").replace(/[^a-zA-Z0-9_-]/g, "-");
          const stamp = new Date().toISOString().replace(/[:.]/g, "-");
          const link = document.createElement("a");
          link.href = url;
          link.download = "coc-di-lam-" + session + "-" + stamp + ".webm";
          document.body.appendChild(link);
          link.click();
          link.remove();
        }
        recorder.current = null;
        setRecording(false);
        setRecordingBusy(false);
      };
      activeRecorder.start(1000);
      setRecording(true);
    } catch (cause) {
      if (drawTimer.current !== null) window.clearInterval(drawTimer.current);
      drawTimer.current = null;
      videoStream?.getTracks().forEach((track) => track.stop());
      sceneStream.current?.getTracks().forEach((track) => track.stop());
      sceneStream.current = null;
      stopRecordingAudioRef.current();
      setRecordError((cause as Error).message || "Không thể bắt đầu ghi video phỏng vấn.");
      setRecording(false);
    } finally {
      setRecordingBusy(false);
    }
  }

  return (
    <div className="room active-room">
      <div className="active-interview-grid">
        <div className="active-linh-wrap">
          <LinhStage ref={props.linh} speaking={phase === "speaking"} mouthLevel={props.mouthLevel ?? (() => 0)}
                     greet={!!props.greet} onGreeted={() => props.onGreeted?.()} candidateName={props.candidateName}
                     recording={phase === "recording"} waiting={phase === "waiting" || phase === "transcribing"}
                     micLevel={level} showSelfView={false} seated />
          {camera && (
            <div className="active-self-view">
              <video autoPlay muted playsInline ref={(video) => { candidateVideo.current = video; if (video && video.srcObject !== camera) { video.srcObject = camera; void video.play().catch(() => {}); } }} />
              <span>{props.candidateName || "Bạn"}</span>
            </div>
          )}
          <div className="active-linh-caption"><span className="live-dot" /><strong>Linh</strong><span>{phase === "speaking" ? "Đang nói…" : phase === "waiting" ? "Đang chuẩn bị câu hỏi…" : "Người phỏng vấn AI"}</span></div>
        </div>
        {transcript}
      </div>

      <div className="active-control-dock">
        <div className="active-speaker-status" aria-live="polite">
          <span className={`active-speaker-avatar${phase === "speaking" ? " speaking" : ""}`} aria-hidden="true">L</span>
          <span><strong>Linh</strong><small>{PHASE_LABEL[phase]}</small><small className="space-to-talk-hint"><kbd>Space</kbd> giữ để nói · thả để gửi</small></span>
        </div>
        <div className="active-controls" aria-label="Điều khiển phỏng vấn">
          <button type="button" className={`active-control mic-control${phase === "recording" ? " selected" : ""}`}
                  onClick={() => phase === "recording" ? props.onPttEnd() : enabled && void props.onPttStart()}
                  disabled={!enabled && phase !== "recording"} aria-pressed={phase === "recording"}
                  aria-label={phase === "recording" ? "Tắt micro và gửi câu trả lời" : "Bật micro để trả lời"}>
            {phase === "recording" ? <MicOff size={22} aria-hidden="true" /> : <Mic size={22} aria-hidden="true" />}
            <span>{phase === "recording" ? "Tắt mic · gửi" : "Bật mic"}</span>
          </button>
          <button type="button" className={`active-control${camera ? " selected" : ""}`} onClick={() => void toggleCamera()}
                  disabled={cameraBusy || (!active && !camera)} aria-pressed={!!camera} aria-label={camera ? "Tắt camera" : "Bật camera"}>
            {camera ? <CameraOff size={22} aria-hidden="true" /> : <Camera size={22} aria-hidden="true" />}
            <span>{camera ? "Tắt cam" : "Camera"}</span>
          </button>
          <div className="active-upload-control" ref={uploadMenu}>
            <button type="button" className={`active-control${uploadMenuOpen ? " selected" : ""}`}
                    onClick={() => setUploadMenuOpen((open) => !open)}
                    disabled={!active || uploading || attachments.length >= MAX_ATTACHMENTS}
                    aria-expanded={uploadMenuOpen} aria-haspopup="menu" aria-label="Tải tệp hoặc thư mục lên">
              {uploading ? <span className="small-spinner" aria-label="Đang tải tài liệu" /> : <Upload size={22} aria-hidden="true" />}
              <span>{uploading ? "Đang tải" : "Tải tài liệu"}</span>
            </button>
            {uploadMenuOpen && <div className="active-upload-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => fileInput.current?.click()}>
                <FileText size={17} aria-hidden="true" /> Chọn tệp
              </button>
              <button type="button" role="menuitem" onClick={() => folderInput.current?.click()}>
                <FolderUp size={17} aria-hidden="true" /> Chọn thư mục
              </button>
              <small>PDF, DOCX, TXT · tối đa {MAX_ATTACHMENTS} tài liệu mỗi câu trả lời</small>
            </div>}
          </div>
          <button type="button" className={`active-control record-control${recording ? " recording" : ""}`} onClick={() => void toggleRecording()}
                  disabled={recordingBusy || (!active && !recording)} aria-pressed={recording}
                  aria-label={recording ? "Dừng và lưu video phỏng vấn" : "Ghi âm cuộc phỏng vấn"}>
            {recording ? <Square size={21} aria-hidden="true" /> : <Circle size={22} aria-hidden="true" />}
            <span>{recordingBusy ? "Đang lưu…" : recording ? "Dừng & lưu" : "Ghi âm"}</span>
          </button>
          {phase === "speaking" && <button type="button" className="active-control" onClick={props.onStopSpeaking}><MicOff size={21} aria-hidden="true" /><span>Dừng đọc</span></button>}
          {recordUrl && <a className="active-control record-download" href={recordUrl} download={`coc-di-lam-recording-${new Date().toISOString().replace(/[:.]/g, "-")}.webm`} aria-label="Tải lại video cuộc trò chuyện">
            <Download size={22} aria-hidden="true" /><span>Tải lại video</span>
          </a>}
        </div>
        <button type="button" className={`active-control end-interview-control${props.confirmEnd ? " confirming" : ""}`}
                onClick={() => void props.onEndInterview()} disabled={!active}
                aria-label={props.confirmEnd ? "Nhấn lại để kết thúc phỏng vấn" : "Kết thúc và tạo báo cáo"}
                title={props.confirmEnd ? "Nhấn lại để kết thúc" : "Kết thúc & tạo báo cáo"}>
          <PhoneOff size={22} aria-hidden="true" />
          <span>{props.confirmEnd ? "Nhấn lại" : "Kết thúc"}</span>
        </button>
      </div>
      {recording && <p className="recording-hint"><span />Đang ghi video và âm thanh cuộc phỏng vấn. Khi dừng, video WebM sẽ được tải về máy.</p>}
    </div>
  );
}
