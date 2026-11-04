
import React, {
  useEffect,
  useRef,
  useState
} from "react";

import { useNavigate } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";

import {
  ArrowLeft,
  FileText,
  Send,
  PhoneOff,
  Check,
  MessageCircle,
  Video,
  VideoOff,
  Mic,
  MicOff,
  MonitorUp,
  Hand,
  Captions,
  Circle,
  Square,
  Download,
  MoreHorizontal,
  X
} from "lucide-react";

import { useLanguage } from "../context/LanguageContext";
import { questionBank } from "../data/mockInterviews";
import { LinhStage } from "@interview/components/LinhStage";
import "virtual:interview.css";
import "../interview-controls.css";
import "../interview-avatar.css";

const vietnameseQuestions = [
  "Hãy giới thiệu bản thân và kinh nghiệm liên quan đến vị trí ứng tuyển.",
  "Tại sao bạn muốn ứng tuyển vào vị trí này?",
  "Hãy kể về một thử thách bạn từng gặp và cách bạn giải quyết.",
  "Điểm mạnh và điểm yếu của bạn là gì?",
  "Bạn thường làm việc nhóm và xử lý bất đồng như thế nào?",
  "Hãy kể về một dự án mà bạn tự hào nhất.",
  "Bạn quản lý thời gian và công việc khi có áp lực ra sao?",
  "Bạn mong muốn phát triển sự nghiệp như thế nào trong ba năm tới?"
];

