const state = {
  token: sessionStorage.getItem("jobAgentToken") || "",
  profiles: [], policies: [], jobs: [], analyses: [],
  selectedProfile: null, selectedJob: null, profileEtag: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
})[char]);
const date = value => value ? new Intl.DateTimeFormat("zh-CN", {dateStyle: "medium", timeStyle: "short"}).format(new Date(value)) : "—";
const pageNames = {overview: "概览", profiles: "候选人档案", policies: "匹配策略", jobs: "职位与分析"};

function toast(message, bad = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("bad", bad);
  node.classList.remove("hidden");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.add("hidden"), 3600);
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, {...options, headers});
  const type = response.headers.get("content-type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) {
    if (response.status === 401) setConnected(false);
    throw new Error(payload?.error?.message || `请求失败（${response.status}）`);
  }
  return {data: payload, response};
}

function setConnected(connected) {
  const badge = $("#connectionBadge");
  badge.textContent = connected ? "后端已连接" : "未连接";
  badge.classList.toggle("muted", !connected);
  if (!connected) {
    state.token = "";
    sessionStorage.removeItem("jobAgentToken");
  }
}

function metric(id, items, cursor) {
  $(`#${id}Count`).textContent = `${items.length}${cursor ? "+" : ""}`;
  $(`#${id}CountHint`).textContent = cursor ? "仅显示前100项" : "当前已保存";
}

async function loadAll() {
  if (!state.token) { $("#tokenDialog").showModal(); return; }
  $("#refreshButton").disabled = true;
  try {
    const [profiles, policies, jobs, analyses] = await Promise.all([
      api("/profiles?limit=100"), api("/policies?limit=100"),
      api("/jobs?limit=100"), api("/analyses?limit=100"),
    ]);
    state.profiles = profiles.data.items;
    state.policies = policies.data.items;
    state.jobs = jobs.data.items;
    state.analyses = analyses.data.items;
    setConnected(true);
    metric("profile", state.profiles, profiles.data.next_cursor);
    metric("policy", state.policies, policies.data.next_cursor);
    metric("job", state.jobs, jobs.data.next_cursor);
    metric("analysis", state.analyses, analyses.data.next_cursor);
    renderAll();
    $("#globalNotice").classList.add("hidden");
  } catch (error) {
    $("#globalNotice").textContent = error.message;
    $("#globalNotice").classList.remove("hidden");
    toast(error.message, true);
  } finally {
    $("#refreshButton").disabled = false;
  }
}

function scoreClass(score) { return score !== null && Number(score) >= 70 ? "" : "low"; }
function analysisPill(item) {
  if (!item.is_current) return '<span class="pill muted">已过期</span>';
  if (item.status === "needs_review") return '<span class="pill warning">需复核</span>';
  if (item.eligible) return '<span class="pill">可申请</span>';
  return '<span class="pill danger">未通过</span>';
}

function renderRecentAnalyses() {
  const root = $("#recentAnalyses");
  if (!state.analyses.length) { root.className = "stack-list empty-state"; root.textContent = "暂无分析结果"; return; }
  root.className = "stack-list";
  root.innerHTML = state.analyses.slice(0, 5).map(item => `
    <button class="list-row" data-analysis-job="${esc(item.job_id)}">
      <span class="score ${scoreClass(item.score)}">${item.score === null ? "?" : Math.round(Number(item.score))}</span>
      <span class="grow"><strong>${esc(item.job_title)}</strong><small>${esc(item.company_name)} · ${esc(item.profile_name)}</small></span>
      ${analysisPill(item)}
    </button>`).join("");
}

function renderProfiles() {
  const root = $("#profileList");
  const filter = $("#analysisProfileFilter");
  filter.innerHTML = '<option value="">全部档案</option>' + state.profiles.map(item => `<option value="${esc(item.id)}">${esc(item.name)}</option>`).join("");
  if (!state.profiles.length) { root.className = "stack-list empty-state"; root.textContent = "暂无档案，先创建一个空白档案"; return; }
  root.className = "stack-list";
  root.innerHTML = state.profiles.map(item => `
    <button class="list-row ${state.selectedProfile?.id === item.id ? "active" : ""}" data-profile-id="${esc(item.id)}">
      <span class="score">${item.skills.length}</span><span class="grow"><strong>${esc(item.name)}</strong><small>版本 ${item.version} · ${item.experience_years ?? "?"} 年经验</small></span>
    </button>`).join("");
}

async function selectProfile(id) {
  try {
    const [profile, resumes] = await Promise.all([api(`/profiles/${id}`), api(`/profiles/${id}/resumes?limit=100`)]);
    state.selectedProfile = profile.data;
    state.selectedProfile.resumes = resumes.data.items;
    state.profileEtag = profile.response.headers.get("etag");
    renderProfiles(); renderProfileDetail();
  } catch (error) { toast(error.message, true); }
}

