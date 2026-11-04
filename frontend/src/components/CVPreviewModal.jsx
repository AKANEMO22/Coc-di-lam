import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  ChevronLeft, ChevronRight, Download, FileText, FolderOpen,
  House, Maximize2, Minus, Plus, Search, Video, X
} from "lucide-react";

export default function CVPreviewModal({ cv, fileUrl, vi, onClose, onUse }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchText, setSearchText] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const previewPane = useRef(null);
  const isPdf = cv.type === "pdf";
  const fileSize = (cv.size / 1024 / 1024).toFixed(2) + " MB";
  const uploaded = new Date(cv.createdAt).toLocaleDateString(vi ? "vi-VN" : "en-US");

  const documentUrl = useMemo(() => {
    if (!fileUrl || !isPdf) return "";
    const params = new URLSearchParams({ toolbar: "0", navpanes: "0", page: String(page), zoom: String(zoom) });
    if (appliedSearch) params.set("search", appliedSearch);
    return fileUrl + "#" + params.toString();
  }, [fileUrl, isPdf, page, zoom, appliedSearch]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = event => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  function applyPage(value) {
    setPage(Math.max(1, Number(value) || 1));
  }

  function downloadFile() {
    if (!fileUrl) return;
    const link = document.createElement("a");
    link.href = fileUrl;
    link.download = cv.name;
    link.click();
  }

  async function fullscreen() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await previewPane.current?.requestFullscreen();
    } catch {
      // Fullscreen is optional and may be blocked by the browser.
    }
  }

  return (
    <div className="cv-preview-overlay" onMouseDown={event => event.target === event.currentTarget && onClose()}>
      <section className="cv-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="cv-preview-title">
        <header className="cv-preview-header">
          <nav className="cv-preview-breadcrumb" aria-label={vi ? "Đường dẫn" : "Breadcrumb"}>
            <span className="cv-preview-home"><House size={17} /></span>
            <ChevronRight size={15} />
            <span>{vi ? "Kho CV" : "CV Library"}</span>
            <ChevronRight size={15} />
            <strong id="cv-preview-title">{vi ? "Xem CV" : "Resume preview"}</strong>
          </nav>
          <div className="cv-preview-header-actions">
            <button type="button" className="cv-preview-interview" onClick={onUse}>
              <Video size={17} />{vi ? "Dùng CV phỏng vấn" : "Use for interview"}
            </button>
            <button type="button" className="cv-preview-close" onClick={onClose} aria-label={vi ? "Đóng xem trước" : "Close preview"}>
              <X size={21} />
            </button>
          </div>
        </header>

        <div className="cv-preview-layout">
          <aside className="cv-preview-sidebar">
            <div className="cv-preview-file-heading">
              <span className="cv-preview-file-icon"><FileText size={27} /></span>
              <div>
                <h2 title={cv.name}>{cv.name}</h2>
                <p>{fileSize} <span>·</span> {uploaded}</p>
              </div>
            </div>

            <section className="cv-preview-info">
              <h3><FileText size={17} />{vi ? "Thông tin file" : "File details"}</h3>
              <dl>
                <div><dt>{vi ? "Loại file" : "File type"}</dt><dd>{cv.type.toUpperCase()}</dd></div>
                <div><dt>{vi ? "Dung lượng" : "Size"}</dt><dd>{fileSize}</dd></div>
                <div><dt>{vi ? "Ngày tải lên" : "Uploaded"}</dt><dd>{uploaded}</dd></div>
                <div><dt>{vi ? "Vị trí" : "Role"}</dt><dd>{cv.role || (vi ? "Chưa phân loại" : "Uncategorized")}</dd></div>
              </dl>
            </section>

            <section className="cv-preview-quick-view">
              <h3><FolderOpen size={17} />{vi ? "Xem nhanh" : "Quick view"}</h3>
              <div className="cv-preview-thumbnail" aria-label={vi ? "Xem nhanh trang đầu" : "First page preview"}>
                {isPdf && fileUrl
                  ? <iframe title={vi ? "Ảnh thu nhỏ trang đầu" : "First page thumbnail"} src={fileUrl + "#toolbar=0&navpanes=0&page=1&zoom=38"} />
                  : <FileText size={48} />}
              </div>
              <span className="cv-preview-page-chip">1</span>
            </section>

            {!isPdf && <p className="cv-preview-docx-note">
              {vi ? "Tải file DOCX về máy để xem nội dung đầy đủ." : "Download the DOCX file to view its contents."}
            </p>}
          </aside>

          <section className="cv-preview-main" aria-label={vi ? "Nội dung CV" : "Resume document"}>
            <div className="cv-preview-toolbar">
              {isPdf && <>
                <button type="button" className="cv-tool-button" onClick={() => applyPage(page - 1)} disabled={page <= 1} aria-label={vi ? "Trang trước" : "Previous page"}><ChevronLeft size={18} /></button>
                <label className="cv-page-number">
                  <span className="sr-only">{vi ? "Số trang" : "Page number"}</span>
                  <input type="number" min="1" value={page} onChange={event => applyPage(event.target.value)} />
                </label>
                <span className="cv-page-total">/ PDF</span>
                <span className="cv-toolbar-divider" />
                <button type="button" className="cv-tool-button" onClick={() => setZoom(value => Math.max(50, value - 10))} aria-label={vi ? "Thu nhỏ" : "Zoom out"}><Minus size={17} /></button>
                <span className="cv-zoom-value">{zoom}%</span>
                <button type="button" className="cv-tool-button" onClick={() => setZoom(value => Math.min(200, value + 10))} aria-label={vi ? "Phóng to" : "Zoom in"}><Plus size={17} /></button>
                <span className="cv-toolbar-divider cv-toolbar-spacer" />
                {searchOpen && <form className="cv-preview-search" onSubmit={event => { event.preventDefault(); setAppliedSearch(searchText.trim()); }}>
                  <input value={searchText} onChange={event => setSearchText(event.target.value)} placeholder={vi ? "Tìm trong CV" : "Search in CV"} autoFocus />
                </form>}
                <button type="button" className={"cv-tool-button" + (searchOpen ? " active" : "")} onClick={() => setSearchOpen(value => !value)} aria-label={vi ? "Tìm trong CV" : "Search in CV"}><Search size={18} /></button>
                <button type="button" className="cv-tool-button" onClick={fullscreen} aria-label={vi ? "Toàn màn hình" : "Fullscreen"}><Maximize2 size={17} /></button>
              </>}
              <button type="button" className="cv-preview-download" onClick={downloadFile} disabled={!fileUrl}>
                <Download size={17} />{vi ? "Tải xuống" : "Download"}
              </button>
            </div>

            <div className={"cv-preview-document" + (isPdf ? " pdf" : " docx")} ref={previewPane}>
              {!fileUrl ? (
                <div className="cv-preview-loading"><span className="cv-preview-spinner" />{vi ? "Đang mở CV..." : "Loading resume..."}</div>
              ) : isPdf ? (
                <iframe key={documentUrl} title={cv.name} src={documentUrl} />
              ) : (
                <div className="cv-preview-docx-card">
                  <span className="cv-preview-file-icon"><FileText size={34} /></span>
                  <h3>{cv.name}</h3>
                  <p>{vi ? "File DOCX đã sẵn sàng. Tải về để xem nội dung đầy đủ." : "Your DOCX file is ready. Download it to view the full document."}</p>
                  <button type="button" className="cv-preview-interview" onClick={downloadFile}><Download size={17} />{vi ? "Tải file DOCX" : "Download DOCX"}</button>
                </div>
              )}
            </div>
          </section>
        </div>
      </section>
    </div>
  );
}
