import React, { useEffect, useState, useMemo, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import {
  Briefcase,
  Compass,
  Search,
  Filter,
  RefreshCw,
  ExternalLink,
  MapPin,
  Building2,
  Calendar,
  Sparkles,
  CheckCircle2,
  X,
  Play,
  ArrowRight,
  FileText,
  Gift,
  ChevronDown,
  RotateCcw,
  Check,
  ChevronLeft,
  ChevronRight
} from "lucide-react";

import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import { useLanguage } from "../context/LanguageContext";
import { toast } from "@interview/toast";
import "../jobs.css";

const PLATFORMS = [
  { id: "all", label: "Tất cả nguồn" },
  { id: "ITviec", label: "ITviec" },
  { id: "TopCV", label: "TopCV" },
  { id: "TopDev", label: "TopDev" },
  { id: "VietnamWorks", label: "VietnamWorks" },
];

const SENIORITIES = [
  { id: "all", label: "Tất cả cấp bậc" },
  { id: "Intern", label: "Intern / Thực tập" },
  { id: "Fresher", label: "Fresher" },
  { id: "Junior", label: "Junior" },
  { id: "Middle", label: "Middle" },
  { id: "Senior", label: "Senior" },
  { id: "Lead", label: "Tech Lead / Manager" },
];

const CATEGORIES = [
  { id: "all", label: "Tất cả chuyên môn" },
  { id: "Backend", label: "Backend" },
  { id: "Frontend", label: "Frontend" },
  { id: "Fullstack", label: "Fullstack" },
  { id: "Mobile", label: "Mobile" },
  { id: "DevOps", label: "DevOps / Cloud" },
  { id: "AI/Data", label: "AI & Data" },
  { id: "QA/QC", label: "QA / Tester" },
  { id: "Security", label: "Security" },
];

const BOILERPLATE_REGEXES = [
  /^(\d+|[ivxlcdm]+)$/i,
  /^(vì sao bạn nên ứng tuyển|hồ sơ của bạn sẽ được|đối với các hồ sơ đáp ứng|ứng viên vui lòng kiểm tra)/i,
  /^(cập nhật ngay thông tin|ai yêu miền|mb careers|fanpage)/i,
  /^(yêu cầu ứng viên ứng tuyển cần cung cấp|thông tin cá nhân:|số điện thoại và email|trình độ học vấn)/i,
  /^(từ 3 - 5 kỹ năng nổi bật|kinh nghiệm làm việc$)/i,
];

function cleanJdText(raw) {
  if (!raw) return [];
  const text = String(raw).replace(/(trách nhiệm của bạn|trình độ của bạn|quyền lợi)(?=[A-ZÀ-Ỹ])/gi, "$1:\n");
  const rawLines = text.split("\n");
  const cleaned = [];

  for (const line of rawLines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (BOILERPLATE_REGEXES.some((rx) => rx.test(trimmed))) continue;
    cleaned.push(trimmed);
  }
  return cleaned;
}

function CleanJobSection({ title, content, icon: Icon }) {
  const lines = useMemo(() => cleanJdText(content), [content]);
  if (!lines.length) return null;

  return (
    <div className="job-modal-section">
      <div className="job-modal-section-title">
        {Icon && <Icon size={16} />}
        <h4>{title}</h4>
      </div>
      <div className="job-clean-content">
        {lines.map((line, idx) => {
          const isBullet = /^[-*+•]\s+/.test(line);
          const cleanText = line.replace(/^[-*+•]\s+/, "");
          const isSubheading =
            !isBullet &&
            (line.endsWith(":") ||
              /^(trách nhiệm|yêu cầu|quyền lợi|ưu tiên|điểm cộng|must have|good to have|\d+\.\s+[a-zà-ỹ0-9])/i.test(line));

          if (isSubheading) {
            return (
              <div key={idx} className="job-subheading-badge">
                {line}
              </div>
            );
          }

          return (
            <div key={idx} className={`job-line-item ${isBullet ? "bullet" : ""}`}>
              {isBullet && <span className="job-bullet-dot" />}
              <span>{cleanText}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const CACHE_KEY = "coc:cached-jobs-v1";

function loadCachedJobs() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return { jobs: [], total: 0 };
}

function CustomDropdown({ label, value, options, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    function handleClickOutside(event) {
      if (ref.current && !ref.current.contains(event.target)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      return () => document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [open]);

  const selectedOption = options.find((o) => o.id === value) || options[0];
  const isSelected = value !== "all";

  return (
    <div className="custom-dropdown-wrap" ref={ref}>
      <span className="custom-dropdown-label">{label}</span>
      <button
        type="button"
        className={`custom-dropdown-trigger ${isSelected ? "active" : ""} ${open ? "open" : ""}`}
        onClick={() => setOpen(!open)}
      >
        <span>{selectedOption?.label}</span>
        <ChevronDown size={14} className={`dropdown-chevron ${open ? "rotate" : ""}`} />
      </button>

      {open && (
        <div className="custom-dropdown-menu">
          {options.map((opt) => {
            const isItemActive = opt.id === value;
            return (
              <button
                key={opt.id}
                type="button"
                className={`custom-dropdown-item ${isItemActive ? "active" : ""}`}
                onClick={() => {
                  onChange(opt.id);
                  setOpen(false);
                }}
              >
                <span>{opt.label}</span>
                {isItemActive && <Check size={14} color="#2263ed" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

const PAGE_SIZE = 12;

function getPaginationRange(current, totalPages) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const pages = [];
  if (current <= 4) {
    for (let i = 1; i <= 5; i++) pages.push(i);
    pages.push("...");
    pages.push(totalPages);
  } else if (current >= totalPages - 3) {
    pages.push(1);
    pages.push("...");
    for (let i = totalPages - 4; i <= totalPages; i++) pages.push(i);
  } else {
    pages.push(1);
    pages.push("...");
    pages.push(current - 1);
    pages.push(current);
    pages.push(current + 1);
    pages.push("...");
    pages.push(totalPages);
  }
  return pages;
}

export default function Jobs() {
  const navigate = useNavigate();
  const { language } = useLanguage();
  const vi = language === "vi";

  const initialCache = useMemo(() => loadCachedJobs(), []);
  const [jobs, setJobs] = useState(initialCache.jobs);
  const [total, setTotal] = useState(initialCache.total);
  const [loading, setLoading] = useState(initialCache.jobs.length === 0);
  const [syncing, setSyncing] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState("all");
  const [selectedSeniority, setSelectedSeniority] = useState("all");
  const [page, setPage] = useState(1);
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [activeJob, setActiveJob] = useState(null);
  const jobsListRef = useRef(null);

  const showToast = useCallback((msg, tone = "info") => {
    toast({ id: "jobs-sync", message: msg, tone });
  }, []);

  const fetchJobs = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (search.trim()) params.set("query", search.trim());
      if (selectedPlatform !== "all") params.set("source", selectedPlatform);
      if (selectedSeniority !== "all") params.set("seniority", selectedSeniority);
      if (selectedCategory !== "all") params.set("job_category", selectedCategory);
      params.set("page", String(page));
      params.set("page_size", String(PAGE_SIZE));

      const res = await fetch(`/api/jobs?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP error: ${res.status}`);
      const data = await res.json();
      const loadedJobs = data.jobs || [];
      const loadedTotal = data.total || (data.jobs ? data.jobs.length : 0);
      setJobs(loadedJobs);
      setTotal(loadedTotal);
      if (!search && selectedPlatform === "all" && selectedSeniority === "all" && selectedCategory === "all") {
        try {
          sessionStorage.setItem(CACHE_KEY, JSON.stringify({ jobs: loadedJobs, total: loadedTotal }));
        } catch {}
      }
    } catch (err) {
      console.warn("Could not fetch jobs from API, falling back to cached list:", err);
    } finally {
      setLoading(false);
    }
  }, [page, search, selectedPlatform, selectedSeniority, selectedCategory]);

  const triggerBrowserbaseSync = useCallback(async (isInitial = false) => {
    setSyncing(true);
    if (!isInitial) {
      showToast(vi ? "Đang làm mới danh sách việc làm..." : "Refreshing job listings...");
    }
    try {
      const res = await fetch("/api/job-sync", { method: "POST" });
      if (res.ok) {
        const results = await res.json();
        const totalNew = Array.isArray(results)
          ? results.reduce((acc, r) => acc + (r.new_jobs || 0), 0)
          : (results.new_jobs || 0);
        await fetchJobs();
        showToast(
          vi
            ? (totalNew > 0 ? `Đã làm mới danh sách việc làm! (+${totalNew} việc làm mới)` : "Đã làm mới danh sách việc làm!")
            : (totalNew > 0 ? `Refreshed job listings! (+${totalNew} new jobs)` : "Refreshed job listings!"),
          "success"
        );
      } else if (!isInitial && res.status === 403) {
        showToast(vi ? "Chỉ quản trị viên được làm mới danh sách việc làm." : "Only administrators can refresh job listings.", "warn");
      }
    } catch (err) {
      console.warn("Sync error:", err);
      if (!isInitial) {
        showToast(vi ? "Lỗi khi làm mới hoặc dịch vụ chưa sẵn sàng." : "Refresh error or service offline.", "error");
      }
    } finally {
      setSyncing(false);
    }
  }, [fetchJobs, showToast, vi]);

  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  function handlePracticeJob(job) {
    const cleanDesc = cleanJdText(job.description).join("\n");
    const cleanReq = cleanJdText(job.requirements).join("\n");
    const cleanBen = cleanJdText(job.benefits).join("\n");

    const fullJd = [
      `Vị trí: ${job.title}`,
      `Công ty: ${job.company}`,
      `Mức lương: ${job.salary || "Thỏa thuận"}`,
      `Địa điểm: ${job.location || "Việt Nam"}`,
      "",
      "MÔ TẢ CÔNG VIỆC:",
      cleanDesc,
      "",
      "YÊU CẦU ỨNG VIÊN:",
      cleanReq,
      cleanBen ? `\nQUYỀN LỢI:\n${cleanBen}` : "",
      "",
      `Kỹ năng yêu cầu: ${(job.skills || []).join(", ")}`
    ].filter(Boolean).join("\n");

    navigate("/prepare-interview", {
      state: {
        role: job.title,
        company: job.company,
        jobDescription: fullJd,
        seniority: job.seniority?.toLowerCase() || "",
        skills: job.skills || [],
        sourceJobId: job.id,
        sourceUrl: job.source_url
      }
    });
  }

  return (
    <main className="workspace-shell viewport-fit-shell viewport-fit-jobs">
      <div className="scenery" />
      <Sidebar />

      <section className="workspace-content viewport-fit-content viewport-fit-jobs-content">
        <Topbar search={search} onSearch={(q) => { setSearch(q); setPage(1); }} placeholder={vi ? "Tìm việc làm theo vị trí, công ty hoặc kỹ năng..." : "Search jobs by title, company, or skills..."} />

        <div className="jobs-page viewport-fit-jobs-page">
          <header className="jobs-heading">
            <div>
              <h1>
                <Compass size={30} color="#2263ed" />
                {vi ? "Khám phá việc làm IT" : "Explore IT Jobs"}
              </h1>
              <p>
                {vi
                  ? "Việc làm chuyên ngành IT & Software được cập nhật liên tục từ các nền tảng tuyển dụng hàng đầu."
                  : "IT & Software engineering jobs continuously updated from top hiring platforms."}
              </p>
            </div>

            <div className="jobs-header-actions">
              <div className="jobs-count-badge glass">
                <Briefcase size={16} color="#2263ed" />
                <span>{vi ? "Tổng tin tuyển dụng:" : "Total jobs:"} <strong>{total}</strong></span>
              </div>

              <button
                className="btn btn-primary"
                onClick={() => triggerBrowserbaseSync(false)}
                disabled={syncing}
                title={vi ? "Làm mới danh sách việc làm" : "Refresh jobs"}
              >
                <RefreshCw size={17} className={syncing ? "animate-spin" : ""} />
                {syncing
                  ? (vi ? "Đang làm mới..." : "Refreshing...")
                  : (vi ? "Làm mới" : "Refresh")}
              </button>
            </div>
          </header>

          {/* Compact Custom Dropdown Filters Bar */}
          <div className="jobs-filters-compact glass">
            <div className="filter-intro">
              <Filter size={16} color="#2263ed" />
              <span>{vi ? "Bộ lọc:" : "Filter:"}</span>
            </div>

            <CustomDropdown
              label={vi ? "Nguồn:" : "Source:"}
              value={selectedPlatform}
              options={PLATFORMS}
              onChange={(p) => { setSelectedPlatform(p); setPage(1); }}
            />

            <CustomDropdown
              label={vi ? "Cấp bậc:" : "Seniority:"}
              value={selectedSeniority}
              options={SENIORITIES}
              onChange={(s) => { setSelectedSeniority(s); setPage(1); }}
            />

            <CustomDropdown
              label={vi ? "Chuyên môn:" : "Category:"}
              value={selectedCategory}
              options={CATEGORIES}
              onChange={(c) => { setSelectedCategory(c); setPage(1); }}
            />

            {(selectedPlatform !== "all" || selectedSeniority !== "all" || selectedCategory !== "all") && (
              <button
                className="filter-reset-btn"
                onClick={() => {
                  setSelectedPlatform("all");
                  setSelectedSeniority("all");
                  setSelectedCategory("all");
                  setPage(1);
                }}
                title={vi ? "Đặt lại tất cả bộ lọc" : "Reset all filters"}
              >
                <RotateCcw size={13} />
                <span>{vi ? "Đặt lại" : "Reset"}</span>
              </button>
            )}
          </div>

          <div className="jobs-results-region">
          <div className="jobs-list-viewport" ref={jobsListRef}>
          {/* Jobs Grid */}
          {loading && !jobs.length ? (
            <div className="jobs-empty glass">
              <RefreshCw size={36} className="animate-spin text-blue-500" />
              <h3>{vi ? "Đang tải danh sách việc làm IT..." : "Loading IT job listings..."}</h3>
              <p>{vi ? "Hệ thống đang nạp dữ liệu việc làm từ kho dữ liệu đồng bộ." : "Retrieving synced job data."}</p>
            </div>
          ) : !jobs.length ? (
            <div className="jobs-empty glass">
              <Briefcase size={42} color="#8397b5" />
              <h3>{vi ? "Chưa tìm thấy việc làm phù hợp" : "No matching jobs found"}</h3>
              <p>{vi ? "Hãy thử đổi từ khóa tìm kiếm hoặc bấm 'Làm mới' để tải thêm tin mới." : "Try a different search term or click 'Refresh' to load more."}</p>
              <button className="btn btn-primary" style={{ marginTop: 14 }} onClick={() => triggerBrowserbaseSync(false)}>
                <RefreshCw size={17} />
                {vi ? "Làm mới danh sách" : "Refresh Jobs"}
              </button>
            </div>
          ) : (
            <div className="jobs-grid">
              {jobs.map((job) => {
                return (
                  <article className="job-card glass" key={job.id}>
                    <div>
                      {job.seniority && (
                        <div className="job-card-top">
                          <span className="badge-seniority">{job.seniority}</span>
                        </div>
                      )}

                      <div className="job-card-body" style={{ marginTop: job.seniority ? 6 : 0 }}>
                        <h3 onClick={() => setActiveJob(job)} title={job.title}>
                          {job.title}
                        </h3>

                        <div className="job-company-name">
                          <Building2 size={15} />
                          <span>{job.company}</span>
                        </div>

                        <div className="job-meta-row">
                          <span className="job-meta-item salary">
                            💰 {job.salary || (vi ? "Thỏa thuận" : "Negotiable")}
                          </span>
                          <span className="job-meta-item">
                            <MapPin size={14} />
                            {job.location || "Việt Nam"}
                          </span>
                        </div>

                        {job.skills && job.skills.length > 0 && (
                          <div className="job-skills-list">
                            {job.skills.slice(0, 5).map((skill, i) => (
                              <span className="job-skill-chip" key={i}>
                                {skill}
                              </span>
                            ))}
                            {job.skills.length > 5 && (
                              <span className="job-skill-chip">+{job.skills.length - 5}</span>
                            )}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="job-card-actions">
                      <button
                        className="btn btn-soft btn-small"
                        onClick={() => setActiveJob(job)}
                      >
                        {vi ? "Xem JD" : "View JD"}
                      </button>
                      <button
                        className="btn btn-primary btn-small"
                        onClick={() => handlePracticeJob(job)}
                        title={vi ? "Tự động điền JD này vào phiên phỏng vấn" : "Practice interview with this job"}
                      >
                        {vi ? "Phỏng vấn" : "Practice"}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}

          </div>

          {/* Pagination Controls */}
          {Math.ceil(total / PAGE_SIZE) > 1 && (
            <div className="jobs-pagination glass">
              <button
                className="btn btn-soft btn-small pagination-btn"
                onClick={() => {
                  setPage((p) => Math.max(1, p - 1));
                  jobsListRef.current?.scrollTo({ top: 0, behavior: "smooth" });
                }}
                disabled={page <= 1}
              >
                <ChevronLeft size={16} />
                <span>{vi ? "Trang trước" : "Previous"}</span>
              </button>

              <div className="pagination-numbers">
                {getPaginationRange(page, Math.ceil(total / PAGE_SIZE)).map((pNum, idx) =>
                  pNum === "..." ? (
                    <span key={`dots-${idx}`} className="pagination-dots">…</span>
                  ) : (
                    <button
                      key={pNum}
                      className={`pagination-num ${page === pNum ? "active" : ""}`}
                      onClick={() => {
                        setPage(pNum);
                        jobsListRef.current?.scrollTo({ top: 0, behavior: "smooth" });
                      }}
                    >
                      {pNum}
                    </button>
                  )
                )}
              </div>

              <button
                className="btn btn-soft btn-small pagination-btn"
                onClick={() => {
                  setPage((p) => Math.min(Math.ceil(total / PAGE_SIZE), p + 1));
                  jobsListRef.current?.scrollTo({ top: 0, behavior: "smooth" });
                }}
                disabled={page >= Math.ceil(total / PAGE_SIZE)}
              >
                <span>{vi ? "Trang sau" : "Next"}</span>
                <ChevronRight size={16} />
              </button>
            </div>
          )}
          </div>
        </div>

        {/* Modal Chi tiết JD */}
        {activeJob && (
          <div className="job-modal-backdrop" onClick={() => setActiveJob(null)}>
            <div className="job-modal" onClick={(e) => e.stopPropagation()}>
              <div className="job-modal-header">
                <div>
                  <h2>{activeJob.title}</h2>
                  <div className="job-modal-meta">
                    <span style={{ color: "#2263ed" }}><Building2 size={16} /> {activeJob.company}</span>
                    <span>📍 {activeJob.location || "Việt Nam"}</span>
                    <span style={{ color: "#0c8a51" }}>💰 {activeJob.salary || "Thỏa thuận"}</span>
                  </div>
                </div>
                <button
                  className="btn btn-soft"
                  style={{ borderRadius: "50%", padding: 8, minWidth: 36, height: 36 }}
                  onClick={() => setActiveJob(null)}
                >
                  <X size={18} />
                </button>
              </div>

              <div className="job-modal-content">
                {activeJob.skills && activeJob.skills.length > 0 && (
                  <div className="job-modal-section">
                    <div className="job-modal-section-title">
                      <Sparkles size={16} />
                      <h4>{vi ? "Kỹ năng & Công nghệ yêu cầu" : "Required Skills & Technologies"}</h4>
                    </div>
                    <div className="job-skills-list">
                      {activeJob.skills.map((s, i) => (
                        <span className="job-skill-chip" key={i}>{s}</span>
                      ))}
                    </div>
                  </div>
                )}

                <CleanJobSection
                  title={vi ? "Mô tả công việc (JD)" : "Job Description"}
                  content={activeJob.description}
                  icon={FileText}
                />

                <CleanJobSection
                  title={vi ? "Yêu cầu ứng viên" : "Candidate Requirements"}
                  content={activeJob.requirements}
                  icon={CheckCircle2}
                />

                <CleanJobSection
                  title={vi ? "Quyền lợi & Đãi ngộ" : "Benefits"}
                  content={activeJob.benefits}
                  icon={Gift}
                />
              </div>

              <div className="job-modal-actions">
                {activeJob.source_url && (
                  <button
                    className="btn btn-soft"
                    onClick={() => window.open(activeJob.source_url, "_blank", "noopener,noreferrer")}
                  >
                    <ExternalLink size={16} />
                    {vi ? "Xem tin gốc tại nguồn" : "View Original Listing"}
                  </button>
                )}
                <button
                  className="btn btn-primary"
                  onClick={() => handlePracticeJob(activeJob)}
                >
                  {vi ? "Luyện phỏng vấn với JD này" : "Practice Interview with this JD"}
                </button>
              </div>
            </div>
          </div>
        )}

      </section>
    </main>
  );
}
