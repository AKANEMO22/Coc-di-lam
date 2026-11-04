import React, { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, ArrowLeft, Briefcase, Building2, CheckCircle2, Clock3, FileText, FolderOpen,
  Layers, Loader2, Pencil, Plus, ScanText, Sparkles, Video
} from "lucide-react";

import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import { useApp } from "../context/AppContext";
import { useLanguage } from "../context/LanguageContext";
import { cvStorage } from "../utils/cvStorage";
import { useToast } from "@interview/toast";
import {
  DETAIL_LABELS, SENIORITY_OPTIONS, interviewDetails, missingDetails, needsOcr, skillList
} from "../utils/cvProfile";
import CVDetailsDialog from "../components/CVDetailsDialog";
import { api } from "@interview/api";
import "../gateway.css";

const PENDING_CREATE = "coc:pending-interview-create";

export default function PrepareInterview() {
  const navigate = useNavigate();
  const location = useLocation();
  const [params] = useSearchParams();
  const { user } = useApp();
  const { language } = useLanguage();
  const vi = language === "vi";

  // Prefilled state from "Khám phá việc làm" (Jobs)
  const jobState = location.state || {};

  const [cvs, setCvs] = useState([]);
  const [selected, setSelected] = useState(params.get("cv") || "");
  const [selectedCv, setSelectedCv] = useState(null);
  const [details, setDetails] = useState(() => interviewDetails(null, jobState));
  const [checking, setChecking] = useState(false);
  const [dialog, setDialog] = useState(null); // "start" | "edit" | null
  const [savingDetails, setSavingDetails] = useState(false);
  const loadRequest = useRef(0);
  const [duration, setDuration] = useState("");
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [consent, setConsent] = useState(false);

  useEffect(() => {
    let active = true;
    const check = () => api.health().then(value => {
      if (active) { setHealth(value); setHealthError(""); }
    }).catch(() => {
      if (active) setHealthError(vi
        ? "Không kết nối được backend. Hãy chạy gateway ở cổng 8020."
        : "Cannot connect to the backend. Start the gateway on port 8020.");
    });
    void check();
    const timer = window.setInterval(check, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [vi]);

  useEffect(() => {
    cvStorage.getAll()
      .then(setCvs)
      .catch(() => setError(vi ? "Không thể tải danh sách CV." : "Unable to load CVs."))
      .finally(() => setLoading(false));
  }, [vi]);

  // Load the chosen CV, read it once on the backend if it was never checked, and merge its saved answers.
  useEffect(() => {
    const request = ++loadRequest.current;
    setSelectedCv(null);
    if (!selected) { setDetails(interviewDetails(null, jobState)); return; }
    (async () => {
      let cv = await cvStorage.get(selected);
      if (request !== loadRequest.current) return;
      if (!cv) { setDetails(interviewDetails(null, jobState)); return; }
      setSelectedCv(cv);
      setDetails(interviewDetails(cv, jobState));
      if (cv.analysis || !cv.file) return;
      setChecking(true);
      try {
        const analysis = await api.analyzeCv(cv.file, cv.name);
        cv = await cvStorage.update(cv.id, { analysis });
        if (request !== loadRequest.current) return;
        setSelectedCv(cv);
        setDetails(interviewDetails(cv, jobState));
      } catch {
        // Backend unreachable: the required fields are still asked for before the session starts.
      } finally {
        if (request === loadRequest.current) setChecking(false);
      }
    })().catch(() => {
      if (request === loadRequest.current) setError(vi ? "Không thể đọc CV đã lưu." : "Unable to read the saved CV.");
    });
    // jobState comes from the navigation and does not change while this page is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const missing = selectedCv ? missingDetails(selectedCv, details) : [];
  const ocr = needsOcr(selectedCv);

  async function saveDetails(next) {
    if (!selectedCv) return null;
    const saved = { ...next, experienceYears: next.experienceYears === "" ? "" : Number(next.experienceYears) };
    const cv = await cvStorage.update(selectedCv.id, {
      details: saved,
      role: saved.role.trim() || selectedCv.role || "",
    });
    setSelectedCv(cv);
    setCvs(list => list.map(item => item.id === cv.id ? cv : item));
    return cv;
  }

  async function onDialogSave(next) {
    setSavingDetails(true);
    try {
      const cv = await saveDetails(next);
      setDetails(next);
      const startAfter = dialog === "start";
      setDialog(null);
      if (startAfter && cv) await create(cv, next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingDetails(false);
    }
  }

  function begin() {
    if (creating) return;
    if (!selectedCv) {
      setError(vi ? "Vui lòng chọn CV." : "Please select a CV.");
      return;
    }
    if (!consent) {
      setError(vi ? "Hãy đồng ý để hệ thống xử lý giọng nói và nội dung phỏng vấn." : "Please agree to the processing of your interview voice and content.");
      return;
    }
    if (duration && (!Number.isInteger(Number(duration)) || Number(duration) < 10 || Number(duration) > 120)) {
      setError(vi ? "Thời lượng phải từ 10 đến 120 phút." : "Duration must be between 10 and 120 minutes.");
      return;
    }
    setError("");
    if (missing.length) { setDialog("start"); return; }
    void create(selectedCv, details);
  }

  async function create(cvRecord, d) {
    setCreating(true);
    setError("");
    try {
      const cv = cvRecord?.file ? cvRecord : await cvStorage.get(cvRecord.id);
      if (!cv?.file) throw new Error(vi
        ? "Không tìm thấy tệp CV đã lưu. Hãy tải CV lên lại."
        : "The saved CV file could not be found. Please upload it again.");

      const scanned = needsOcr(cv);
      const skills = scanned ? skillList(d.skills) : (jobState.skills || []);
      const years = d.experienceYears === "" || d.experienceYears == null ? null : Number(d.experienceYears);
      const payload = {
        candidate_name: user?.name || cv.analysis?.fields?.candidate_name || user?.email || (vi ? "Ứng viên" : "Candidate"),
        target_role: d.role.trim(),
        target_company: d.company.trim(),
        seniority: d.seniority,
        interview_round: d.round || null,
        experience_years: scanned ? years : null,
        skills,
        cv_text: null,
        job_description: d.jobDescription.trim() || null,
        additional_requirements: d.notes.trim() || null,
        duration_minutes: duration ? Number(duration) : null,
        interview_language: language,
        // A CV without a text layer cannot be read by ID_Q: the hand-entered profile is the source instead.
        ...(scanned ? { profile: { summary: d.summary.trim() || null, skills, experience_years: years,
                                   current_role: cv.analysis?.fields?.current_role || null } } : {}),
        metadata: { recording_consent: true, consent_at: new Date().toISOString(), cv_needs_ocr: scanned || undefined }
      };
      const body = new FormData();
      body.append("payload", JSON.stringify(payload));
      if (!scanned) body.append("cv_file", cv.file, cv.name);

      const print = JSON.stringify([payload, cv.id, cv.name, cv.size, cv.file.lastModified, user?.email]);
      let pending;
      try { pending = JSON.parse(sessionStorage.getItem(PENDING_CREATE) || "null"); } catch { /* stale entry */ }
      const key = pending?.print === print ? pending.key : crypto.randomUUID();
      sessionStorage.setItem(PENDING_CREATE, JSON.stringify({ print, key }));

      const result = await api.create(body, key);
      sessionStorage.removeItem(PENDING_CREATE);
      // Remember what this CV was used for, so the next session starts pre-filled.
      await saveDetails(d).catch(() => {});
      navigate(`/interview/${encodeURIComponent(result.session_id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  const coreUnavailable = health && !health.core?.ok;
  const speechUnavailable = health && !health.speech?.ok;
  const coreMessage = !coreUnavailable ? null : health.core?.body?.details?.provider === "unconfigured"
    ? (vi ? "Core AI đã kết nối nhưng chưa được cấu hình nhà cung cấp LLM. Hãy kiểm tra API key và model rồi khởi động lại dịch vụ."
      : "Core AI is connected but no LLM provider is configured. Check the API key and model, then restart the service.")
    : (vi ? "Core AI chưa sẵn sàng. Hãy kiểm tra dịch vụ Core AI và Redis." : "Core AI is not ready. Check the Core AI and Redis services.");
  const speechMessage = !speechUnavailable ? null : (!health.speech?.reachable
    ? (vi ? "Chưa kết nối được dịch vụ giọng nói ở cổng 8010. " : "Speech service on port 8010 is unreachable. ")
    : health.speech?.body?.tts?.state === "loading"
      ? (vi ? "Dịch vụ giọng nói đang tải model TTS. " : "Speech service is loading its TTS model. ")
      : (vi ? "Dịch vụ giọng nói chưa sẵn sàng. " : "Speech service is not ready. "))
    + (vi ? "Bạn vẫn có thể trả lời bằng văn bản." : "You can still answer by text.");
  useToast(healthError, { id: "prepare-health", tone: "error", duration: 8000 });
  useToast(coreMessage, { id: "prepare-core", tone: "error", duration: 9000 });
  useToast(speechMessage, { id: "prepare-speech", tone: "warn", duration: 7000 });
  useToast(error, { id: "prepare-error", tone: "error" });

  return (
    <main className="workspace-shell cv-prepare-shell">
      <div className="scenery" />
      <Sidebar />

      <section className="workspace-content cv-prepare-content">
        <Topbar />

        <div className="cv-prepare glass">
          <div className="cv-prepare-topline">
            <button
              className="btn btn-soft cv-prepare-back"
              onClick={() => navigate(jobState.role ? "/jobs" : "/home")}
            >
              <ArrowLeft size={17} />
              {vi ? (jobState.role ? "Quay lại danh sách việc làm" : "Quay lại") : (jobState.role ? "Back to Jobs" : "Back")}
            </button>
            <div className="cv-prepare-heading">
              <Video size={27} />
              <div>
                <h1>{vi ? "Chuẩn bị phiên phỏng vấn" : "Prepare Interview Session"}</h1>
                <p>{vi
                  ? "Chọn CV, chúng tôi sẽ tạo buổi phỏng vấn phù hợp với hồ sơ của bạn."
                  : "Choose a CV and we’ll tailor the interview to your profile."}</p>
              </div>
            </div>
            <span className="cv-prepare-spacer" aria-hidden="true" />
          </div>

          {jobState.role && (
            <div
              className="glass"
              style={{
                padding: "14px 20px",
                borderRadius: 16,
                background: "linear-gradient(135deg, rgba(235, 243, 255, 0.95), rgba(240, 248, 255, 0.9))",
                border: "1.5px solid #bdd7ff",
                color: "#184ba8",
                marginBottom: 20,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 12
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <Sparkles size={20} color="#2263ed" />
                <div>
                  <div style={{ fontWeight: 800, fontSize: 13 }}>
                    {vi ? "🎯 Đã nạp thông tin từ Khám phá việc làm IT" : "🎯 Pre-filled from Explore IT Jobs"}
                  </div>
                  <div style={{ fontSize: 12, color: "#476fae", marginTop: 2 }}>
                    <strong>{jobState.role}</strong> {jobState.company ? `tại ${jobState.company}` : ""}
                  </div>
                </div>
              </div>
              <span style={{ fontSize: 11, fontWeight: 800, background: "#d9e9ff", color: "#1f58b8", padding: "4px 10px", borderRadius: 20 }}>
                {vi ? "Tự động điền" : "Auto-filled"}
              </span>
            </div>
          )}

          <div className="cv-prepare-options">
            <div className={`cv-details-summary ${selectedCv && missing.length ? "incomplete" : ""}`}>
              <div className="cv-details-summary-head">
                <strong>{vi ? "Thông tin phiên" : "Session details"}</strong>
                {checking ? (
                  <span className="cv-details-state"><Loader2 size={14} className="spin" /> {vi ? "Đang kiểm tra CV…" : "Checking CV…"}</span>
                ) : selectedCv && ocr && missing.length ? (
                  <span className="cv-details-state warn"><ScanText size={14} /> {vi ? "CV dạng ảnh – cần bổ sung" : "Scanned CV – needs details"}</span>
                ) : selectedCv && missing.length ? (
                  <span className="cv-details-state warn"><AlertTriangle size={14} /> {vi ? `Thiếu ${missing.length} mục` : `${missing.length} missing`}</span>
                ) : selectedCv ? (
                  <span className="cv-details-state ok"><CheckCircle2 size={14} /> {vi ? "Đã đủ" : "Complete"}</span>
                ) : null}
                <button type="button" className="btn btn-soft cv-details-edit" disabled={!selectedCv} onClick={() => setDialog("edit")}>
                  <Pencil size={14} /> {vi ? "Bổ sung" : "Edit"}
                </button>
              </div>
              <ul>
                {[
                  ["company", Building2, details.company],
                  ["role", Briefcase, details.role],
                  ["seniority", Layers, SENIORITY_OPTIONS.find(o => o.value === details.seniority)?.[vi ? "vi" : "en"]],
                ].map(([key, Icon, value]) => (
                  <li key={key} className={value ? "" : "empty"}>
                    <Icon size={14} />
                    <span>{DETAIL_LABELS[key][vi ? "vi" : "en"]}</span>
                    <b title={value || ""}>{value || (vi ? "Chưa có" : "Not set")}</b>
                  </li>
                ))}
              </ul>
            </div>
            <label>
              <span><Clock3 size={16} /> {vi ? "Thời lượng (phút)" : "Duration (minutes)"}</span>
              <input type="number" min={10} max={120} value={duration} onChange={e => setDuration(e.target.value)} placeholder={vi ? "Tự đề xuất" : "Automatic"} />
            </label>
          </div>

          <div className="cv-prepare-selection">
            <div className="cv-selection-title">
              <h2>{vi ? "Chọn CV để bắt đầu" : "Select a CV to continue"}</h2>
              <span>{cvs.length} {vi ? (cvs.length === 1 ? "CV" : "CV") : (cvs.length === 1 ? "resume" : "resumes")}</span>
            </div>

            {loading ? (
              <div className="cv-selection-message">{vi ? "Đang tải danh sách CV..." : "Loading resumes..."}</div>
            ) : cvs.length === 0 ? (
              <div className="cv-empty cv-prepare-empty">
                <FolderOpen size={35} />
                <p>{vi ? "Bạn chưa tải CV nào lên." : "You have not uploaded a resume yet."}</p>
                <button className="btn btn-primary" onClick={() => navigate("/cv-library")}>
                  <Plus size={17} /> {vi ? "Tải CV lên" : "Upload a resume"}
                </button>
              </div>
            ) : (
              <div className="cv-selection-list">
                {cvs.map(cv => (
                  <button
                    key={cv.id}
                    type="button"
                    className={`cv-select-item ${selected === cv.id ? "selected" : ""}`}
                    aria-pressed={selected === cv.id}
                    onClick={() => { setSelected(cv.id); setError(""); }}
                  >
                    <FileText size={22} />
                    <span className="cv-select-copy">
                      <strong>{cv.name}</strong>
                      <small>{cv.role || (vi ? "Hồ sơ cá nhân" : "Resume")}</small>
                    </span>
                    {selected === cv.id && <CheckCircle2 size={21} />}
                  </button>
                ))}
              </div>
            )}
          </div>


          <label className="consent-line">
            <input type="checkbox" checked={consent} onChange={e => { setConsent(e.target.checked); setError(""); }} />
            <span>{vi
              ? "Mình đồng ý để hệ thống xử lý giọng nói và nội dung buổi phỏng vấn nhằm tạo báo cáo. Mình có thể xóa phiên bất cứ lúc nào trong Lịch sử phỏng vấn."
              : "I agree to the processing of my interview voice and content to create a report. I can delete the session at any time from Interview history."}</span>
          </label>

          <button
            className="btn btn-primary cv-begin"
            disabled={!selected || creating || checking || !health?.core?.ok}
            onClick={begin}
          >
            <Video size={19} />
            {creating
              ? (vi ? "Đang tạo phiên…" : "Creating session…")
              : (vi ? "Tạo phiên phỏng vấn" : "Create interview session")}
          </button>
        </div>
      </section>
      {dialog && selectedCv && (
        <CVDetailsDialog
          cv={selectedCv}
          initial={details}
          vi={vi}
          mode={dialog}
          saving={savingDetails || creating}
          onCancel={() => setDialog(null)}
          onSave={onDialogSave}
        />
      )}
    </main>
  );
}
