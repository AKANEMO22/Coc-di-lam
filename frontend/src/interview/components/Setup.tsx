import { useState } from "react";
import {
  BriefcaseBusiness,
  Building2,
  ChartNoAxesColumnIncreasing,
  CheckCircle2,
  FileText,
  GraduationCap,
  Lightbulb,
  Mic,
  Pencil,
  Play,
  Save,
} from "lucide-react";
import { api, type SessionView } from "../api";

const ROUND: Record<string, string> = {
  hr: "HR",
  technical: "Kỹ thuật",
  hiring_manager: "Quản lý tuyển dụng",
  combined: "Tổng hợp",
};

interface Props {
  view: SessionView;
  connected: boolean;
  onChanged: () => void;
  onStart: () => Promise<void>;
  micLevel: number;
  onMicTest: () => Promise<boolean>;
}

const list = (items: unknown, format: (item: any) => string) =>
  Array.isArray(items) && items.length ? (
    <ul>{items.slice(0, 6).map((item, i) => <li key={i}>{format(item)}</li>)}</ul>
  ) : null;

export function Setup({ view, connected, onChanged, onStart, onMicTest }: Props) {
  const profile = view.candidate_profile ?? {};
  const form = view.form ?? {};
  const brief = view.brief ?? {};
  const duration = brief.duration ?? {};
  const [plan, setPlan] = useState({
    seniority: form.seniority ?? "",
    interview_round: form.interview_round ?? "",
    duration_minutes: form.duration_minutes ?? "",
  });
  const [editing, setEditing] = useState(false);
  const [json, setJson] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [micOk, setMicOk] = useState<boolean | null>(null);

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const savePlan = () => run("plan", () => api.updatePlan(view.session_id, {
    seniority: plan.seniority || null,
    interview_round: plan.interview_round || null,
    duration_minutes: plan.duration_minutes ? Number(plan.duration_minutes) : null,
  }));

  const saveProfile = () => run("profile", async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(json);
    } catch (e) {
      throw new Error(`JSON không hợp lệ: ${(e as Error).message}`);
    }
    await api.updateProfile(view.session_id, parsed as Record<string, unknown>);
    setEditing(false);
  });

  const limitations: string[] = Array.isArray(brief.limitations) ? brief.limitations : [];
  const proposedRound = ROUND[brief.interview_round?.value] ?? brief.interview_round?.value ?? "Chưa xác định";

  return (
    <div className="ready-setup-grid">
      <section className="ready-card ready-profile-card">
        <div className="ready-profile-heading">
          <span className="ready-identity-icon"><Building2 size={27} /></span>
          <div className="ready-identity-copy">
            <h2>{form.candidate_name || "Hồ sơ ứng viên"}</h2>
            <p>{[form.target_role, form.target_company].filter(Boolean).join(" · ") || "Thông tin ứng tuyển"}</p>
          </div>
          <button
            type="button"
            className="ready-outline-button"
            onClick={() => {
              setJson(JSON.stringify(profile, null, 2));
              setEditing(!editing);
            }}
          >
            <Pencil size={16} />{editing ? "Đóng" : "Sửa hồ sơ"}
          </button>
        </div>

        {editing ? (
          <div className="ready-profile-editor">
            <label htmlFor="candidate-profile-json">Nội dung hồ sơ trích xuất</label>
            <textarea
              id="candidate-profile-json"
              className="mono"
              rows={16}
              value={json}
              onChange={(e) => setJson(e.target.value)}
            />
            <button className="ready-primary" onClick={saveProfile} disabled={busy !== null}>
              <Save size={17} />{busy === "profile" ? "Đang lưu…" : "Lưu hồ sơ"}
            </button>
          </div>
        ) : (
          <div className="ready-profile-body">
            {profile.summary && (
              <section className="ready-profile-section">
                <h3><FileText size={18} />Giới thiệu</h3>
                <p>{profile.summary}</p>
              </section>
            )}
            {Array.isArray(profile.experience) && profile.experience.length > 0 && (
              <section className="ready-profile-section">
                <h3><BriefcaseBusiness size={18} />Kinh nghiệm</h3>
                {list(profile.experience, (item) => `${item.role ?? ""}${item.company ? ` · ${item.company}` : ""}${item.period ? ` (${item.period})` : ""}`)}
              </section>
            )}
            {Array.isArray(profile.projects) && profile.projects.length > 0 && (
              <section className="ready-profile-section">
                <h3><BriefcaseBusiness size={18} />Dự án tiêu biểu</h3>
                <div className="ready-project-list">
                  {list(profile.projects, (item) => `${item.name ?? "Dự án"}${item.technologies?.length ? ` · ${item.technologies.join(", ")}` : ""}`)}
                </div>
              </section>
            )}
            <div className="ready-profile-details">
              {Array.isArray(profile.skills) && profile.skills.length > 0 && (
                <section className="ready-profile-section">
                  <h3><ChartNoAxesColumnIncreasing size={18} />Kỹ năng</h3>
                  <p>{profile.skills.slice(0, 20).map((skill: any) => typeof skill === "string" ? skill : skill.name).join(", ")}</p>
                </section>
              )}
              {Array.isArray(profile.education) && profile.education.length > 0 && (
                <section className="ready-profile-section">
                  <h3><GraduationCap size={18} />Học vấn</h3>
                  {list(profile.education, (item) => `${item.institution ?? ""}${item.field ? ` · ${item.field}` : ""}${item.period ? ` (${item.period})` : ""}`)}
                </section>
              )}
            </div>
          </div>
        )}
      </section>

      <section className="ready-card ready-plan-card">
        <header className="ready-card-heading">
          <span className="ready-heading-icon"><FileText size={20} /></span>
          <div>
            <h2>Kế hoạch phỏng vấn</h2>
            <p>Tùy chỉnh buổi phỏng vấn AI theo mục tiêu của bạn</p>
          </div>
        </header>

        <p className="ready-plan-summary">
          <span>Đề xuất</span>
          <strong>{brief.seniority?.value ?? "Chưa xác định"} · {proposedRound} · {duration.planned_minutes ?? "—"} phút</strong>
        </p>

        <div className="ready-plan-fields">
          <label>Cấp độ
            <select value={plan.seniority} onChange={(e) => setPlan({ ...plan, seniority: e.target.value })}>
              <option value="">Theo đề xuất</option>
              {["intern", "fresher", "junior", "mid", "senior"].map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          <label>Vòng
            <select value={plan.interview_round} onChange={(e) => setPlan({ ...plan, interview_round: e.target.value })}>
              <option value="">Theo đề xuất</option>
              {Object.entries(ROUND).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label>Thời lượng (phút)
            <input
              type="number"
              min={10}
              max={120}
              value={plan.duration_minutes}
              placeholder={String(duration.planned_minutes ?? 40)}
              onChange={(e) => setPlan({ ...plan, duration_minutes: e.target.value })}
            />
          </label>
        </div>

        <div className="ready-tip">
          <Lightbulb size={21} />
          <div><strong>Gợi ý</strong><p>Bạn có thể chọn cấp độ, vòng phỏng vấn và thời lượng phù hợp với mục tiêu luyện tập.</p></div>
        </div>
        <button className="ready-primary ready-save-plan" onClick={savePlan} disabled={busy !== null}>
          <Play size={17} fill="currentColor" />{busy === "plan" ? "Đang lưu…" : "Lưu kế hoạch"}
        </button>
        {limitations.length > 0 && (
          <div className="ready-limitations"><strong>Lưu ý trước khi bắt đầu</strong><ul>{limitations.map((item, i) => <li key={i}>{item}</li>)}</ul></div>
        )}
      </section>

      <section className="ready-card ready-start-card">
        <header className="ready-card-heading ready-start-heading">
          <span className="ready-heading-icon"><Mic size={20} /></span>
          <div>
            <h2>Bắt đầu phỏng vấn</h2>
            <p>Chọn cách bạn muốn trò chuyện với Linh</p>
          </div>
        </header>

        <div className="ready-mode-grid">
          <div className="ready-mode-card selected ready-voice-mode">
            <span className="ready-mode-icon"><Mic size={23} /></span>
            <span><strong>Phỏng vấn bằng giọng nói</strong><small>Người phỏng vấn sẽ đọc câu hỏi. Bật micro khi trả lời, bấm lần nữa để gửi.</small></span>
            <CheckCircle2 className="ready-mode-check" size={20} aria-hidden="true" />
          </div>
        </div>

        {error && <p className="ready-error" role="alert">{error}</p>}
        <div className="ready-start-actions">
          <div className="ready-mic-check-row">
            <button type="button" className="ready-outline-button" onClick={async () => setMicOk(await onMicTest())}>
              <Mic size={16} />Kiểm tra micro
            </button>
            {micOk === true && <span className="ready-mic-ok"><CheckCircle2 size={16} />Micro đã sẵn sàng</span>}
            {micOk === false && <span className="ready-mic-error">Chưa có quyền dùng micro</span>}
          </div>
          <button
            className="ready-primary ready-start-button"
            disabled={!connected || busy !== null}
            onClick={() => run("start", onStart)}
          >
            <Play size={18} fill="currentColor" />
            {busy === "start" ? "Đang bắt đầu…" : connected ? "Bắt đầu phỏng vấn" : "Đang kết nối…"}
          </button>
        </div>
      </section>
    </div>
  );
}