function renderProfileDetail() {
  const root = $("#profileDetail"), item = state.selectedProfile;
  if (!item) { root.className = "panel detail-panel empty-state"; root.textContent = "从左侧选择一个档案"; return; }
  const skills = item.skills.length ? item.skills.map(skill => `<span class="tag"><b>${esc(skill.canonical_name)}</b> · ${esc(skill.level)}${skill.years ? ` · ${esc(skill.years)}年` : ""}</span>`).join("") : '<span class="pill muted">等待简历导入</span>';
  const education = Object.entries(item.education || {}).filter(([, value]) => value).map(([key, value]) => `<span class="tag">${esc(key)}：${esc(value)}</span>`).join("") || '<span class="pill muted">未填写</span>';
  const resumes = item.resumes?.length ? item.resumes.map(file => `<span class="tag">v${file.version} · ${esc(file.filename)}</span>`).join("") : '<span class="pill muted">尚未上传</span>';
  root.className = "panel detail-panel";
  root.innerHTML = `
    <div class="detail-head"><div><p class="eyebrow">PROFILE V${item.version}</p><h2>${esc(item.name)}</h2><p>${esc(item.experience_years ?? "未知")} 年经验 · ${esc(item.preferred_cities.join(" / ") || "未设置城市")}</p></div><span class="pill">${item.skills.length} 项技能</span></div>
    <div class="detail-section"><h3>技能与能力证据</h3><div class="tag-list">${skills}</div></div>
    <div class="detail-section"><h3>教育信息</h3><div class="tag-list">${education}</div></div>
    <div class="detail-section"><h3>已上传简历</h3><div class="tag-list">${resumes}</div></div>
    <form id="resumeUploadForm" class="upload-box">
      <h3>上传并解析新简历</h3><p>支持 PDF/DOCX。默认只补充缺失字段，不覆盖人工填写内容。</p>
      <div class="upload-actions"><input name="resume" type="file" accept=".pdf,.docx" required><button class="button primary" type="submit">上传并生成档案</button></div>
    </form>`;
  $("#resumeUploadForm").addEventListener("submit", uploadResume);
}

async function uploadResume(event) {
  event.preventDefault();
  const button = $("button[type=submit]", event.currentTarget);
  const file = $("input[type=file]", event.currentTarget).files[0];
  if (!file) return;
  button.disabled = true; button.textContent = "正在解析…";
  try {
    const form = new FormData(); form.append("file", file);
    const uploaded = await api(`/profiles/${state.selectedProfile.id}/resumes`, {method: "POST", body: form});
    const imported = await api(`/profiles/${state.selectedProfile.id}/resumes/${uploaded.data.id}/import`, {
      method: "POST", headers: {"If-Match": state.profileEtag},
      body: JSON.stringify({mode: "fill_missing", set_default_resume: true}),
    });
    toast(imported.data.status === "needs_review" ? "档案已生成，但存在需人工复核的信息" : "简历解析完成，档案已更新");
    await loadAll(); await selectProfile(state.selectedProfile.id);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "上传并生成档案"; }
}

function renderPolicies() {
  const root = $("#policyList");
  if (!state.policies.length) { root.className = "card-grid empty-state"; root.textContent = "暂无策略"; return; }
  root.className = "card-grid";
  root.innerHTML = state.policies.map(item => `
    <article class="policy-card"><span class="pill ${item.auto_apply ? "warning" : "muted"}">${item.auto_apply ? "自动申请开启" : "仅分析"}</span><h3>${esc(item.name)}</h3>
      <div class="policy-stats"><div><strong>${esc(item.comparison === "gte" ? "≥" : ">")} ${esc(item.threshold)}</strong><small>匹配阈值</small></div><div><strong>${esc(item.daily_limit)}</strong><small>每日上限</small></div></div>
    </article>`).join("");
}

function analysesFor(jobId) { return state.analyses.filter(item => item.job_id === jobId); }
function renderJobs() {
  const root = $("#jobList");
  if (!state.jobs.length) { root.className = "stack-list empty-state"; root.textContent = "暂无职位，请先运行桌面采集器"; return; }
  root.className = "stack-list";
  root.innerHTML = state.jobs.map(item => {
    const latest = analysesFor(item.id)[0];
    return `<button class="list-row ${state.selectedJob?.id === item.id ? "active" : ""}" data-job-id="${esc(item.id)}">
      <span class="score ${scoreClass(latest?.score ?? null)}">${latest?.score == null ? "?" : Math.round(Number(latest.score))}</span>
      <span class="grow"><strong>${esc(item.title)}</strong><small>${esc(item.company_name)} · ${esc(item.status)}</small></span>
    </button>`;
  }).join("");
}

function selectJob(id) {
  state.selectedJob = state.jobs.find(item => item.id === id) || null;
  renderJobs(); renderJobDetail();
}

