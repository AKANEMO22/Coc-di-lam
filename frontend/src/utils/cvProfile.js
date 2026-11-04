// What a stored CV still needs before an interview can be prepared, and how its saved answers merge
// with values handed over from the Jobs page. ID_Q's prepare step requires a company, a role and a
// level; a CV without a text layer (scan/photo) additionally needs a hand-entered profile.

export const SENIORITY_OPTIONS = [
  { value: "intern", vi: "Thực tập sinh", en: "Intern" },
  { value: "fresher", vi: "Fresher", en: "Fresher" },
  { value: "junior", vi: "Junior", en: "Junior" },
  { value: "mid", vi: "Middle", en: "Mid-level" },
  { value: "senior", vi: "Senior", en: "Senior" },
];

export const ROUND_OPTIONS = [
  { value: "", vi: "Tự đề xuất", en: "Automatic" },
  { value: "hr", vi: "Vòng HR", en: "HR round" },
  { value: "technical", vi: "Vòng kỹ thuật", en: "Technical round" },
  { value: "hiring_manager", vi: "Vòng quản lý tuyển dụng", en: "Hiring manager" },
  { value: "combined", vi: "Kết hợp", en: "Combined" },
];

const SENIORITY_VALUES = new Set(SENIORITY_OPTIONS.map(option => option.value));

export function normalizeSeniority(raw) {
  const value = String(raw || "").trim().toLowerCase();
  if (["lead", "manager", "principal", "director"].includes(value)) return "senior";
  if (["middle", "mid-level"].includes(value)) return "mid";
  return SENIORITY_VALUES.has(value) ? value : "";
}

export function needsOcr(cv) {
  return cv?.analysis?.readable === false;
}

/** Values for the next session: Jobs hand-over first, then the CV's saved answers, then CV detection. */
export function interviewDetails(cv, jobState = {}) {
  const saved = cv?.details || {};
  const fields = cv?.analysis?.fields || {};
  return {
    company: String(jobState.company || saved.company || "").trim(),
    role: String(jobState.role || saved.role || cv?.role || fields.current_role || "").trim(),
    seniority: normalizeSeniority(jobState.seniority) || normalizeSeniority(saved.seniority)
      || normalizeSeniority(fields.seniority),
    round: saved.round || "",
    jobDescription: String(jobState.jobDescription || saved.jobDescription || ""),
    notes: String(saved.notes || ""),
    skills: String(saved.skills || ""),
    experienceYears: saved.experienceYears ?? "",
    summary: String(saved.summary || ""),
  };
}

/** Keys of `details` the candidate must fill before the session can be created. */
export function missingDetails(cv, details) {
  const missing = [];
  if (!details.company.trim()) missing.push("company");
  if (!details.role.trim()) missing.push("role");
  if (!details.seniority) missing.push("seniority");
  if (needsOcr(cv) && !details.skills.trim() && !details.summary.trim()) missing.push("skills");
  return missing;
}

export const DETAIL_LABELS = {
  company: { vi: "Công ty mục tiêu", en: "Target company" },
  role: { vi: "Vị trí ứng tuyển", en: "Target role" },
  seniority: { vi: "Cấp bậc", en: "Level" },
  skills: { vi: "Kỹ năng / tóm tắt hồ sơ", en: "Skills / profile summary" },
};

export function skillList(text) {
  return String(text || "").split(/[,;\n]/).map(item => item.trim()).filter(Boolean).slice(0, 300);
}
