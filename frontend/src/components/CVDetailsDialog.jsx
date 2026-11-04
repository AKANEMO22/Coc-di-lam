import React, { useEffect, useRef, useState } from "react";
import { AlertTriangle, Building2, Briefcase, CheckCircle2, FileText, Layers, ScanText, X } from "lucide-react";

import { DETAIL_LABELS, ROUND_OPTIONS, SENIORITY_OPTIONS, missingDetails, needsOcr } from "../utils/cvProfile";

/**
 * Asks for what a CV does not tell us (target company, role, level, and for a scanned CV a hand-written
 * profile) plus optional interview material (JD, round, notes). Saved onto the CV for the next session.
 */
export default function CVDetailsDialog({ cv, initial, vi, mode = "start", saving = false, onCancel, onSave }) {
  const [details, setDetails] = useState(initial);
  const [showErrors, setShowErrors] = useState(mode === "start");
  const firstField = useRef(null);
  const ocr = needsOcr(cv);
  const missing = missingDetails(cv, details);
  const fields = cv?.analysis?.fields || {};
  const t = (vn, en) => (vi ? vn : en);
  const label = key => DETAIL_LABELS[key][vi ? "vi" : "en"];
  const invalid = key => showErrors && missing.includes(key);

  useEffect(() => {
    firstField.current?.focus();
    const onKey = event => { if (event.key === "Escape" && !saving) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel, saving]);

  function set(key, value) {
    setDetails(current => ({ ...current, [key]: value }));
  }

  function submit(event) {
    event.preventDefault();
    if (missing.length) { setShowErrors(true); return; }
    onSave(details);
  }

  const detected = [
    fields.candidate_name && { k: t("Họ tên", "Name"), v: fields.candidate_name },
    fields.current_role && { k: t("Vị trí trong CV", "Role in CV"), v: fields.current_role },
    fields.experience_years != null && { k: t("Kinh nghiệm", "Experience"), v: `${fields.experience_years} ${t("năm", "years")}` },
    fields.skills?.length > 0 && { k: t("Kỹ năng", "Skills"), v: fields.skills.slice(0, 6).join(", ") + (fields.skills.length > 6 ? "…" : "") },
  ].filter(Boolean);

  return (
    <div className="cv-details-overlay" onMouseDown={event => event.target === event.currentTarget && !saving && onCancel()}>
      <form className="cv-details-dialog" role="dialog" aria-modal="true" aria-labelledby="cv-details-title" onSubmit={submit} noValidate>
        <header className="cv-details-header">
          <div>
            <h2 id="cv-details-title">
              {mode === "start" ? t("Bổ sung thông tin trước khi vào phiên", "Complete your details before the session")
                : t("Thông tin phỏng vấn của CV", "Interview details for this CV")}
            </h2>
            <p><FileText size={14} /> {cv?.name}</p>
          </div>
          <button type="button" className="cv-details-close" onClick={onCancel} disabled={saving} aria-label={t("Đóng", "Close")}>
            <X size={20} />
          </button>
        </header>

        <div className="cv-details-body">
          {ocr ? (
            <p className="cv-details-alert warn"><ScanText size={17} />
              {t("CV này là ảnh/scan nên hệ thống không đọc được chữ (cần OCR). Hãy nhập tóm tắt hồ sơ và kỹ năng để buổi phỏng vấn sát với bạn.",
                "This CV is a scan/image, so no text could be read (OCR needed). Add a short profile and your skills so the interview fits you.")}
            </p>
          ) : missing.length > 0 ? (
            <p className="cv-details-alert"><AlertTriangle size={17} />
              {t("Còn thiếu: ", "Still missing: ")}{missing.map(label).join(", ")}
            </p>
          ) : (
            <p className="cv-details-alert ok"><CheckCircle2 size={17} />
              {t("Đã đủ thông tin bắt buộc. Bạn có thể thêm JD hoặc ghi chú để câu hỏi sát hơn.",
                "All required details are present. Add a JD or notes for sharper questions.")}
            </p>
          )}

          {detected.length > 0 && (
            <dl className="cv-details-detected">
              {detected.map(item => <div key={item.k}><dt>{item.k}</dt><dd title={item.v}>{item.v}</dd></div>)}
            </dl>
          )}

          <div className="cv-details-grid">
            <label className={invalid("company") ? "invalid" : ""}>
              <span><Building2 size={15} /> {label("company")} *</span>
              <input ref={firstField} value={details.company} onChange={e => set("company", e.target.value)}
                placeholder={t("Ví dụ: FPT Software, Viettel…", "e.g. Acme, Google…")} maxLength={300} />
            </label>
            <label className={invalid("role") ? "invalid" : ""}>
              <span><Briefcase size={15} /> {label("role")} *</span>
              <input value={details.role} onChange={e => set("role", e.target.value)}
                placeholder={t("Ví dụ: Backend Developer", "e.g. Backend Developer")} maxLength={300} />
            </label>
            <label className={invalid("seniority") ? "invalid" : ""}>
              <span><Layers size={15} /> {label("seniority")} *</span>
              <select value={details.seniority} onChange={e => set("seniority", e.target.value)}>
                <option value="">{t("Chọn cấp bậc", "Choose a level")}</option>
                {SENIORITY_OPTIONS.map(o => <option key={o.value} value={o.value}>{vi ? o.vi : o.en}</option>)}
              </select>
            </label>
            <label>
              <span>{t("Vòng phỏng vấn", "Interview round")}</span>
              <select value={details.round} onChange={e => set("round", e.target.value)}>
                {ROUND_OPTIONS.map(o => <option key={o.value} value={o.value}>{vi ? o.vi : o.en}</option>)}
              </select>
            </label>
          </div>

          {ocr && (
            <div className="cv-details-grid">
              <label className={`wide ${invalid("skills") ? "invalid" : ""}`}>
                <span>{t("Kỹ năng (phân tách bằng dấu phẩy)", "Skills (comma separated)")} *</span>
                <input value={details.skills} onChange={e => set("skills", e.target.value)}
                  placeholder="Python, SQL, React…" maxLength={4000} />
              </label>
              <label>
                <span>{t("Số năm kinh nghiệm", "Years of experience")}</span>
                <input type="number" min={0} max={80} step={0.5} value={details.experienceYears}
                  onChange={e => set("experienceYears", e.target.value)} />
              </label>
              <label className="full">
                <span>{t("Tóm tắt hồ sơ", "Profile summary")}</span>
                <textarea rows={3} value={details.summary} onChange={e => set("summary", e.target.value)} maxLength={6000}
                  placeholder={t("Học vấn, kinh nghiệm, dự án nổi bật…", "Education, experience, key projects…")} />
              </label>
            </div>
          )}

          <div className="cv-details-grid">
            <label className="full">
              <span>{t("Mô tả công việc (JD) — tùy chọn", "Job description (JD) — optional")}</span>
              <textarea rows={4} value={details.jobDescription} onChange={e => set("jobDescription", e.target.value)} maxLength={180000}
                placeholder={t("Dán JD của vị trí để câu hỏi bám sát yêu cầu tuyển dụng.", "Paste the JD so questions follow the hiring requirements.")} />
            </label>
            <label className="full">
              <span>{t("Ghi chú / yêu cầu thêm — tùy chọn", "Notes / extra requirements — optional")}</span>
              <textarea rows={2} value={details.notes} onChange={e => set("notes", e.target.value)} maxLength={8000}
                placeholder={t("Ví dụ: tập trung system design, phỏng vấn bằng tiếng Anh…", "e.g. focus on system design…")} />
            </label>
          </div>
        </div>

        <footer className="cv-details-footer">
          <small>{t("Thông tin được lưu cùng CV này cho các lần sau.", "Saved with this CV for next time.")}</small>
          <div>
            <button type="button" className="btn btn-soft" onClick={onCancel} disabled={saving}>{t("Hủy", "Cancel")}</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? t("Đang lưu…", "Saving…") : mode === "start" ? t("Lưu và tạo phiên", "Save and create session") : t("Lưu", "Save")}
            </button>
          </div>
        </footer>
      </form>
    </div>
  );
}
