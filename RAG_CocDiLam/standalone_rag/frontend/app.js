const $ = (id) => document.getElementById(id);
const state = { knowledgeBases: [], kb: "interview", activeVersion: "", timer: null, documents: [], deleteId: null, refreshing: false };

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options,
    headers: { ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) } });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    if (path === "/api/v1/auth/login") {
      throw new Error(body.detail === "Invalid credentials" ? "Mật khẩu quản trị không đúng." : (body.detail || "Đăng nhập thất bại."));
    }
    showLogin();
    throw new Error("Phiên đăng nhập đã hết hạn.");
  }
  if (!response.ok) throw new Error(body.detail || `Lỗi HTTP ${response.status}`);
  return body;
}

function showLogin() {
  clearInterval(state.timer);
  $("workspace").hidden = true;
  $("logout").hidden = true;
  $("login-panel").hidden = false;
}

function showWorkspace() {
  $("login-panel").hidden = true;
  $("workspace").hidden = false;
  $("logout").hidden = false;
  refresh();
  clearInterval(state.timer);
  state.timer = setInterval(refresh, 4000);
}

function notice(message, isError = false) {
  const node = $("upload-message");
  node.textContent = message;
  node.classList.toggle("error", isError);
  node.hidden = false;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

function safeSourceLink(value) {
  try {
    if (!/^https?:\/\//i.test(String(value))) return "";
    const url = new URL(String(value), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch { return ""; }
}

async function refresh() {
  if (state.refreshing) return;
  state.refreshing = true;
  try {
    const [health, stats, bases] = await Promise.all([
      fetch("/health/ready").then((r) => r.json()), api("/api/v1/stats"), api("/api/v1/knowledge-bases")
    ]);
    $("service-status").textContent = health.status === "ready" ? "Dịch vụ đang sẵn sàng" : "Dịch vụ chưa sẵn sàng";
    $("service-dot").classList.toggle("ok", health.status === "ready");
    $("stat-documents").textContent = stats.documents;
    $("stat-chunks").textContent = stats.chunks;
    $("stat-pending").textContent = stats.jobs_pending;
    $("stat-failed").textContent = stats.jobs_failed;
    state.knowledgeBases = bases.items;
    if (!state.knowledgeBases.some((item) => item.id === state.kb)) state.kb = state.knowledgeBases[0]?.id || "interview";
    const options = state.knowledgeBases.map((item) => `<option value="${esc(item.id)}">${esc(item.name)} · ${esc(item.id)}</option>`).join("");
    ["kb-select", "search-kb-select", "indexes-kb-select"].forEach(id => {
      const select = $(id);
      // Keep an open picker stable while the background status poll runs.
      if (select.dataset.options !== options) { select.innerHTML = options; select.dataset.options = options; }
      select.value = state.kb;
    });
    const [docs, indexes] = await Promise.all([
      api(`/api/v1/knowledge-bases/${encodeURIComponent(state.kb)}/documents`),
      api(`/api/v1/knowledge-bases/${encodeURIComponent(state.kb)}/indexes`)
    ]);
    state.activeVersion = indexes.active_version;
    $("active-version").textContent = `Index ${String(state.activeVersion).slice(0, 15)}`;
    state.documents = docs.items;
    filterDocuments();
    renderIndexes(indexes);
  } catch (error) {
    if (!$("login-panel").hidden) return;
    $("service-status").textContent = error.message;
    $("service-dot").classList.remove("ok");
  } finally {
    state.refreshing = false;
  }
}

function filterDocuments() {
  const query = $("document-filter").value.trim().toLocaleLowerCase("vi");
  const items = state.documents.filter(doc => `${doc.original_name || doc.title} ${doc.metadata.company || ""} ${doc.metadata.topic || ""}`.toLocaleLowerCase("vi").includes(query));
  $("document-count").textContent = query ? `${items.length} / ${state.documents.length} tài liệu` : `${state.documents.length} tài liệu trong kho`;
  renderDocuments(items);
  $("empty-documents").querySelector("strong").textContent = query ? "Không có tài liệu phù hợp" : "Thêm nguồn tri thức đầu tiên";
  $("empty-documents").querySelector("p").textContent = query ? "Thử một từ khóa khác hoặc xóa bộ lọc." : "Tải lên JD, tài liệu công ty hoặc cẩm nang phỏng vấn để bắt đầu.";
}
$("document-filter").addEventListener("input", filterDocuments);

function renderDocuments(items) {
  $("empty-documents").hidden = items.length > 0;
  $("documents-body").innerHTML = items.map((doc) => {
    const status = doc.status === "ready" ? "Đã lập chỉ mục" : doc.status === "failed" ? "Lỗi xử lý" : doc.status === "processing" ? "Đang xử lý" : "Đang chờ";
    const badge = doc.status === "ready" ? "success" : doc.status === "failed" ? "failure" : "pending";
    return `<tr>
      <td><button class="doc-name" data-preview="${esc(doc.id)}">${esc(doc.original_name || doc.title)}</button><small>${esc(doc.content_hash.slice(0, 12))} · ${doc.page_count || 0} trang</small>${doc.error ? `<small class="error-text">${esc(doc.error)}</small>` : ""}</td>
      <td>${esc(doc.metadata.company || "generic")}<small>${esc(doc.metadata.topic || "general")}</small></td>
      <td><span class="badge ${badge}">${status}</span></td><td>${doc.chunk_count || 0}</td>
      <td class="row-actions"><button class="icon-action" title="Xử lý lại" aria-label="Xử lý lại ${esc(doc.original_name || doc.title)}" data-reprocess="${esc(doc.id)}">↻</button><button class="icon-action danger" title="Xóa tài liệu" aria-label="Xóa ${esc(doc.original_name || doc.title)}" data-delete="${esc(doc.id)}">×</button></td>
    </tr>`;
  }).join("");
}

function renderIndexes(data) {
  $("indexes-list").innerHTML = data.items.map((index) => {
    const active = index.id === data.active_version;
    return `<article class="index-row"><div><strong>${esc(index.id)}</strong><small>${new Date(index.created * 1000).toLocaleString("vi-VN")} · ${index.document_count} tài liệu · ${index.chunk_count} đoạn</small></div>${active ? '<span class="badge success">Đang hoạt động</span>' : `<button class="button small secondary" data-activate="${esc(index.id)}">Kích hoạt</button>`}</article>`;
  }).join("") || '<div class="muted">Chưa có phiên bản index.</div>';
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  $("login-error").textContent = "";
  try {
    await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ password: $("login-password").value }) });
    $("login-password").value = "";
    showWorkspace();
  } catch (error) { $("login-error").textContent = error.message; }
  finally { button.disabled = false; }
});

