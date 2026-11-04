// REST calls to the gateway (backend). The gateway talks to the core AI and the speech service.
import { clearSession, getToken } from "./auth";


export type Status =
  | "INITIALIZED" | "PROFILING" | "READY" | "STAGE_ACTIVE" | "STAGE_TRANSITIONING" | "DIALOGUE_COMPLETED"
  | "ASSESSING" | "SYNTHESIZING" | "ENDING" | "REPORT_READY" | "CANCELLED" | "FAILED" | "ASSESSMENT_FAILED"
  | "EXPIRED" | "ARCHIVED";

export const TERMINAL: ReadonlySet<string> = new Set(["REPORT_READY", "CANCELLED", "FAILED", "EXPIRED", "ASSESSMENT_FAILED", "ARCHIVED"]);

export const STATUS_LABEL: Record<string, string> = {
  INITIALIZED: "Đã khởi tạo", PROFILING: "Đang phân tích hồ sơ", READY: "Sẵn sàng", STAGE_ACTIVE: "Đang phỏng vấn",
  STAGE_TRANSITIONING: "Đang chuyển phần", DIALOGUE_COMPLETED: "Đã xong hội thoại", ASSESSING: "Đang đánh giá",
  SYNTHESIZING: "Đang tổng hợp", ENDING: "Đang kết thúc", REPORT_READY: "Có báo cáo", CANCELLED: "Đã hủy",
  FAILED: "Lỗi", ASSESSMENT_FAILED: "Đánh giá lỗi", EXPIRED: "Hết hạn", ARCHIVED: "Đã lưu trữ",
};

/** Delivery metrics from the speech service (app/fluency.py); descriptive, never a score. */
export interface Fluency {
  speech_duration_ms?: number;
  audio_duration_ms?: number;
  pause_count?: number;
  long_pause_count?: number;
  total_silence_ms?: number;
  longest_pause_ms?: number;
  leading_silence_ms?: number;
  words_per_minute?: number;
  word_count?: number;
  filler_count?: number;
  fillers?: Record<string, number>;
  fillers_per_100_words?: number;
  repetition_count?: number;
  hesitation_level?: "low" | "medium" | "high";
}

export interface Message {
  turn: number;
  role: "candidate" | "interviewer";
  text: string;
  timestamp?: number;
  fluency?: Fluency;
  live?: boolean;
}

export interface SessionView {
  session_id: string;
  session: { status?: Status; current_turn?: number; current_stage_id?: string; failure_code?: string;
             failure_message?: string; updated_at?: number; created_at?: number;
             metadata?: { profiling_step?: string; profiling_step_at?: number; failure_code?: string;
                          failure_message?: string; [key: string]: unknown } };
  form: Record<string, any> | null;
  brief: Record<string, any> | null;
  candidate_profile: Record<string, any> | null;
  messages: Message[];
  last_sequence: number;
  execution_origin?: string;
}

export interface RunRow {
  session_id: string;
  candidate_name?: string;
  target_role?: string;
  status?: Status;
  updated_at?: number;
  created_at?: number;
}

export interface Report {
  session_id: string;
  status: Status;
  report_markdown?: string | null;
  final_assessment?: Record<string, any> | null;
  decision?: Record<string, any> | null;
  verdict?: string | null;
  message?: string | null;
}

export interface UploadedDocument {
  filename: string;
  extracted_text: string;
  extracted_characters: number;
  truncated: boolean;
}

/** Pre-session reading of a CV (backend app/cv_check.py); `readable: false` means it needs OCR. */
export interface CvAnalysis {
  readable: boolean;
  reason?: string;
  characters: number;
  fields: { candidate_name?: string | null; email?: string | null; phone?: string | null;
            current_role?: string | null; experience_years?: number | null; seniority?: string | null;
            skills?: string[] };
  sections: Record<string, boolean>;
  warnings: string[];
}

export interface StudentProfile {
  name: string;
  email?: string | null;
  major?: string | null;
  school_year?: string | null;
}

export interface LoginResult {
  access_token: string;
  expires_in: number;
  student_id: string;
  profile?: StudentProfile | null;
}

export interface LoginBody {
  student_id: string;
  access_code?: string;
  name?: string;
  email?: string;
  major?: string;
  school_year?: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(path, { ...init, headers, cache: "no-store" });
  if (response.status === 401 && token) clearSession();
  let data;
  try { data = await response.json(); }
  catch { throw new ApiError(response.status, "Gateway không trả về JSON. Kiểm tra backend và cấu hình proxy /api."); }
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.detail ? JSON.stringify(data.detail) : response.statusText;
    throw new ApiError(response.status, `${response.status}: ${detail}`);
  }
  return data as T;
}

export const api = {
  authStatus: () => request<{ auth_required: boolean; access_code_required: boolean; mode?: "off" | "open" | "student" }>("/api/auth/status"),
  checkStudent: (body: { student_id: string; access_code?: string }) =>
    request<{ student_id: string; exists: boolean; profile: StudentProfile | null }>("/api/auth/check", { method: "POST", body: JSON.stringify(body) }),
  login: (body: LoginBody) =>
    request<LoginResult>("/api/auth/login", { method: "POST", body: JSON.stringify(body) }),
  remove: (id: string) => request(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  health: () => request<{ backend: string; core: { ok: boolean }; speech: { ok: boolean; body?: any } }>("/api/health"),
  config: () => request<{ core: { real_provider?: boolean; provider_model?: string }; tts_enabled: boolean }>("/api/config"),
  sessions: (q = "", limit = 50, offset = 0) => request<{ runs: RunRow[]; total: number }>(`/api/sessions?limit=${limit}&offset=${offset}&q=${encodeURIComponent(q)}`),
  session: (id: string) => request<SessionView>(`/api/sessions/${encodeURIComponent(id)}`),
  create: (form: FormData, idempotencyKey: string) =>
    request<{ session_id: string }>("/api/sessions", { method: "POST", body: form, headers: { "Idempotency-Key": idempotencyKey } }),
  updatePlan: (id: string, plan: Record<string, unknown>) =>
    request(`/api/sessions/${encodeURIComponent(id)}/plan`, { method: "PATCH", body: JSON.stringify(plan) }),
  updateProfile: (id: string, profile: Record<string, unknown>) =>
    request(`/api/sessions/${encodeURIComponent(id)}/profile`, { method: "PATCH", body: JSON.stringify({ candidate_profile: profile }) }),
  uploadDocument: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadedDocument>(`/api/sessions/${encodeURIComponent(id)}/documents`, { method: "POST", body: form });
  },
  analyzeCv: (file: Blob, filename: string) => {
    const form = new FormData();
    form.append("file", file, filename);
    return request<CvAnalysis>("/api/cv/analyze", { method: "POST", body: form });
  },
  start: (id: string) => request(`/api/sessions/${encodeURIComponent(id)}/start`, { method: "POST" }),
  end: (id: string) => request(`/api/sessions/${encodeURIComponent(id)}/end`, { method: "POST" }),
  cancel: (id: string) => request(`/api/sessions/${encodeURIComponent(id)}/cancel`, { method: "POST" }),
  report: (id: string) => request<Report>(`/api/sessions/${encodeURIComponent(id)}/report`),
};
