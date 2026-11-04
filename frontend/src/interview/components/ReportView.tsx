import { useEffect, useState } from "react";
import { BarChart3, CheckCircle2, ChevronRight, ClipboardList, Clock3, Download, House, MessageSquareText, Target, Trophy } from "lucide-react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { api, type Report } from "../api";

const VERDICT: Record<string, string> = { PASS: "Đạt", FAIL: "Chưa đạt", NEEDS_REVIEW: "Cần xem xét thêm" };
const VERDICT_LINE: Record<string, string> = {
  PASS: "Ứng viên đáp ứng yêu cầu của vị trí ở vòng phỏng vấn này.",
  FAIL: "Ứng viên chưa đáp ứng yêu cầu của vị trí ở vòng phỏng vấn này.",
  NEEDS_REVIEW: "Kết quả nằm sát ngưỡng hoặc chưa đủ bằng chứng; cần người đánh giá xác nhận.",
};

export function ReportView({ sessionId, candidateName, onBack }: { sessionId: string; candidateName?: string; onBack: () => void }) {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.report(sessionId).then(setReport).catch((e) => setError((e as Error).message));
  }, [sessionId]);

  if (error) return <p className="notice error">Không tải được báo cáo: {error}</p>;
  if (!report) return <p className="muted">Đang tải báo cáo…</p>;
  const verdict = report.verdict || report.decision?.verdict || report.final_assessment?.verdict;
  const assessment = report.final_assessment ?? {};
  const decision = report.decision ?? {};
  const narrative = assessment.narrative ?? {};
  const metrics = assessment.metrics ?? {};
  const score = typeof decision.weighted_score === "number" ? decision.weighted_score : metrics.weighted_score;
  const threshold = typeof decision.pass_threshold === "number" ? decision.pass_threshold : metrics.pass_threshold;
  const summary = narrative.summary;
  const earlyEnded = assessment.early_ended === true;
  const criteria = (decision.criteria ?? assessment.criteria ?? []) as Array<Record<string, any>>;
  const findings = (narrative.criterion_findings ?? []) as Array<Record<string, any>>;
  const strengthsSource = assessment.demonstrated_strengths ?? narrative.strengths;
  const improvementsSource = assessment.improvement_areas ?? narrative.improvements;
  const strengths = Array.isArray(strengthsSource) ? strengthsSource : [];
  const improvements = Array.isArray(improvementsSource) ? improvementsSource : [];
  const displayVerdict = verdict ? (VERDICT[verdict] ?? verdict) : "Đang cập nhật";
  const scoreLabel = typeof score === "number" ? `${score.toFixed(2).replace(/0+$/, "").replace(/\.$/, "")}/4` : "n/a / 4";
  const html = report.report_markdown ? DOMPurify.sanitize(marked.parse(report.report_markdown, { async: false }) as string) : "";

  const download = () => {
    const blob = new Blob([report.report_markdown ?? JSON.stringify(report, null, 2)], { type: "text/markdown;charset=utf-8" });
    const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: `report_${sessionId}.md` });
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  };

  return (
    <section className="report" aria-labelledby="report-title">
      <nav className="report-breadcrumb" aria-label="Đường dẫn">
        <button type="button" onClick={onBack}><House size={17} aria-hidden="true" /><span>Danh sách</span></button>
        <ChevronRight size={16} aria-hidden="true" />
        <span>{candidateName || sessionId}</span>
        <ChevronRight size={16} aria-hidden="true" />
        <span aria-current="page">Báo cáo phỏng vấn</span>
      </nav>

      <div className="report-surface">
        <header className="report-heading">
          <span className="report-heading-icon"><BarChart3 size={27} aria-hidden="true" /></span>
          <div>
            <h1 id="report-title">Báo cáo phỏng vấn</h1>
            <p>Tổng hợp đánh giá buổi phỏng vấn của bạn với AI</p>
          </div>
          <button className="report-download" type="button" onClick={download}><Download size={18} aria-hidden="true" />Tải xuống báo cáo</button>
        </header>

        <div className={`report-verdict verdict-${verdict || "NEEDS_REVIEW"}`} role="status">
          <span className="report-trophy"><Trophy size={27} aria-hidden="true" /></span>
          <div className="report-verdict-copy">
            <strong>Kết luận: {displayVerdict.toUpperCase()}</strong>
            <p>{VERDICT_LINE[verdict ?? ""] ?? "Báo cáo đã được tổng hợp từ nội dung phiên phỏng vấn."}</p>
            {summary && <p className="report-verdict-summary">{summary}</p>}
          </div>
          <img src="/images/coc-logo-transparent.png" alt="Cóc Đi Làm đang đọc sách" />
        </div>

        {earlyEnded && <div className="report-early-note"><Clock3 size={19} aria-hidden="true" /><span>Phiên kết thúc sớm: báo cáo chỉ dựa trên các câu trả lời đã có.</span></div>}

        <div className="report-columns">
          <section className="report-card report-overview" aria-labelledby="report-overview-title">
            <div className="report-card-heading"><ClipboardList size={25} aria-hidden="true" /><h2 id="report-overview-title">{earlyEnded ? "Phiên kết thúc sớm" : "Tổng quan buổi phỏng vấn"}</h2></div>
            <p className="report-card-intro">{earlyEnded ? "Báo cáo này chỉ dựa trên bằng chứng đã lưu. Các đánh giá còn dở đã bị dừng; cần người duyệt xác nhận." : "Các nhận xét dưới đây được tổng hợp từ câu trả lời và tiêu chí của buổi phỏng vấn."}</p>

            <div className="report-timeline">
              <article className="report-timeline-item">
                <span className="report-step-number">1</span>
                <div>
                  <h3>Kết quả</h3>
                  <p><strong>Kết luận:</strong> {displayVerdict}</p>
                  <p><strong>Điểm có trọng số:</strong> {scoreLabel}{typeof threshold === "number" ? ` (ngưỡng đạt ${threshold}, vùng ranh giới ±${decision.borderline_band ?? 0.25})` : ""}</p>
                  {decision.requires_human_review && <p className="report-review-hint"><CheckCircle2 size={15} aria-hidden="true" />Kết quả cần người duyệt xác nhận.</p>}
                </div>
              </article>
              <article className="report-timeline-item">
                <span className="report-step-number">2</span>
                <div>
                  <h3>Tóm tắt</h3>
                  <p>{summary || report.message || "Chưa có phần tóm tắt cho phiên này."}</p>
                </div>
              </article>
            </div>
          </section>

          <section className="report-card report-evidence" aria-labelledby="report-criteria-title">
            <div className="report-card-heading"><Target size={25} aria-hidden="true" /><h2 id="report-criteria-title">Tiêu chí đánh giá</h2></div>
            {criteria.length > 0 ? (
              <div className="report-table-wrap">
                <table className="report-criteria-table">
                  <thead><tr><th>Tiêu chí</th><th>Trọng số</th><th>Bắt buộc</th><th>Mức (1–4)</th><th>Độ tin cậy bằng chứng</th><th>Đạt mức</th></tr></thead>
                  <tbody>{criteria.map((criterion, index) => (
                    <tr key={criterion.competency_id ?? index}>
                      <td>{criterion.name ?? criterion.competency_id ?? "Tiêu chí"}</td>
                      <td>{typeof criterion.weight === "number" ? criterion.weight.toFixed(1) : criterion.weight ?? "—"}</td>
                      <td>{criterion.must_have ? "Có" : "Không"}</td>
                      <td>{criterion.covered === false ? "Chưa đủ bằng chứng" : typeof criterion.level === "number" ? criterion.level.toFixed(1) : "—"}</td>
                      <td>{typeof criterion.confidence === "number" ? criterion.confidence.toFixed(1) : "—"}</td>
                      <td><span className={`report-result-pill ${criterion.meets_bar ? "is-met" : "is-missed"}`}>{criterion.meets_bar ? "Có" : "Không"}</span></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <p className="report-empty">Báo cáo chưa có dữ liệu tiêu chí.</p>}

            <div className="report-findings">
              <div className="report-card-heading report-subheading"><MessageSquareText size={22} aria-hidden="true" /><h2>Nhận xét theo tiêu chí</h2></div>
              {criteria.length > 0 ? criteria.map((criterion, index) => {
                const finding = findings.find((item) => item.competency_id === criterion.competency_id);
                const quotes = Array.isArray(finding?.quotes) ? finding.quotes : [];
                return <article className="report-finding" key={criterion.competency_id ?? index}>
                  <h3>{criterion.name ?? criterion.competency_id ?? "Tiêu chí"}</h3>
                  <p>{finding?.finding || (criterion.covered === false ? "Chưa có đủ bằng chứng để nhận xét tiêu chí này." : "Chưa có nhận xét chi tiết cho tiêu chí này.")}</p>
                  {quotes.slice(0, 2).map((quote: string, quoteIndex: number) => <blockquote key={quoteIndex}>“{quote}”</blockquote>)}
                </article>;
              }) : <p className="report-empty">Chưa có nhận xét theo tiêu chí.</p>}
            </div>
          </section>
        </div>

        {(strengths.length > 0 || improvements.length > 0) && <div className="report-advice-grid">
          {strengths.length > 0 && <section className="report-advice-card"><h2>Điểm mạnh</h2><ul>{strengths.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>}
          {improvements.length > 0 && <section className="report-advice-card"><h2>Điểm cần cải thiện</h2><ul>{improvements.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>}
        </div>}

        {assessment.disclaimer && <p className="report-disclaimer">{assessment.disclaimer}</p>}
        {html && <details className="report-full-markdown"><summary>Xem báo cáo đầy đủ</summary><div className="markdown" dangerouslySetInnerHTML={{ __html: html }} /></details>}
      </div>
    </section>
  );
}
