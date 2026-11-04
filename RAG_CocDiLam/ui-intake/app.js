(() => {
  const $ = (id) => document.getElementById(id);
  const state = {mode: "form", intake: null, collection: "company_knowledge", pack: null, sessionKey: null, collectionSeq: 0, dirty: false};
  function requestHeaders(json = true) {
    const result = new Headers();
    if (json) result.set("Content-Type", "application/json");
    const token = $("api-token").value.trim();
    if (token) result.set("Authorization", `Bearer ${token}`);
    return result;
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {...options, cache: "no-store"});
    if (response.status === 204) return null;
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(response.status === 401 ? "Hãy đăng nhập workspace hoặc nhập token dịch vụ để tiếp tục." : (typeof body.detail === "string" ? body.detail : `Không thể hoàn tất thao tác (${response.status}).`));
    return body;
  }
  function setMessage(id, message, bad = true) {
    $(id).textContent = message || "";
    const loading = !bad && message?.startsWith("Đang");
    $(id).classList.toggle("error", Boolean(message) && bad);
    $(id).classList.toggle("success", Boolean(message) && !bad && !loading);
    $(id).classList.toggle("is-loading", Boolean(loading));
  }
  function setStep(step) {
    document.querySelectorAll('[data-step]').forEach(node => {
      const number = Number(node.dataset.step);
      node.classList.toggle('active', number === step);
      node.classList.toggle('complete', number < step);
      if (number === step) node.setAttribute('aria-current', 'step'); else node.removeAttribute('aria-current');
    });
  }
  function markDirty() {
    state.dirty = true; state.pack = null; state.sessionKey = null;
    $("prepare-button").disabled = true;
    $("knowledge-panel").hidden = true;
    setStep(2);
    setMessage("workspace-message", "Có thay đổi chưa lưu. Lưu chỉnh sửa để chuẩn bị bộ tri thức mới.", false);
  }
  function syncReview() {
    try {
      const profile = objectFrom("profile-json", "hồ sơ");
      const target = objectFrom("target-json", "mục tiêu");
      Object.assign(profile, {candidate_name: $("review-name").value.trim() || null, skills: $("review-skills").value.split(/[,;]+/).map(x=>x.trim()).filter(Boolean), summary: $("review-summary").value.trim() || null});
      Object.assign(target.interview_target, {company_name: $("review-company").value.trim() || null, role_title: $("review-role").value.trim() || null, target_seniority: $("review-level").value || null});
      // A changed title/company must be resolved again instead of retaining old IDs.
      if (target.interview_target.role_title !== state.intake.interview_target.role_title) { target.interview_target.role_id = null; target.interview_target.jd_id = null; }
      if (target.interview_target.company_name !== state.intake.interview_target.company_name) { target.interview_target.company_id = null; target.interview_target.jd_id = null; }
      if (target.interview_target.target_seniority !== state.intake.interview_target.target_seniority) target.interview_target.jd_id = null;
      $("profile-json").value = JSON.stringify(profile, null, 2);
      $("target-json").value = JSON.stringify(target, null, 2);
      markDirty();
    } catch (error) { setMessage("workspace-message", error.message); }
  }
  ["review-name", "review-skills", "review-company", "review-role", "review-level", "review-summary"].forEach(id => $(id).addEventListener("input", syncReview));
  ["profile-json", "target-json"].forEach(id => $(id).addEventListener("input", markDirty));
  function objectFrom(id, fallback) {
    try { return JSON.parse($(id).value); }
    catch { throw new Error(`JSON trong mục ${fallback} chưa hợp lệ.`); }
  }
  function targetFrom(form) {
    const value = Object.fromEntries(new FormData(form).entries());
    return {
      interview_target: {
        company_name: String(value.company_name || "").trim() || null,
        role_title: String(value.role_title || "").trim() || null,
        target_seniority: value.target_seniority || null
      },
      preferences: {
        interview_language: value.interview_language || "vi",
        interview_round: value.interview_round || null,
        duration_minutes: value.duration_minutes ? Number(value.duration_minutes) : null
      }
    };
  }
  function formProfile(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    const rows = (value) => String(value || "").split(/\n+/).map((text) => text.trim()).filter(Boolean).map((text) => ({text}));
    return {
      candidate_name: String(data.candidate_name || "").trim() || null,
      summary: String(data.summary || "").trim() || null,
      experience_years: data.experience_years ? Number(data.experience_years) : null,
      skills: String(data.skills || "").split(/[,;]+/).map((item) => item.trim()).filter(Boolean),
      experience: rows(data.experience), projects: rows(data.projects), education: rows(data.education),
      certifications: [], awards: [], career_goals: [], additional_sections: rows(data.additional_sections)
    };
  }
  function selectMode(mode) {
    state.mode = mode;
    $("form-fields").hidden = mode !== "form";
    $("cv-fields").hidden = mode !== "cv";
    document.querySelectorAll("[data-mode]").forEach((button) => { button.classList.toggle("active", button.dataset.mode === mode); button.setAttribute("aria-pressed", String(button.dataset.mode === mode)); });
    const cv = document.querySelector('[name="cv_file"]');
    if (mode === "form") { cv.value = ""; $("cv-file-label").textContent = "PDF, DOCX, TXT hoặc Markdown · tối đa 12 MB"; }
    else document.querySelectorAll("#form-fields input, #form-fields textarea").forEach((field) => { field.value = ""; });
  }
  function renderIntake(intake) {
    state.intake = intake;
    state.pack = null;
    state.sessionKey = null;
    state.dirty = false;
    state.collectionSeq += 1;
    setStep(2);
    $("workspace").hidden = false;
    $("workspace-title").textContent = intake.candidate_profile.candidate_name || "Hồ sơ ứng viên";
    $("intake-badge").textContent = `${intake.id_q} · rev ${intake.revision}`;
    $("workspace-status").textContent = intake.status === "ready" ? "Đã lưu. Hãy kiểm tra hồ sơ và mục tiêu trước khi chuẩn bị." : "Cần bổ sung mục tiêu ứng tuyển trước khi tạo knowledge pack.";
    $("profile-json").value = JSON.stringify(intake.candidate_profile, null, 2);
    $("target-json").value = JSON.stringify({interview_target: intake.interview_target, preferences: intake.preferences}, null, 2);
    $("review-name").value = intake.candidate_profile.candidate_name || "";
    $("review-skills").value = (intake.candidate_profile.skills || []).join(", ");
    $("review-summary").value = intake.candidate_profile.summary || "";
    $("review-company").value = intake.interview_target.company_name || "";
    $("review-role").value = intake.interview_target.role_title || "";
    $("review-level").value = intake.interview_target.target_seniority || "";
    const name = intake.candidate_profile.candidate_name || "Ứng viên";
    $("review-candidate-label").textContent = name;
    $("profile-avatar").textContent = name.split(/\s+/).filter(Boolean).slice(-2).map(x => x[0]).join("").toUpperCase();
    $("review-target-label").textContent = [intake.interview_target.role_title, intake.interview_target.company_name].filter(Boolean).join(" · ");
    $("prepare-button").disabled = intake.status !== "ready";
    $("knowledge-panel").hidden = true;
    $("web-research-summary").hidden = true;
    $("session-launch").hidden = true;
    $("session-result").hidden = true;
    $("workspace").scrollIntoView({behavior: "smooth", block: "start"});
  }
  function renderWebResearch(result) {
    const panel = $("web-research-summary");
    panel.replaceChildren();
    if (state.collection !== "company_knowledge") {
      panel.hidden = true;
      return;
    }
    const research = result.web_research || {};
    const selected = result.selected_jd || {};
    const origin = selected.data_origin === "web_search";
    const researchLabels = {unavailable: "Chưa có nguồn web khả dụng", disabled: "Đang chỉ sử dụng kho tri thức", not_found: "Chưa tìm thấy nguồn web phù hợp", no_results: "Chưa tìm thấy nguồn web phù hợp", failed: "Tìm kiếm web chưa thành công", selected: "Đã bổ sung từ nguồn web", candidates: "Có nguồn tham khảo, cần kiểm tra", cached: "Đang sử dụng kết quả đã lưu"};
    const label = origin
      ? "Nguồn web – chưa xác minh"
      : research.status === "not_needed" || result.selected_jd
        ? "Nguồn kho tri thức nội bộ"
        : researchLabels[research.status] || "Chưa có nguồn web phù hợp";
    panel.append(document.createTextNode(label));
    panel.classList.toggle("warning", !origin && research.status !== "not_needed");
    const sources = Array.isArray(result.sources) ? result.sources : [];
    sources.slice(0, 4).forEach((source) => {
      if (!source?.url) return;
      panel.append(document.createTextNode(" · "));
      const link = document.createElement("a");
      try { const url = new URL(source.url); if (!["http:", "https:"].includes(url.protocol)) return; link.href = url.href; } catch { return; }
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = source.title || new URL(link.href).hostname;
      panel.append(link);
    });
    panel.hidden = false;
  }
  const esc = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[char]);
  function asText(value) {
    if (value == null) return "";
    if (typeof value === "string" || typeof value === "number") return String(value);
    if (Array.isArray(value)) return value.map(asText).filter(Boolean).join(" · ");
    return value.text || value.name || value.description || value.title || "";
  }
  function renderCollection(result, collection) {
    const items = Array.isArray(result.items) ? result.items : [];
    const panel = $("collection-content");
    if (!items.length) {
      const message = collection === "company_knowledge" ? "Chưa tìm thấy JD phù hợp" : collection === "question_bank" ? "Chưa có câu hỏi phù hợp trong kho" : "Chưa có dữ liệu cho mục này";
      panel.innerHTML = `<div class="empty-state"><div class="empty-icon">⌕</div><strong>${message}</strong><p>${collection === "company_knowledge" ? "Kiểm tra tên công ty, vị trí hoặc chọn tìm mới từ web. Bạn cũng có thể bổ sung JD vào kho tri thức." : "Kiểm tra vị trí, cấp độ hoặc bổ sung nguồn tri thức để chuẩn bị lại."}</p></div>`;
      return;
    }
    panel.innerHTML = items.map((item, index) => {
      const title = (collection === "question_bank" ? `Câu hỏi ${index + 1}` : collection === "role_profile" ? item.role_title : collection === "rubric" ? item.name : item.title) || `Nội dung ${index + 1}`;
      const body = collection === "question_bank" ? item.question : collection === "company_knowledge" || collection === "rubric" ? item.description : "";
      const facts = collection === "role_profile" ? [["Cấp độ", item.target_seniority], ["Nhóm vị trí", item.role_family]] : collection === "rubric" ? [["Trọng số", Number.isFinite(item.weight) ? `${Math.round(item.weight * 100)}%` : ""], ["Yêu cầu", item.must_have ? "Bắt buộc" : "Đánh giá theo tiêu chí"]] : [];
      const list = collection === "company_knowledge" ? item.requirements || [] : collection === "role_profile" ? item.expectations || [] : [];
      const values = Array.isArray(list) ? list : [list];
      let sourceHtml = "";
      try { const url = new URL(item.source_url || ""); if (["http:", "https:"].includes(url.protocol)) sourceHtml = `<a class="evidence-source" href="${esc(url.href)}" target="_blank" rel="noopener noreferrer">Xem nguồn · ${esc(url.hostname)} ↗</a>`; } catch {}
      const review = item.data_origin === "web_search" ? "Nguồn web · chưa xác minh" : item.review_status === "generic_project_framework" ? "Khung tiêu chí tham khảo" : "Nguồn kho tri thức · chưa duyệt";
      const jdNotice = collection === "company_knowledge" && item.extraction_status === "insufficient_evidence" ? "<p>Chưa trích xuất được nhiệm vụ hoặc yêu cầu phù hợp từ nguồn này.</p>" : "";
      return `<article class="evidence-card"><div class="evidence-meta"><span class="badge neutral">${esc(review)}</span>${collection === "question_bank" && item.stage_type ? `<span>${item.stage_type === "behavioral" ? "Hành vi" : "Kỹ thuật"}</span>` : ""}</div><h3>${esc(title)}</h3>${jdNotice}${body ? `${collection === "company_knowledge" ? "<h4>Nhiệm vụ công việc</h4>" : ""}<p>${esc(asText(body))}</p>` : ""}${facts.length ? `<dl class="fact-grid">${facts.filter(([,value])=>value).map(([key,value])=>`<div><dt>${esc(key)}</dt><dd>${esc(asText(value))}</dd></div>`).join("")}</dl>` : ""}${values.length ? `${collection === "company_knowledge" ? "<h4>Yêu cầu ứng viên</h4>" : ""}<ul>${values.map(value=>`<li>${esc(asText(value))}</li>`).join("")}</ul>` : ""}${sourceHtml}</article>`;
    }).join("");
  }
  async function refreshCollection() {
    if (!state.intake) return;
    const collection = state.collection, seq = ++state.collectionSeq;
    const panel = $("collection-content");
    panel.setAttribute("aria-busy", "true");
    panel.innerHTML = '<div class="loading-skeleton" aria-label="Đang tải bộ tri thức"></div>';
    let result;
    try { result = await api(`/api/v1/intakes/${encodeURIComponent(state.intake.id_q)}/${collection}?top_k=10`, {headers: requestHeaders(false)}); }
    catch (error) { if (seq === state.collectionSeq) { panel.textContent = error.message; panel.removeAttribute("aria-busy"); } throw error; }
    if (seq !== state.collectionSeq) return;
    panel.removeAttribute("aria-busy");
    $("collection-result").textContent = JSON.stringify(result, null, 2);
    renderCollection(result, collection);
    renderWebResearch(result);
  }
  document.querySelectorAll("[data-mode]").forEach((button) => button.addEventListener("click", () => selectMode(button.dataset.mode)));
  document.querySelectorAll("[data-collection]").forEach((button) => button.addEventListener("click", async () => {
    state.collection = button.dataset.collection;
    document.querySelectorAll("[data-collection]").forEach((item) => { item.classList.toggle("active", item === button); item.setAttribute("aria-selected", String(item === button)); item.tabIndex = item === button ? 0 : -1; });
    $("collection-content").setAttribute("aria-labelledby", button.id);
    try { await refreshCollection(); } catch (error) { $("collection-result").textContent = error.message; }
  }));
  document.querySelectorAll('[data-collection]').forEach((button, index, buttons) => {
    button.tabIndex = index === 0 ? 0 : -1;
    button.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus(); buttons[next].click();
    });
  });
  document.querySelector('[name="cv_file"]').addEventListener('change', event => {
    const file = event.target.files[0];
    $("cv-file-label").textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)} MB` : 'PDF, DOCX, TXT hoặc Markdown · tối đa 12 MB';
  });
  $("intake-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = $("create-button");
    submit.disabled = true;
    setMessage("form-message", "Đang lưu hồ sơ…", false);
    try {
      const target = targetFrom(form);
      let result;
      if (state.mode === "form") {
      result = await api("/api/v1/intakes/form", {method: "POST", headers: requestHeaders(), body: JSON.stringify({candidate_profile: formProfile(form), ...target})});
      } else {
        const file = new FormData(form).get("cv_file");
        if (!(file instanceof File) || !file.size) throw new Error("Hãy chọn một tệp CV.");
        if (file.size > 12 * 1024 * 1024) throw new Error("CV vượt quá 12 MB. Hãy chọn tệp nhỏ hơn.");
        const multipart = new FormData();
        multipart.append("metadata", JSON.stringify(target));
        multipart.append("file", file, file.name);
      result = await api("/api/v1/intakes/cv", {method: "POST", headers: requestHeaders(false), body: multipart});
      }
      const intake = await api(result.status_url);
      renderIntake(intake);
      setMessage("form-message", `Đã tạo ${result.id_q}.`, false);
    } catch (error) { setMessage("form-message", error.message); }
    finally { submit.disabled = false; }
  });
  $("save-button").addEventListener("click", async () => {
    if (!state.intake) return;
    const button = $("save-button"); button.disabled = true;
    try {
      const target = objectFrom("target-json", "mục tiêu");
      const updated = await api(`/api/v1/intakes/${encodeURIComponent(state.intake.id_q)}`, {method: "PATCH", headers: requestHeaders(), body: JSON.stringify({expected_revision: state.intake.revision, candidate_profile: objectFrom("profile-json", "hồ sơ"), ...target})});
      renderIntake(updated);
      setMessage("workspace-message", "Đã lưu revision mới. Hãy chuẩn bị lại pack.", false);
    } catch (error) { setMessage("workspace-message", error.message); }
    finally { button.disabled = false; }
  });
  $("prepare-button").addEventListener("click", async () => {
    if (!state.intake) return;
    const button = $("prepare-button");
    button.disabled = true;
    $("save-button").disabled = true;
    state.pack = null; state.sessionKey = null;
    $("knowledge-panel").hidden = true;
    document.querySelectorAll('#workspace input, #workspace textarea, #workspace select').forEach(field => { field.disabled = true; });
    setStep(3);
    setMessage("workspace-message", "Đang tìm JD và chuẩn bị bộ tri thức…", false);
    try {
      const result = await api(`/api/v1/intakes/${encodeURIComponent(state.intake.id_q)}/prepare`, {method: "POST", headers: requestHeaders(), body: JSON.stringify({expected_revision: state.intake.revision, web_search_mode: $("web-search-mode").value})});
      state.pack = result;
      state.sessionKey = crypto.randomUUID();
      $("knowledge-panel").hidden = false;
      $("session-launch").hidden = false;
      setMessage("workspace-message", `Bộ tri thức đã sẵn sàng.${result.limitations?.length ? " Một số nguồn còn thiếu hoặc chưa xác minh; hãy kiểm tra nội dung trước khi tiếp tục. Chi tiết có trong dữ liệu JSON." : " Hãy kiểm tra nội dung rồi tạo buổi phỏng vấn."}`, false);
      setStep(4);
      await refreshCollection();
    } catch (error) { setMessage("workspace-message", error.message); setStep(2); }
    finally { button.disabled = state.intake?.status !== "ready" || state.dirty; $("save-button").disabled = false; document.querySelectorAll('#workspace input, #workspace textarea, #workspace select').forEach(field => { field.disabled = false; }); }
  });
  async function startInterview() {
    if (!state.intake || !state.pack?.pack_id) throw new Error("Hãy chuẩn bị knowledge pack trước.");
    const baseInput = $("core-api-url").value.trim() || `${location.protocol}//${location.hostname}:8010`;
    const baseUrl = new URL(baseInput);
    if (!["http:", "https:"].includes(baseUrl.protocol) || baseUrl.username || baseUrl.password || baseUrl.search || baseUrl.hash) {
      throw new Error("Core API URL phải là địa chỉ HTTP(S) hợp lệ, không chứa thông tin đăng nhập.");
    }
    const base = baseUrl.href.replace(/\/$/, "");
    let token = $("core-api-token").value.trim();
    if (!token) {
      const configResponse = await fetch(`${base}/api/v1/inspect/config`, {cache: "no-store"});
      const config = await configResponse.json().catch(() => ({}));
      if (!configResponse.ok || !config.local_token_enabled) throw new Error("Core từ xa cần service token. Hãy nhập token Core ở phía trên.");
      const tokenResponse = await fetch(`${base}/api/v1/inspect/token`, {method: "POST", cache: "no-store"});
      const tokenData = await tokenResponse.json().catch(() => ({}));
      if (!tokenResponse.ok || !tokenData.token) throw new Error(tokenData.detail || "Không lấy được token local từ Core.");
      token = tokenData.token;
    }
    const response = await fetch(`${base}/api/v1/sessions/from-idq`, {
      method: "POST",
      headers: {"Content-Type": "application/json", "Authorization": `Bearer ${token}`, "Idempotency-Key": state.sessionKey || crypto.randomUUID()},
      body: JSON.stringify({id_q: state.intake.id_q, pack_id: state.pack.pack_id}),
      cache: "no-store"
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `Core không thể tạo phiên (${response.status}).`);
    $("session-id-label").textContent = `Đã tạo phiên ${payload.session_id} · ${payload.status || "PROFILING"}`;
    const link = new URL("/test/", base);
    link.searchParams.set("session_id", payload.session_id);
    $("session-link").href = link.href;
    $("session-result").hidden = false;
    setMessage("session-message", "Core đang chuẩn bị hồ sơ phỏng vấn.", false);
  }
  $("start-interview-button").addEventListener("click", async () => {
    const button = $("start-interview-button");
    button.disabled = true;
    setMessage("session-message", "Đang tạo buổi phỏng vấn…", false);
    try { await startInterview(); }
    catch (error) { setMessage("session-message", error.message); }
    finally { button.disabled = false; }
  });
  async function health() {
    try {
      const result = await api("/health", {headers: requestHeaders(false)});
      $("health-dot").classList.add("ok");
      $("health-label").textContent = "Hệ thống đang sẵn sàng";
      $("health-label").title = `Chỉ mục ${result.index_version}`;
    } catch { $("health-label").textContent = "Không kết nối được RAG API"; }
  }
  $("api-token").addEventListener("change", health);
  fetch("/api/v1/auth/me", {credentials: "same-origin", cache: "no-store"}).then(async response => {
    if (response.ok) $("auth-banner").hidden = Boolean((await response.json()).authenticated);
  }).catch(() => {});
  health();
})();
