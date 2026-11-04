import { useEffect, useState } from "react";
import { api, STATUS_LABEL, type RunRow } from "../api";
import { StatusBadge } from "./StatusBadge";

const when = (seconds?: number) =>
  seconds ? new Date(seconds * 1000).toLocaleString("vi-VN", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "";

export function SessionList({ onOpen }: { onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<RunRow[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true);
      api.sessions(query)
        .then((data) => { setRows(data.runs); setError(null); })
        .catch((e) => setError((e as Error).message))
        .finally(() => setLoading(false));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  return (
    <aside className="card list">
      <h2>Phiên đã có</h2>
      <input type="search" placeholder="Tìm theo tên, vị trí…" value={query} onChange={(e) => setQuery(e.target.value)} />
      {error && <p className="notice error">{error}</p>}
      {!error && !loading && !rows.length && <p className="muted">Chưa có phiên nào.</p>}
      <ul>
        {rows.map((row) => (
          <li key={row.session_id}>
            <button type="button" onClick={() => onOpen(row.session_id)} title={STATUS_LABEL[row.status ?? ""] ?? row.status}>
              <strong>{row.candidate_name || row.session_id}</strong>
              <span className="muted">{row.target_role || "—"}</span>
              <span className="row-meta"><StatusBadge status={row.status} /><small>{when(row.updated_at || row.created_at)}</small></span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