function renderJobDetail() {
  const root = $("#jobDetail"), item = state.selectedJob;
  if (!item) { root.className = "panel detail-panel empty-state"; root.textContent = "从左侧选择一个职位"; return; }
  const analyses = analysesFor(item.id);
  root.className = "panel detail-panel";
  root.innerHTML = `
    <div class="detail-head"><div><p class="eyebrow">${esc(item.platform_key)}</p><h2>${esc(item.title)}</h2><p>${esc(item.company_name)} · 最近采集 ${date(item.last_seen_at)}</p></div><span class="pill ${item.status !== "active" ? "muted" : ""}">${esc(item.status)}</span></div>
    <div class="detail-section"><h3>匹配分析</h3><div class="stack-list">${analyses.length ? analyses.map(result => `
      <div class="list-row"><span class="score ${scoreClass(result.score)}">${result.score === null ? "?" : Math.round(Number(result.score))}</span><span class="grow"><strong>${esc(result.profile_name)}</strong><small>${esc(result.status)} · ${esc(result.hard_status)} · ${date(result.created_at)}</small></span>${analysisPill(result)}</div>`).join("") : '<div class="empty-state">该职位暂无分析</div>'}</div></div>
    <div class="detail-section"><h3>采集快照</h3><pre id="jobSnapshot"></pre></div>`;
  $("#jobSnapshot").textContent = JSON.stringify(item.snapshot?.normalized || {}, null, 2);
}

function renderAll() { renderRecentAnalyses(); renderProfiles(); renderPolicies(); renderJobs(); renderProfileDetail(); renderJobDetail(); }

async function filterAnalyses() {
  try {
    const value = $("#analysisProfileFilter").value;
    state.analyses = (await api(`/analyses?limit=100${value ? `&profile_id=${encodeURIComponent(value)}` : ""}`)).data.items;
    renderRecentAnalyses(); renderJobs(); renderJobDetail();
  } catch (error) { toast(error.message, true); }
}

function showView(name) {
  $$(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === name));
  $$(".view").forEach(item => item.classList.toggle("active", item.id === `${name}View`));
  $("#pageTitle").textContent = pageNames[name];
  $(".sidebar").classList.remove("open");
}

document.addEventListener("click", event => {
  const nav = event.target.closest("[data-view]"); if (nav) showView(nav.dataset.view);
  const go = event.target.closest("[data-go]"); if (go) showView(go.dataset.go);
  const profile = event.target.closest("[data-profile-id]"); if (profile) selectProfile(profile.dataset.profileId);
  const job = event.target.closest("[data-job-id]"); if (job) selectJob(job.dataset.jobId);
  const analysisJob = event.target.closest("[data-analysis-job]"); if (analysisJob) { showView("jobs"); selectJob(analysisJob.dataset.analysisJob); }
  if (event.target.closest("[data-close-dialog]")) event.target.closest("dialog").close();
});

$("#tokenButton").addEventListener("click", () => { $("#tokenInput").value = state.token; $("#tokenDialog").showModal(); });
$("#refreshButton").addEventListener("click", loadAll);
$("#menuButton").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
$("#newProfileButton").addEventListener("click", () => $("#profileDialog").showModal());
$("#newPolicyButton").addEventListener("click", () => $("#policyDialog").showModal());
$("#analysisProfileFilter").addEventListener("change", filterAnalyses);

$("#tokenForm").addEventListener("submit", async event => {
  event.preventDefault(); state.token = $("#tokenInput").value.trim();
  sessionStorage.setItem("jobAgentToken", state.token); $("#tokenDialog").close(); await loadAll();
});

$("#profileForm").addEventListener("submit", async event => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  try {
    const result = await api("/profiles", {method: "POST", body: JSON.stringify({
      name: form.get("name"), skills: [], experience_years: null, education: {},
      preferred_cities: String(form.get("cities") || "").split(/[,，]/).map(x => x.trim()).filter(Boolean),
      remote_preference: "any", facts: {},
    })});
    $("#profileDialog").close(); event.currentTarget.reset(); await loadAll(); showView("profiles"); await selectProfile(result.data.id); toast("档案已创建");
  } catch (error) { toast(error.message, true); }
});

$("#policyForm").addEventListener("submit", async event => {
  event.preventDefault(); const form = new FormData(event.currentTarget);
  try {
    await api("/policies", {method: "POST", body: JSON.stringify({
      name: form.get("name"), threshold: String(form.get("threshold")), comparison: "gte",
      daily_limit: Number(form.get("dailyLimit")), timezone: "Asia/Shanghai",
      auto_apply: form.get("autoApply") === "on", blocked_companies: [], allowed_platforms: ["boss"],
    })});
    $("#policyDialog").close(); await loadAll(); showView("policies"); toast("策略已创建");
  } catch (error) { toast(error.message, true); }
});

if (state.token) loadAll(); else $("#tokenDialog").showModal();