$("logout").addEventListener("click", async () => {
  try { await api("/api/v1/auth/logout", { method: "POST", body: "{}" }); } catch {}
  showLogin();
});

["kb-select", "search-kb-select", "indexes-kb-select"].forEach(id => $(id).addEventListener("change", event => {
  state.kb = event.target.value;
  $("document-filter").value = "";
  $("results").replaceChildren(); $("search-message").hidden = true;
  refresh();
}));
$("new-kb").addEventListener("click", () => {
  $("create-kb-error").textContent = "";
  $("create-kb-form").reset();
  $("create-kb-dialog").showModal();
});
document.querySelectorAll("[data-close-dialog]").forEach(button => button.addEventListener("click", () => $(button.dataset.closeDialog).close()));
$("create-kb-form").addEventListener("submit", async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  const id = $("new-kb-id").value.trim(), name = $("new-kb-name").value.trim();
  try {
    await api("/api/v1/knowledge-bases", {method: "POST", body: JSON.stringify({id, name})});
    state.kb = id;
    $("create-kb-dialog").close();
    await refresh();
    notice(`Đã tạo kho “${name}”. Bạn có thể thêm tài liệu ngay.`);
  } catch (error) { $("create-kb-error").textContent = error.message; }
  finally { button.disabled = false; }
});

$("choose-files").addEventListener("click", () => $("file-input").click());
function renderSelectedFiles() {
  const files = [...$("file-input").files];
  $("selected-files").hidden = !files.length;
  $("selected-files").innerHTML = files.map(file => `<span class="file-chip">${esc(file.name)} · ${(file.size / 1024 / 1024).toFixed(1)} MB</span>`).join("");
}
$("file-input").addEventListener("change", renderSelectedFiles);
["dragenter", "dragover"].forEach(name => $("drop-area").addEventListener(name, event => { event.preventDefault(); $("drop-area").classList.add("drag-over"); }));
["dragleave", "drop"].forEach(name => $("drop-area").addEventListener(name, event => { event.preventDefault(); $("drop-area").classList.remove("drag-over"); }));
$("drop-area").addEventListener("drop", event => {
  if (!event.dataTransfer?.files.length) return;
  $("file-input").files = event.dataTransfer.files;
  renderSelectedFiles();
});

$("upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = [...$("file-input").files];
  if (!files.length) return notice("Hãy chọn ít nhất một tài liệu.", true);
  if (files.some(file => !/\.(pdf|docx|txt|md)$/i.test(file.name))) return notice("Chỉ hỗ trợ PDF, DOCX, TXT và Markdown. Hãy chọn lại tệp.", true);
  if (files.some(file => file.size > 25 * 1024 * 1024)) return notice("Mỗi tài liệu cần nhỏ hơn hoặc bằng 25 MB.", true);
  const button = event.submitter;
  button.disabled = true;
  try {
    for (const file of files) {
      const data = new FormData();
      data.append("file", file);
      data.append("metadata", JSON.stringify({ company: $("company-input").value.trim() || "generic", topic: $("topic-input").value.trim() || "general", language: "vi", review_status: "unreviewed", confidence: 0.7 }));
      const result = await api(`/api/v1/knowledge-bases/${encodeURIComponent(state.kb)}/documents`, { method: "POST", body: data });
      notice(`${file.name}: đã nhận, mã tác vụ ${result.job_id.slice(0, 8)}.`);
    }
    $("file-input").value = "";
    renderSelectedFiles();
    await refresh();
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; }
});

