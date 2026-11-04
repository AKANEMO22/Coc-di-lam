import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@interview/api";
import { AUTH_EVENT, clearSession, getStudent, getToken } from "@interview/auth";
import { useToast } from "@interview/toast";

const AppContext = createContext(null);

export function AppProvider({ children }) {
  const [user, setUser] = useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem("virtual-user") || "null");
      if (saved) return saved.signed && !getToken() ? null : saved;
      // A new tab: the sign-in token outlives the tab, so restore the account from it.
      const student = getToken() ? getStudent() : null;
      return student ? { studentId: student.studentId, email: student.studentId, name: student.name || student.studentId, signed: true } : null;
    } catch { return null; }
  });
  const [sessions, setSessions] = useState([]);
  const [sessionsError, setSessionsError] = useState("");
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const generation = useRef(0);

  const lastRows = useRef("");
  const refreshSessions = useCallback(async () => {
    // Hidden tabs skip the 15s polls, but never the first load.
    if (!user || (lastRows.current && document.visibilityState === "hidden")) return;
    const current = ++generation.current;
    // Only the first load shows "loading": background polls must not make the page jump.
    if (!lastRows.current) setSessionsLoading(true);
    try {
      const rows = [];
      for (let offset = 0; ; offset += 250) {
        const page = await api.sessions("", 250, offset);
        if (generation.current !== current) return;
        rows.push(...page.runs);
        if (page.runs.length < 250 || rows.length >= page.total) break;
      }
      // Polling every 15s: only re-render the pages when something actually changed.
      const print = JSON.stringify(rows);
      if (print !== lastRows.current) { lastRows.current = print; setSessions(rows); }
      setSessionsError("");
    } catch (error) {
      if (generation.current === current) setSessionsError(error.message);
    } finally {
      if (generation.current === current) setSessionsLoading(false);
    }
  }, [user]);

  useEffect(() => {
    if (user) sessionStorage.setItem("virtual-user", JSON.stringify(user));
    else sessionStorage.removeItem("virtual-user");
    localStorage.removeItem("virtual-user");
    void refreshSessions();
    const timer = window.setInterval(refreshSessions, 15000);
    const onVisible = () => { if (document.visibilityState === "visible") void refreshSessions(); };
    window.addEventListener("focus", refreshSessions);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      generation.current++;
      window.clearInterval(timer);
      window.removeEventListener("focus", refreshSessions);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [user, refreshSessions]);

  useEffect(() => {
    const onAuth = () => setUser(current => (current?.signed && !getToken() ? null : current));
    window.addEventListener(AUTH_EVENT, onAuth);
    return () => window.removeEventListener(AUTH_EVENT, onAuth);
  }, []);

  /** account: { studentId, name, email?, major?, school_year?, signed } */
  const login = useCallback(account => {
    const studentId = String(account?.studentId || "").trim();
    if (!studentId) return;
    const name = String(account.name || "").trim() || studentId;
    setUser({ ...account, studentId, name, email: account.email || studentId, signed: Boolean(account.signed) });
  }, []);
  const logout = useCallback(() => {
    clearSession();
    generation.current++;
    setUser(null);
    setSessions([]);
    lastRows.current = "";
    setSessionsError("");
    setSessionsLoading(false);
    sessionStorage.removeItem("virtual-user");
  }, []);

  useToast(user ? sessionsError : null, { id: "sessions-error", tone: "error", duration: 0,
                                         action: { label: "Thử lại", onClick: () => void refreshSessions() } });

  const results = useMemo(() => sessions.filter(row => row.status === "REPORT_READY").map(row => ({
    id: row.session_id,
    role: row.target_role,
    date: row.updated_at ? new Date(row.updated_at * 1000).toISOString() : null,
    startedAt: row.created_at ? new Date(row.created_at * 1000).toISOString() : null,
    status: "Completed",
  })), [sessions]);
  const value = useMemo(() => ({ user, login, logout, results, sessions, sessionsError, sessionsLoading, refreshSessions }),
    [user, login, logout, results, sessions, sessionsError, sessionsLoading, refreshSessions]);
  return <AppContext.Provider value={value}>
    {children}
  </AppContext.Provider>;
}

export function useApp() {
  const context = useContext(AppContext);
  if (!context) throw new Error("useApp must be used inside AppProvider.");
  return context;
}
