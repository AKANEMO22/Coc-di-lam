import { useState, type FormEvent } from "react";
import { api } from "../api";

const MAX_BYTES = 12 * 1024 * 1024;
const ACCEPT = ".pdf,.docx,.txt";
const PENDING = "interview:pending-create";

async function fingerprint(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function CreateSession({ disabled, onCreated }: { disabled: boolean; onCreated: (id: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cvMode, setCvMode] = useState<"file" | "text">("file");
  const [jdMode, setJdMode] = useState<"file" | "text">("text");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const text = (name: string) => String(data.get(name) ?? "").trim();
    const number = (name: string) => (text(name) ? Number(text(name)) : null);
    const cvFile = cvMode === "file" ? (data.get("cv_file") as File | null) : null;
    const jdFile = jdMode === "file" ? (data.get("jd_file") as File | null) : null;
    const payload = {
      candidate_name: text("candidate_name"),
      target_role: text("target_role") || null,
      target_company: text("target_company") || "Target Company",
      experience_years: number("experience_years"),
      skills: text("skills").split(",").map((s) => s.trim()).filter(Boolean),
      cv_text: cvMode === "text" ? text("cv_text") || null : null,
      job_description: jdMode === "text" ? text("job_description") || null : null,
      seniority: text("seniority") || null,
      interview_round: text("interview_round") || null,
      duration_minutes: number("duration_minutes"),
      interview_language: text("interview_language") || "vi",
    };
    if (!payload.candidate_name) return setError("Cần nhập họ tên.");
    if (!payload.cv_text && !cvFile?.size) return setError("Hãy tải CV lên hoặc dán nội dung CV.");
    if ([cvFile, jdFile].some((f) => f && f.size > MAX_BYTES)) return setError("Mỗi tệp tối đa 12 MB.");

    const body = new FormData();
    body.append("payload", JSON.stringify(payload));
    if (cvFile?.size) body.append("cv_file", cvFile, cvFile.name);
    if (jdFile?.size) body.append("job_description_file", jdFile, jdFile.name);
    // Retrying the same unfinished submission reuses its key (no duplicate session); a new submission gets a new key.
    const print = await fingerprint(JSON.stringify(payload) + (cvFile ? `${cvFile.name}:${cvFile.size}:${cvFile.lastModified}` : "")
      + (jdFile ? `${jdFile.name}:${jdFile.size}:${jdFile.lastModified}` : ""));
    let pending: { print: string; key: string } | null = null;
    try { pending = JSON.parse(sessionStorage.getItem(PENDING) || "null"); } catch { /* stale */ }
    const key = pending?.print === print ? pending.key : crypto.randomUUID();
    sessionStorage.setItem(PENDING, JSON.stringify({ print, key }));

    setBusy(true);
    setError(null);
    try {
      const result = await api.create(body, key);
      sessionStorage.removeItem(PENDING);
      onCreated(result.session_id);
    } catch (e) {
      setError(`Không tạo được phiên: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card create" onSubmit={submit}>
      <h2>Phiên phỏng vấn mới</h2>
      <p className="muted">Tải CV (và JD nếu có). Core sẽ phân tích hồ sơ, lập kế hoạch rồi bạn xem lại trước khi bắt đầu.</p>

      <div className="grid two">
        <label>Họ tên *<input name="candidate_name" required autoComplete="name" /></label>
        <label>Vị trí ứng tuyển<input name="target_role" placeholder="Backend Developer" /></label>
        <label>Công ty<input name="target_company" placeholder="FPT Software" /></label>
        <label>Số năm kinh nghiệm<input name="experience_years" type="number" min="0" max="50" step="0.5" /></label>
      </div>
      <label>Kỹ năng chính<input name="skills" placeholder="Python, FastAPI, PostgreSQL" /></label>

      <fieldset>
        <legend>CV *</legend>
        <div className="segmented">
          <button type="button" className={cvMode === "file" ? "on" : ""} onClick={() => setCvMode("file")}>Tải tệp</button>
          <button type="button" className={cvMode === "text" ? "on" : ""} onClick={() => setCvMode("text")}>Dán nội dung</button>
        </div>
        {cvMode === "file"
          ? <input name="cv_file" type="file" accept={ACCEPT} />
          : <textarea name="cv_text" rows={6} placeholder="Dán nội dung CV…" />}
        <small className="muted">PDF có chữ, DOCX hoặc TXT · tối đa 12 MB. PDF dạng ảnh scan chưa hỗ trợ.</small>
      </fieldset>

      <fieldset>
        <legend>Mô tả công việc (JD)</legend>
        <div className="segmented">
          <button type="button" className={jdMode === "text" ? "on" : ""} onClick={() => setJdMode("text")}>Dán nội dung</button>
          <button type="button" className={jdMode === "file" ? "on" : ""} onClick={() => setJdMode("file")}>Tải tệp</button>
        </div>
        {jdMode === "file"
          ? <input name="jd_file" type="file" accept={ACCEPT} />
          : <textarea name="job_description" rows={4} placeholder="Không bắt buộc" />}
      </fieldset>

      <div className="grid four">
        <label>Ngôn ngữ
          <select name="interview_language" defaultValue="vi"><option value="vi">Tiếng Việt</option><option value="en">English</option></select>
        </label>
        <label>Vòng
          <select name="interview_round" defaultValue="">
            <option value="">Tự đề xuất</option><option value="hr">HR</option><option value="technical">Kỹ thuật</option>
            <option value="hiring_manager">Quản lý tuyển dụng</option><option value="combined">Tổng hợp</option>
          </select>
        </label>
        <label>Cấp độ
          <select name="seniority" defaultValue="">
            <option value="">Tự suy luận</option><option value="intern">Intern</option><option value="fresher">Fresher</option>
            <option value="junior">Junior</option><option value="mid">Mid</option><option value="senior">Senior</option>
          </select>
        </label>
        <label>Thời lượng (phút)<input name="duration_minutes" type="number" min="10" max="120" placeholder="Tự đề xuất" /></label>
      </div>

      {error && <p className="notice error">{error}</p>}
      <button className="primary" disabled={disabled || busy}>{busy ? "Đang tạo phiên…" : "Tạo phiên"}</button>
    </form>
  );
}
