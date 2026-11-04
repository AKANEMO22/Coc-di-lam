import type { Fluency } from "../api";

const LEVEL = { low: "Lưu loát", medium: "Hơi ấp úng", high: "Ấp úng nhiều" } as const;
const seconds = (ms?: number) => `${((ms ?? 0) / 1000).toFixed(1)}s`;

/** Delivery metrics of one answer. Descriptive only: the core never scores them. */
export function FluencyChips({ fluency }: { fluency?: Fluency | null }) {
  if (!fluency || !fluency.speech_duration_ms) return null;
  const fillers = Object.entries(fluency.fillers ?? {}).map(([word, n]) => `${word} ×${n}`).join(", ");
  return (
    <div className="fluency">
      {fluency.hesitation_level && <span className={`chip level-${fluency.hesitation_level}`}>{LEVEL[fluency.hesitation_level]}</span>}
      <span className="chip" title={fillers || undefined}>Từ đệm: {fluency.filler_count ?? 0}</span>
      <span className="chip" title={`Ngắt dài (≥1s): ${fluency.long_pause_count ?? 0}`}>
        Ngắt quãng: {fluency.pause_count ?? 0}{fluency.longest_pause_ms ? ` · dài nhất ${seconds(fluency.longest_pause_ms)}` : ""}
      </span>
      {!!fluency.repetition_count && <span className="chip">Lặp từ: {fluency.repetition_count}</span>}
      {!!fluency.words_per_minute && <span className="chip">{fluency.words_per_minute} từ/phút</span>}
      <span className="chip" title="Thời gian nói thực (không tính khoảng lặng)">Nói {seconds(fluency.speech_duration_ms)}</span>
      {(fluency.leading_silence_ms ?? 0) >= 1000 && <span className="chip">Im lặng trước khi nói {seconds(fluency.leading_silence_ms)}</span>}
    </div>
  );
}
