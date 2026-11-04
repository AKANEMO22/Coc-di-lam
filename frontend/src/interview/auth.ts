const KEY = "interview:auth";
export const AUTH_EVENT = "interview:auth-changed";

export interface Student {
  studentId: string;
  name: string;
}

interface Stored extends Student {
  token: string;
  expiresAt: number;
}

let memory: Stored | null = null;

function load(): Stored | null {
  if (!memory) {
    try { memory = JSON.parse(localStorage.getItem(KEY) || "null"); } catch { memory = null; }
  }
  if (memory && memory.expiresAt <= Date.now()) memory = null;
  return memory;
}

export const getToken = (): string | null => load()?.token ?? null;

export function getStudent(): Student | null {
  const stored = load();
  return stored ? { studentId: stored.studentId, name: stored.name } : null;
}

export function saveLogin(login: { access_token: string; expires_in: number; student_id: string }, name: string): void {
  memory = { token: login.access_token, expiresAt: Date.now() + login.expires_in * 1000 - 30_000, studentId: login.student_id, name };
  try { localStorage.setItem(KEY, JSON.stringify(memory)); } catch { /* storage blocked: the token stays in memory */ }
  window.dispatchEvent(new Event(AUTH_EVENT));
}

export function clearSession(): void {
  memory = null;
  try { localStorage.removeItem(KEY); } catch { /* nothing stored */ }
  window.dispatchEvent(new Event(AUTH_EVENT));
}