export default function Interview() {
  const navigate = useNavigate();

  const { language, t } = useLanguage();
  const vi = language === "vi";

  const [activeMeeting, setActiveMeeting] = useState(() => ({
    role: "General Interview",
    index: 0,
    transcript: []
  }));
  const [answer, setAnswer] = useState("");
  const [transcriptVisible, setTranscriptVisible] = useState(true);
  const [confirmEnd, setConfirmEnd] = useState(false);
  const [greetLinh, setGreetLinh] = useState(true);

  const [mic, setMic] = useState(false);
  const [camera, setCamera] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [recording, setRecording] = useState(false);
  const [recordedUrl, setRecordedUrl] = useState("");
  const [handRaised, setHandRaised] = useState(false);
  const [captions, setCaptions] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [notice, setNotice] = useState("");

  const videoRef = useRef(null);
  const screenRef = useRef(null);
  const micStreamRef = useRef(null);
  const cameraStreamRef = useRef(null);
  const screenStreamRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const recordingUrlRef = useRef("");
  const mountedRef = useRef(true);

  const questions = vi ? vietnameseQuestions : questionBank;

  useEffect(() => {
    mountedRef.current = true;

    return () => {
      mountedRef.current = false;

      [
        micStreamRef.current,
        cameraStreamRef.current,
        screenStreamRef.current
      ].forEach(stream => {
        stream?.getTracks().forEach(track => track.stop());
      });

      if (recorderRef.current?.state === "recording") {
        recorderRef.current.stop();
      }

      if (recordingUrlRef.current) {
        URL.revokeObjectURL(recordingUrlRef.current);
      }
    };
  }, []);

  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.srcObject =
        camera ? cameraStreamRef.current : null;
    }
  }, [camera]);

  useEffect(() => {
    if (screenRef.current) {
      screenRef.current.srcObject =
        sharing ? screenStreamRef.current : null;
    }
  }, [sharing]);

  function notify(message) {
    setNotice(message);
  }

  async function toggleMic() {
    if (mic) {
      if (recording) {
        notify(
          vi
            ? "Hãy dừng ghi âm trước khi tắt microphone."
            : "Stop recording before turning off the microphone."
        );
        return;
      }

      micStreamRef.current?.getTracks().forEach(track => track.stop());
      micStreamRef.current = null;
      setMic(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true
      });

      if (!mountedRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }

      micStreamRef.current = stream;
      setMic(true);
    } catch {
      notify(
        vi
          ? "Không thể bật microphone. Hãy kiểm tra quyền truy cập."
          : "Unable to access microphone. Check browser permissions."
      );
    }
  }

  async function toggleCamera() {
    if (camera) {
      cameraStreamRef.current?.getTracks().forEach(track => track.stop());
      cameraStreamRef.current = null;
      setCamera(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          frameRate: { ideal: 30, max: 30 },
          facingMode: "user"
        }
      });

      if (!mountedRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }

      cameraStreamRef.current = stream;
      setCamera(true);
    } catch {
      notify(
        vi
          ? "Không thể bật camera. Hãy kiểm tra quyền truy cập."
          : "Unable to access camera. Check browser permissions."
      );
    }
  }

  async function toggleShare() {
    if (sharing) {
      screenStreamRef.current?.getTracks().forEach(track => track.stop());
      screenStreamRef.current = null;
      setSharing(false);
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: true
      });

      if (!mountedRef.current) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }

      screenStreamRef.current = stream;
      setSharing(true);

      stream.getVideoTracks()[0].onended = () => {
        screenStreamRef.current = null;
        if (mountedRef.current) setSharing(false);
      };
    } catch (error) {
      if (error.name !== "NotAllowedError") {
        notify(
          vi
            ? "Không thể chia sẻ màn hình."
            : "Unable to share your screen."
        );
      }
    }
  }

  async function startRecording() {
    try {
      let stream = micStreamRef.current;

      if (!stream || !stream.active) {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: true
        });

        if (!mountedRef.current) {
          stream.getTracks().forEach(track => track.stop());
          return;
        }

        micStreamRef.current = stream;
        setMic(true);
      }

      if (!window.MediaRecorder) {
        notify(
          vi
            ? "Trình duyệt không hỗ trợ ghi âm."
            : "Recording is not supported by this browser."
        );
        return;
      }

      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];

      recorder.ondataavailable = event => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm"
        });

        const url = URL.createObjectURL(blob);

        if (recordingUrlRef.current) {
          URL.revokeObjectURL(recordingUrlRef.current);
        }

        recordingUrlRef.current = url;

        if (mountedRef.current) {
          setRecordedUrl(url);
          setRecording(false);
        } else {
          URL.revokeObjectURL(url);
          recordingUrlRef.current = "";
        }
      };

      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
      setRecordedUrl("");
    } catch {
      notify(
        vi
          ? "Không thể bắt đầu ghi âm."
          : "Unable to start recording."
      );
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
    }
  }

  function downloadRecording() {
    if (!recordedUrl) return;

    const link = document.createElement("a");
    link.href = recordedUrl;
    link.download = `coc-di-lam-recording-${Date.now()}.webm`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  const index = activeMeeting.index || 0;
  const finished = index >= questions.length;

  const question = questions[
    Math.min(index, questions.length - 1)
  ];

  const savedAnswers = activeMeeting.transcript.filter(
    entry => entry.speaker === "You"
  ).length;

  function submit(e) {
    e.preventDefault();

    if (!answer.trim() || finished) return;

    const entries = [
      ...activeMeeting.transcript,
      { speaker: "AI Interviewer", text: question },
      { speaker: "You", text: answer.trim() }
    ];

    setActiveMeeting({
      ...activeMeeting,
      index: index + 1,
      transcript: entries
    });

    setAnswer("");
  }

  function end() {
    if (recording) stopRecording();

    [
      micStreamRef.current,
      cameraStreamRef.current,
      screenStreamRef.current
    ].forEach(stream => {
      stream?.getTracks().forEach(track => track.stop());
    });

    navigate("/home", { replace: true });
  }

  function tool(Icon, label, onClick, active = false, danger = false) {
    return (
      <button
        type="button"
        title={label}
        aria-label={label}
        aria-pressed={active}
        className={
          "control-btn " +
          (active ? "coc-tool-active " : "") +
          (danger ? "control-danger " : "")
        }
        onClick={onClick}
      >
        <Icon size={21} />
      </button>
    );
  }

  return (
    <main className="interview-shell">
      <header className="room-top">
        <button
          className="room-pill"
          onClick={() => navigate("/home")}
        >
          <ArrowLeft size={17} /> {t("home")}
        </button>

        <div className="room-pill">
          <span className="live-dot" />
          {vi ? "Phòng phỏng vấn ảo" : "Virtual Interview Room"}
        </div>

        <span className="room-pill">
          {activeMeeting.role}
        </span>
      </header>

      <div className="interview-layout">
        <section className="interview-stage glass">
          <div className="stage-backdrop" />

          <div className="stage-content">
            <span className="stage-tag">
              CÓC ĐI LÀM · {t("demo")}
            </span>

            <div className="gateway-ui live2d-frame linh-minimal">
              <LinhStage
                speaking={false}
                recording={mic}
                waiting={false}
                mouthLevel={() => 0}
                greet={greetLinh}
                onGreeted={() => setGreetLinh(false)}
                candidateName="Bạn"
                micLevel={mic ? 0.08 : 0}
                showSelfView={false}
              />
            </div>

            <h1>{t("interviewer")}</h1>

            <p>
              {vi
                ? "Phòng luyện tập phỏng vấn mô phỏng."
                : "Simulated interview practice room."}
            </p>

            <div className="question-panel">
              <small>
                {t("questions")}{" "}
                {Math.min(index + 1, questions.length)}
                {" / "}
                {questions.length}
              </small>

              <strong>
                {finished
                  ? (vi
                      ? "Bạn đã hoàn thành tất cả câu hỏi!"
                      : "You have completed all questions!")
                  : question}
              </strong>
            </div>
          </div>

          {camera && (
            <div className="coc-webcam-preview">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
              />
              <span>{vi ? "Camera của bạn" : "Your camera"}</span>
            </div>
          )}

          {sharing && (
            <div className="coc-sharing-preview">
              <video
                ref={screenRef}
                autoPlay
                playsInline
                muted
              />
              <span>
                {vi ? "Đang chia sẻ màn hình" : "Sharing screen"}
              </span>
            </div>
          )}

          {handRaised && (
            <div className="coc-raised-hand">
              ✋ {vi ? "Đã giơ tay" : "Hand raised"}
            </div>
          )}

          {recording && (
            <div className="coc-recording-indicator">
              <span />
              REC
            </div>
          )}

          <span className="stage-corner">
            Cóc Đi Làm · {t("demo")}
          </span>
        </section>

        <AnimatePresence>
          {transcriptVisible && (
            <motion.aside
              className="transcript glass"
              initial={{ opacity: 0, x: 25 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 25 }}
            >
              <div className="transcript-head">
                <div>
                  <FileText size={21} />
                  <strong>{t("transcript")}</strong>
                </div>

                <button
                  onClick={() => setTranscriptVisible(false)}
                  aria-label="Close transcript"
                >
                  ×
                </button>
              </div>

              <p className="transcript-sub">
                {vi
                  ? "Câu hỏi và câu trả lời của bạn"
                  : "Questions and your answers"}
              </p>

              <div className="transcript-scroll">
                {activeMeeting.transcript.length === 0 ? (
                  <div className="transcript-empty">
                    <MessageCircle size={32} />

                    <strong>
                      {vi
                        ? "Buổi phỏng vấn bắt đầu tại đây"
                        : "Your conversation starts here"}
                    </strong>

                    <span>
                      {vi
                        ? "Nhập câu trả lời bên dưới để bắt đầu."
                        : "Type your answer below to begin."}
                    </span>
                  </div>
                ) : (
                  activeMeeting.transcript.map((entry, i) => (
                    <div
                      key={i}
                      className={
                        "message " +
                        (entry.speaker === "You"
                          ? "message-user"
                          : "message-ai")
                      }
                    >
                      <small>
                        {entry.speaker === "You"
                          ? t("you")
                          : t("interviewer")}
                      </small>

                      <p>{entry.text}</p>
                    </div>
                  ))
                )}
              </div>

              <form className="transcript-form" onSubmit={submit}>
                <textarea
                  rows={3}
                  value={answer}
                  onChange={e => setAnswer(e.target.value)}
                  placeholder={t("answer")}
                  disabled={finished}
                />

                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={!answer.trim() || finished}
                >
                  <Send size={16} />
                  {t("submit")}
                </button>
              </form>
            </motion.aside>
          )}
        </AnimatePresence>
      </div>

      <footer className="room-footer coc-room-footer">
        <div className="footer-label">
          {savedAnswers} {t("saved")}
        </div>

        <div className="controls glass coc-meeting-controls">
          {tool(
            mic ? Mic : MicOff,
            vi ? "Microphone" : "Microphone",
            toggleMic,
            mic
          )}

          {tool(
            camera ? Video : VideoOff,
            vi ? "Camera" : "Camera",
            toggleCamera,
            camera
          )}

          {tool(
            MonitorUp,
            vi ? "Chia sẻ màn hình" : "Share screen",
            toggleShare,
            sharing
          )}

          {tool(
            Hand,
            vi ? "Giơ tay" : "Raise hand",
            () => setHandRaised(!handRaised),
            handRaised
          )}

          {tool(
            Captions,
            vi ? "Phụ đề" : "Captions",
            () => {
              setCaptions(!captions);
              notify(
                vi
                  ? "Phụ đề tự động cần tích hợp nhận dạng giọng nói."
                  : "Automatic captions require speech recognition."
              );
            },
            captions
          )}

          {tool(
            recording ? Square : Circle,
            recording
              ? (vi ? "Dừng ghi âm" : "Stop recording")
              : (vi ? "Bắt đầu ghi âm" : "Start recording"),
            recording ? stopRecording : startRecording,
            recording
          )}

          <div className="coc-more-wrapper">
            {tool(
              MoreHorizontal,
              vi ? "Tùy chọn khác" : "More options",
              () => setMoreOpen(!moreOpen),
              moreOpen
            )}

            {moreOpen && (
              <div className="coc-more-menu">
                <button
                  onClick={() => {
                    setTranscriptVisible(v => !v);
                    setMoreOpen(false);
                  }}
                >
                  <FileText size={17} />
                  {transcriptVisible
                    ? t("hideTranscript")
                    : t("showTranscript")}
                </button>

                {recordedUrl && (
                  <button
                    onClick={() => {
                      downloadRecording();
                      setMoreOpen(false);
                    }}
                  >
                    <Download size={17} />
                    {vi ? "Tải bản ghi âm" : "Download recording"}
                  </button>
                )}
              </div>
            )}
          </div>

          {tool(
            PhoneOff,
            t("end"),
            () => setConfirmEnd(true),
            false,
            true
          )}
        </div>

        <button
          className="room-pill coc-transcript-toggle"
          onClick={() => setTranscriptVisible(v => !v)}
        >
          <FileText size={18} />
          {transcriptVisible
            ? t("hideTranscript")
            : t("showTranscript")}
        </button>
      </footer>

      {recordedUrl && !recording && (
        <div className="coc-recording-download">
          <span>
            {vi ? "Đã có bản ghi âm" : "Recording ready"}
          </span>

          <button onClick={downloadRecording}>
            <Download size={16} />
            {vi ? "Tải xuống" : "Download"}
          </button>

          <button
            aria-label="Close"
            onClick={() => setRecordedUrl("")}
          >
            <X size={16} />
          </button>
        </div>
      )}

      {notice && (
        <div
          className="toast"
          onClick={() => setNotice("")}
        >
          {notice}
        </div>
      )}

      {confirmEnd && (
        <div className="modal-backdrop">
          <div className="modal glass">
            <h2>{t("finishTitle")}</h2>
            <p>{t("finishDescription")}</p>

            <div className="modal-actions">
              <button
                className="btn btn-soft"
                onClick={() => setConfirmEnd(false)}
              >
                {t("continue")}
              </button>

              <button
                className="btn btn-primary"
                onClick={end}
              >
                <Check size={18} />
                {t("finish")}
              </button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
