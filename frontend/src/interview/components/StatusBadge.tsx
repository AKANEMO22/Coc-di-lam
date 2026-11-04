import { STATUS_LABEL } from "../api";

const tone = (status?: string) =>
  status === "STAGE_ACTIVE" || status === "REPORT_READY" ? "ok"
    : status && ["FAILED", "ASSESSMENT_FAILED", "CANCELLED", "EXPIRED"].includes(status) ? "bad"
      : "wait";

export function StatusBadge({ status }: { status?: string }) {
  return <span className={`badge ${tone(status)}`}>{STATUS_LABEL[status ?? ""] ?? status ?? "—"}</span>;
}
