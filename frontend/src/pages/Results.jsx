import React, { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Plus, ChevronRight, RefreshCw } from "lucide-react";
import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import { useApp } from "../context/AppContext";
import { useLanguage } from "../context/LanguageContext";
import { api, STATUS_LABEL } from "@interview/api";
import { useToast } from "@interview/toast";
import "../gateway.css";

export default function Results() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const selected = params.get("selected");
  const { sessions, sessionsLoading, sessionsError, refreshSessions } = useApp();
  const { language } = useLanguage();
  const vi = language === "vi";
  const [search, setSearch] = useState("");
  const [removeError, setRemoveError] = useState("");
  useToast(removeError, { id: "results-remove-error", tone: "error" });
  useEffect(() => { void refreshSessions(); }, [refreshSessions]);
  useEffect(() => {
    if (selected) navigate(`/interview/${encodeURIComponent(selected)}`, { replace: true });
  }, [selected, navigate]);
  const rows = sessions.filter(row => [row.candidate_name, row.target_role, row.session_id]
    .join(" ").toLowerCase().includes(search.toLowerCase()));

  async function remove(row) {
    const label = row.target_role || row.candidate_name || row.session_id;
    if (!window.confirm(vi ? `Xóa phiên "${label}"? Không thể hoàn tác.` : `Delete session "${label}"? This cannot be undone.`)) return;
    try {
      await api.remove(row.session_id);
      setRemoveError("");
      await refreshSessions();
    } catch (error) {
      setRemoveError(error instanceof Error ? error.message : String(error));
    }
  }

  return <main className="workspace-shell viewport-fit-shell">
    <div className="scenery" /><Sidebar />
    <section className="workspace-content viewport-fit-content viewport-fit-results-content">
      <Topbar search={search} onSearch={setSearch} />
      <section className="session-history glass">
        <header className="session-history-header">
          <div><h1>{vi ? "Lịch sử phỏng vấn" : "Interview history"}</h1>
            <p>{vi ? "Mở phiên để tiếp tục phỏng vấn hoặc xem báo cáo và hội thoại." : "Open a session to continue or review its report and transcript."}</p></div>
          <button className="btn btn-primary" onClick={() => navigate("/prepare-interview")}><Plus size={18} />{vi ? "Tạo phiên" : "New session"}</button>
          <button className="btn btn-soft" onClick={refreshSessions} disabled={sessionsLoading}><RefreshCw size={18} />{vi ? "Làm mới" : "Refresh"}</button>
        </header>
        {sessionsLoading && <p role="status">{vi ? "Đang tải…" : "Loading…"}</p>}
        {!sessionsLoading && !sessionsError && !rows.length && <p>{vi ? "Chưa có phiên phỏng vấn phù hợp." : "No matching interviews yet."}</p>}
        <div className="session-history-list">
          {rows.map(row => <div className="session-history-item" key={row.session_id}>
            <button className="session-history-row" onClick={() => navigate(`/interview/${encodeURIComponent(row.session_id)}`)}>
              <span><strong>{row.target_role || (vi ? "Phỏng vấn" : "Interview")}</strong><small>{row.candidate_name || row.session_id}</small></span>
              <span>{STATUS_LABEL[row.status] || row.status}<small>{row.updated_at || row.created_at ? new Date((row.updated_at || row.created_at) * 1000).toLocaleString(vi ? "vi-VN" : "en-US") : "—"}</small></span>
              <ChevronRight size={20} />
            </button>
            <button className="btn btn-soft session-delete" onClick={() => remove(row)} aria-label={vi ? "Xóa phiên" : "Delete session"}>{vi ? "Xóa" : "Delete"}</button>
          </div>)}
        </div>
      </section>
    </section>
  </main>;
}
