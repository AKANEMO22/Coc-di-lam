
import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FileText, UploadCloud, Trash2, Eye,
  Video, Plus, FolderOpen, ArrowRight, AlertTriangle, CheckCircle2, ScanText, Pencil
} from "lucide-react";

import Sidebar from "../components/Sidebar";
import Topbar from "../components/Topbar";
import { cvStorage } from "../utils/cvStorage";
import { useLanguage } from "../context/LanguageContext";
import CVPreviewModal from "../components/CVPreviewModal";
import CVDetailsDialog from "../components/CVDetailsDialog";
import { api } from "@interview/api";
import { useToast } from "@interview/toast";
import { interviewDetails, missingDetails, needsOcr } from "../utils/cvProfile";

export default function CVLibrary() {
  const navigate = useNavigate();
  const { language } = useLanguage();
  const vi = language === "vi";

  const [cvs, setCvs] = useState([]);
  const [role, setRole] = useState("");
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  useToast(error, { id: "cv-library-error", tone: "error" });
  const [previewFile, setPreviewFile] = useState(null);
  const [editing, setEditing] = useState(null);
  const [savingDetails, setSavingDetails] = useState(false);
  const previewUrl = useRef(null);
  const previewRequest = useRef(0);

  async function refresh() {
    try {
      setCvs(await cvStorage.getAll());
      setError("");
    } catch {
      setError(vi
        ? "Không thể đọc kho CV."
        : "Unable to load CV library.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => () => {
    previewRequest.current += 1;
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
  }, []);

  async function upload(e) {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    setError("");

    try {
      const cv = await cvStorage.add(file, role);
      setRole("");
      await refresh();
      // Read the CV right away so missing details (or a scan that needs OCR) show on its card.
      try {
        const analysis = await api.analyzeCv(file, file.name);
        const updated = await cvStorage.update(cv.id, { analysis });
        await refresh();
        if (analysis.readable === false || missingDetails(updated, interviewDetails(updated)).length) setEditing(updated);
      } catch {
        // Backend unavailable: the check runs again when the CV is picked for an interview.
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  }

  async function remove(id) {
    const confirmed = window.confirm(
      vi ? "Bạn muốn xóa CV này?" : "Delete this CV?"
    );
    if (!confirmed) return;

    try {
      await cvStorage.remove(id);
      await refresh();
    } catch {
      setError(vi ? "Không thể xóa CV." : "Cannot delete CV.");
    }
  }

  async function preview(cv) {
    const requestId = ++previewRequest.current;
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    setPreviewFile({ cv, url: null });
    try {
      const stored = await cvStorage.get(cv.id);
      if (!stored?.file) throw new Error("CV file missing");
      if (requestId !== previewRequest.current) return;

      const url = URL.createObjectURL(stored.file);
      previewUrl.current = url;
      setPreviewFile({ cv, url });
    } catch {
      if (requestId !== previewRequest.current) return;
      setPreviewFile(null);
      setError(vi
        ? "Không thể mở CV."
        : "Unable to open CV.");
    }
  }

  function closePreview() {
    previewRequest.current += 1;
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = null;
    setPreviewFile(null);
  }

  async function saveDetails(next) {
    setSavingDetails(true);
    try {
      await cvStorage.update(editing.id, {
        details: { ...next, experienceYears: next.experienceYears === "" ? "" : Number(next.experienceYears) },
        role: next.role.trim() || editing.role || "",
      });
      setEditing(null);
      await refresh();
    } catch {
      setError(vi ? "Không thể lưu thông tin CV." : "Cannot save CV details.");
    } finally {
      setSavingDetails(false);
    }
  }

  function cvStatus(cv) {
    const missing = missingDetails(cv, interviewDetails(cv));
    if (needsOcr(cv) && missing.length) return { tone: "warn", Icon: ScanText, text: vi ? "CV dạng ảnh – cần bổ sung" : "Scanned – add details" };
    if (missing.length) return { tone: "warn", Icon: AlertTriangle, text: vi ? `Thiếu ${missing.length} thông tin` : `${missing.length} details missing` };
    return { tone: "ok", Icon: CheckCircle2, text: vi ? "Sẵn sàng phỏng vấn" : "Ready to interview" };
  }

  function useCv(cv) {
    closePreview();
    navigate("/prepare-interview?cv=" + encodeURIComponent(cv.id));
  }

  return (
    <main className="workspace-shell cv-library-shell">
      <div className="scenery" />
      <Sidebar />

      <section className="workspace-content cv-library-content">
        <Topbar />

        <div className="cv-page">
          <div className="cv-heading">
            <div>
              <h1>
                <FolderOpen size={30} />
                {vi ? "Kho CV của tôi" : "My CV Library"}
              </h1>
              <p>
                {vi
                  ? "Quản lý hồ sơ cho từng vị trí ứng tuyển."
                  : "Manage resumes for different job positions."}
              </p>
            </div>

            <button
              className="btn btn-primary"
              onClick={() => navigate("/prepare-interview")}
            >
              <Video size={18} />
              {vi ? "Bắt đầu phỏng vấn" : "Start Interview"}
            </button>
          </div>

          <div className="cv-upload glass">
            <UploadCloud size={36} />
            <h2>{vi ? "Tải CV mới" : "Upload New CV"}</h2>
            <p>PDF, DOCX · Max 10MB</p>

            <input
              className="cv-role-input"
              value={role}
              onChange={e => setRole(e.target.value)}
              placeholder={
                vi
                  ? "Vị trí CV: AI Intern, Marketing..."
                  : "CV role: AI Intern, Marketing..."
              }
            />

            <label className="btn btn-primary cv-upload-button">
              <Plus size={18} />
              {uploading
                ? (vi ? "Đang lưu..." : "Saving...")
                : (vi ? "Chọn file CV" : "Choose CV File")}

              <input
                type="file"
                accept=".pdf,.docx"
                onChange={upload}
                disabled={uploading}
                hidden
              />
            </label>
          </div>


          <div className="cv-list-heading">
            <h2>{vi ? "CV đã lưu" : "Saved CVs"} ({cvs.length})</h2>
          </div>

          <div className="cv-list-scroll">
          {loading ? (
            <p>{vi ? "Đang tải..." : "Loading..."}</p>
          ) : cvs.length === 0 ? (
            <div className="cv-empty glass">
              <FolderOpen size={46} />
              <h3>{vi ? "Chưa có CV nào" : "No CVs yet"}</h3>
              <p>
                {vi
                  ? "Hãy tải CV đầu tiên để bắt đầu phỏng vấn."
                  : "Upload your first CV to begin interviewing."}
              </p>
            </div>
          ) : (
            <div className="cv-grid">
              {cvs.map(cv => (
                <article className="cv-item glass" key={cv.id}>
                  <div className="cv-file-icon">
                    <FileText size={32} />
                  </div>

                  <h3 title={cv.name}>{cv.name}</h3>
                  <p>{cv.role || (vi ? "Chưa phân loại" : "Uncategorized")}</p>
                  {(() => { const { tone, Icon, text } = cvStatus(cv); return (
                    <span className={`cv-status-chip ${tone}`}><Icon size={13} /> {text}</span>
                  ); })()}

                  <small>
                    {(cv.size / 1024 / 1024).toFixed(2)} MB
                    {" · "}
                    {new Date(cv.createdAt).toLocaleDateString(
                      vi ? "vi-VN" : "en-US"
                    )}
                  </small>

                  <div className="cv-item-actions">
                    <button
                      className="btn btn-soft"
                      onClick={() => preview(cv)}
                    >
                      <Eye size={16} />
                      {vi ? "Xem" : "View"}
                    </button>

                    <button
                      className="btn btn-soft"
                      onClick={() => setEditing(cv)}
                      title={vi ? "Thông tin phỏng vấn" : "Interview details"}
                      aria-label={vi ? "Thông tin phỏng vấn" : "Interview details"}
                    >
                      <Pencil size={16} />
                    </button>

                    <button
                      className="btn btn-soft"
                      onClick={() => remove(cv.id)}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>

                  <button
                    className="btn btn-primary"
                    onClick={() => useCv(cv)}
                  >
                    {vi ? "Dùng CV này" : "Use This CV"}
                    <ArrowRight size={16} />
                  </button>
                </article>
              ))}
            </div>
          )}
          </div>
        </div>
      </section>
      {previewFile && (
        <CVPreviewModal
          cv={previewFile.cv}
          fileUrl={previewFile.url}
          vi={vi}
          onClose={closePreview}
          onUse={() => useCv(previewFile.cv)}
        />
      )}
      {editing && (
        <CVDetailsDialog
          cv={editing}
          initial={interviewDetails(editing)}
          vi={vi}
          mode="edit"
          saving={savingDetails}
          onCancel={() => setEditing(null)}
          onSave={saveDetails}
        />
      )}
    </main>
  );
}