$("rebuild").addEventListener("click", async () => {
  try {
    const result = await api(`/api/v1/knowledge-bases/${encodeURIComponent(state.kb)}/rebuild`, { method: "POST", body: "{}" });
    notice(`Đã đưa ${result.queued} tài liệu vào hàng chờ xử lý lại.`);
    await refresh();
  } catch (error) { notice(error.message, true); }
});

$("documents-body").addEventListener("click", async (event) => {
  const preview = event.target.closest("[data-preview]")?.dataset.preview;
  const reprocess = event.target.closest("[data-reprocess]")?.dataset.reprocess;
  const remove = event.target.closest("[data-delete]")?.dataset.delete;
  try {
    if (preview) {
      const [doc, chunks] = await Promise.all([api(`/api/v1/documents/${preview}`), api(`/api/v1/documents/${preview}/chunks`)]);
      $("preview-title").textContent = doc.original_name || doc.title;
      $("preview-content").innerHTML = chunks.items.map((chunk) => `<article><span>Trang ${chunk.page || 1} · đoạn ${chunk.chunk_index + 1}</span><p>${esc(chunk.content)}</p></article>`).join("") || '<p class="muted">Tài liệu chưa có đoạn văn bản.</p>';
      $("preview-dialog").showModal();
    } else if (reprocess) {
      await api(`/api/v1/documents/${reprocess}/reprocess`, { method: "POST", body: "{}" });
      notice("Đã xếp tài liệu vào hàng chờ xử lý lại.");
      await refresh();
    } else if (remove) {
      state.deleteId = remove;
      $("delete-error").textContent = "";
      $("delete-dialog").showModal();
    }
  } catch (error) { notice(error.message, true); }
});

$("confirm-delete").addEventListener("click", async () => {
  if (!state.deleteId) return;
  const button = $("confirm-delete"); button.disabled = true;
  try {
    await api(`/api/v1/documents/${encodeURIComponent(state.deleteId)}`, {method: "DELETE"});
    $("delete-dialog").close(); state.deleteId = null;
    notice("Đã xóa tài liệu và tạo phiên bản chỉ mục mới.");
    await refresh();
  } catch (error) { $("delete-error").textContent = error.message; }
  finally { button.disabled = false; }
});

$("search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("search-message");
  const results = $("results");
  const button = event.submitter;
  button.disabled = true;
  results.innerHTML = '<div class="loading-skeleton" aria-label="Đang tìm dữ liệu"></div>';
  results.setAttribute("aria-busy", "true");
  message.hidden = false;
  message.textContent = "Đang tìm kiếm…";
  const company = $("search-company").value.trim();
  const filters = company ? { company: [company.toLowerCase(), "generic"] } : {};
  try {
    const result = await api("/api/v1/retrieval/search", { method: "POST", body: JSON.stringify({
      knowledge_base_id: state.kb, index_version: state.activeVersion, collection: "company_knowledge",
      text: $("query-input").value, filters, top_k: Number($("top-k").value)
    }) });
    message.textContent = `${result.chunks.length} kết quả từ kho đang chọn`;
    message.title = `Chỉ mục ${result.index_version}`;
    results.innerHTML = result.chunks.map((chunk) => {
      const source = String(chunk.source || "");
      const href = safeSourceLink(source);
      const sourceHtml = href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(source)}</a>` : `<span>${esc(source)}</span>`;
      return `<article class="result-card"><div class="result-meta"><span>${esc(chunk.metadata.company || "generic")} · ${esc(chunk.metadata.topic || "general")}</span><span>Điểm ${Number(chunk.score).toFixed(4)}</span></div><p>${esc(chunk.content)}</p>${sourceHtml}</article>`;
    }).join("") || '<div class="empty-state compact">Không tìm thấy đoạn phù hợp.</div>';
  } catch (error) { message.textContent = error.message; results.replaceChildren(); }
  finally { button.disabled = false; results.removeAttribute("aria-busy"); }
});

$("indexes-list").addEventListener("click", async (event) => {
  const version = event.target.closest("[data-activate]")?.dataset.activate;
  if (!version) return;
  try {
    await api(`/api/v1/knowledge-bases/${encodeURIComponent(state.kb)}/indexes/${encodeURIComponent(version)}/activate`, { method: "POST", body: "{}" });
    notice(`Đã kích hoạt ${version}.`);
    await refresh();
  } catch (error) { notice(error.message, true); }
});

async function start() {
  try {
    const response = await fetch("/health/ready");
    const health = await response.json();
    $("service-status").textContent = health.status === "ready" ? "Dịch vụ đang sẵn sàng" : "Dịch vụ chưa sẵn sàng";
    $("service-dot").classList.toggle("ok", health.status === "ready");
    const auth = await api("/api/v1/auth/me");
    if (auth.authenticated) showWorkspace(); else showLogin();
  } catch (error) {
    $("service-status").textContent = error.message;
    showLogin();
  }
}

start();

