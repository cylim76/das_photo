const state = {
  config: null,
  index: null,
  periods: { years: [] },
  periodDefaultsApplied: false,
  groups: [],
  summary: null,
  images: [],
  currentIndex: -1,
  currentContainer: null,
  currentImage: null,
  annotations: [],
  selectedAnnotationId: null,
  selectedAnnotationIds: [],
  drawing: null,
  editing: null,
  selectionBox: null,
  pointer: null,
  imageRect: null,
  isRendering: false,
  saveTimer: null,
  pendingSaveSnapshot: null,
  saveSeq: 0,
  labelConfig: null,
  currentUser: "",
  selectedLabelId: "",
  advancedFilterReady: false,
  zoom: 1,
  panX: 0,
  panY: 0,
  filmstripHeight: 104,
  resizingFilmstrip: null,
  draggingTaskOverlay: null,
  batchMode: false,
  selectedBatchKeys: new Set(),
  batchLastIndex: -1,
  thumbSize: 62,
  longPressTimer: null,
  longPressTriggered: false,
};

const $ = (id) => document.getElementById(id);
const BASE = "/labeler";
const TASK_STATUSES = ["pending", "annotated", "no_target", "excluded"];
const REVIEW_STATUSES = ["unreviewed", "reviewed"];
const CROSSHAIR_LENGTH = 160;
const MIN_ZOOM = 1;
const MAX_ZOOM = 6;
const MIN_FILMSTRIP_HEIGHT = 80;
const MIN_WORKSPACE_HEIGHT = 120;
const TASK_SHORTCUT_KEYS = "abdefghijklmnopqrstuvwy".split("");
const DEFAULT_LABEL_CONFIG = {
  training_tasks: [
    { id: "1", name: "箱号识别", visible: true, sort: 10 },
    { id: "2", name: "铅封号识别", visible: true, sort: 20 },
    { id: "3", name: "货物计数", visible: true, sort: 30 },
  ],
  label_types: [
    { id: "1", name: "箱号", shortcut: "1", task_ids: ["1"], visible: true, sort: 10 },
    { id: "2", name: "铅封号", shortcut: "2", task_ids: ["2"], visible: true, sort: 20 },
    { id: "3", name: "铅封本体", shortcut: "3", task_ids: ["2"], visible: true, sort: 30 },
    { id: "4", name: "箱门", shortcut: "4", task_ids: [], visible: true, sort: 40 },
    { id: "5", name: "货物", shortcut: "5", task_ids: ["3"], visible: true, sort: 50 },
    { id: "6", name: "破损", shortcut: "6", task_ids: [], visible: true, sort: 60 },
  ],
};
const api = async (url, options = {}) => {
  const res = await fetch(`${BASE}${url}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const text = await res.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    throw new Error(`接口未返回 JSON：${url}，请重启工具后刷新页面`);
  }
  if (!res.ok || data.error) throw new Error(data.error || res.statusText);
  return data;
};

function mediaUrl(datasetPath) {
  return `${BASE}/media?path=${encodeURIComponent(datasetPath)}`;
}

function setLog(message) {
  $("settingsLog").textContent = typeof message === "string" ? message : JSON.stringify(message, null, 2);
}

function showToast(text, mode = "success", timeout = null) {
  const host = $("toastHost");
  if (!host || !text) return;
  const item = document.createElement("div");
  item.className = `toast ${mode || "success"}`;
  item.textContent = text;
  host.appendChild(item);
  const delay = timeout || (mode === "error" ? 2600 : mode === "warn" ? 2200 : 1200);
  window.setTimeout(() => {
    item.classList.add("leaving");
    item.addEventListener("animationend", () => item.remove(), { once: true });
  }, delay);
}

function setSaveStatus(text, mode = "") {
  const el = $("saveStatus");
  if (el) {
    el.textContent = text;
    el.className = `save-status ${mode}`.trim();
  }
  if (mode !== "saving" && text) {
    showToast(text, mode || "success");
  }
}

function showConfirmDialog({ title, message, okText = "确认", cancelText = "取消", danger = false }) {
  const overlay = $("confirmOverlay");
  const titleEl = $("confirmTitle");
  const bodyEl = $("confirmBody");
  const okBtn = $("confirmOkBtn");
  const cancelBtn = $("confirmCancelBtn");
  if (!overlay || !titleEl || !bodyEl || !okBtn || !cancelBtn) return Promise.resolve(false);
  titleEl.textContent = title || "确认操作";
  bodyEl.textContent = message || "";
  okBtn.textContent = okText;
  cancelBtn.textContent = cancelText;
  okBtn.className = danger ? "danger" : "primary";
  overlay.hidden = false;
  return new Promise((resolve) => {
    const finish = (result) => {
      overlay.hidden = true;
      okBtn.onclick = null;
      cancelBtn.onclick = null;
      overlay.onclick = null;
      document.removeEventListener("keydown", onKeyDown);
      resolve(result);
    };
    const onKeyDown = (event) => {
      if (event.key === "Escape") finish(false);
    };
    okBtn.onclick = () => finish(true);
    cancelBtn.onclick = () => finish(false);
    overlay.onclick = (event) => {
      if (event.target === overlay) finish(false);
    };
    document.addEventListener("keydown", onKeyDown);
    okBtn.focus();
  });
}

function setView(view) {
  document.querySelectorAll(".tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.view === view));
  $("workbenchView").classList.toggle("active", view === "workbench");
  $("settingsView").classList.toggle("active", view === "settings");
}

function maxFilmstripHeight() {
  const workbench = $("workbenchView");
  const controls = document.querySelector(".current-controls");
  const viewHeight = workbench?.clientHeight || window.innerHeight;
  const controlsHeight = controls?.offsetHeight || 0;
  const available = viewHeight - controlsHeight - MIN_WORKSPACE_HEIGHT - 8;
  return Math.max(180, Math.floor(available));
}

function updateFilmstripToggleIcon() {
  const btn = $("filmstripToggleBtn");
  if (!btn) return;
  btn.textContent = state.filmstripHeight >= maxFilmstripHeight() - 4 ? "▾" : "▴";
  btn.title = state.filmstripHeight >= maxFilmstripHeight() - 4 ? "收起照片列表" : "展开照片列表";
}

function clampFilmstripHeight(value) {
  return Math.max(MIN_FILMSTRIP_HEIGHT, Math.min(maxFilmstripHeight(), Math.round(value)));
}

function setFilmstripHeight(value) {
  state.filmstripHeight = clampFilmstripHeight(value);
  document.documentElement.style.setProperty("--filmstrip-height", `${state.filmstripHeight}px`);
  updateFilmstripToggleIcon();
  requestAnimationFrame(updateCanvasSize);
}

function toggleFilmstripHeight() {
  const minHeight = MIN_FILMSTRIP_HEIGHT;
  const maxHeight = maxFilmstripHeight();
  const isExpanded = state.filmstripHeight >= maxHeight - 4;
  setFilmstripHeight(isExpanded ? minHeight : maxHeight);
}

function setThumbSize(value) {
  state.thumbSize = Math.max(48, Math.min(180, Number(value) || 62));
  document.documentElement.style.setProperty("--thumb-size", `${state.thumbSize}px`);
}

function batchKey(img) {
  return imageKey(img.container_no, img.file_name);
}

function batchItems() {
  return state.images
    .filter((img) => state.selectedBatchKeys.has(batchKey(img)))
    .map((img) => ({ container_no: img.container_no, file_name: img.file_name }));
}

function renderBatchPurposeOptions() {
  const select = $("batchPurposeSelect");
  if (!select) return;
  const current = select.value || $("purposeFilter")?.value || "";
  select.innerHTML = '<option value="">选择训练任务</option>';
  trainingTasks({ visibleOnly: true }).forEach((task) => {
    const option = document.createElement("option");
    option.value = task.id;
    option.textContent = task.name;
    select.appendChild(option);
  });
  select.value = trainingTaskIds().includes(current) ? current : "";
}

function renderResetTaskOptions() {
  const select = $("resetTaskSelect");
  if (!select) return;
  const current = select.value || $("purposeFilter")?.value || "";
  select.innerHTML = '<option value="">选择要重置的任务</option>';
  trainingTasks({ visibleOnly: true }).forEach((task) => {
    const option = document.createElement("option");
    option.value = task.id;
    option.textContent = task.name;
    select.appendChild(option);
  });
  select.value = trainingTaskIds().includes(current) ? current : "";
}

function updateBatchToolbar() {
  const enabled = state.batchMode;
  $("filmstripTools").classList.toggle("active", enabled);
  $("batchModeBtn").classList.toggle("active", enabled);
  $("batchModeBtn").textContent = enabled ? "退出批量" : "批量";
  $("batchSelectedCount").hidden = !enabled;
  $("batchPurposeSelect").hidden = !enabled;
  $("batchPendingBtn").hidden = !enabled;
  $("batchNoTargetBtn").hidden = !enabled;
  $("batchExcludeBtn").hidden = !enabled;
  $("batchUnassignBtn").hidden = !enabled;
  $("batchClearBtn").hidden = !enabled;
  $("batchSelectedCount").textContent = `已选 ${state.selectedBatchKeys.size} 张`;
}

function clearBatchSelection() {
  state.selectedBatchKeys.clear();
  state.batchLastIndex = -1;
  updateBatchToolbar();
  renderImageList();
}

function enterBatchMode(index) {
  state.batchMode = true;
  state.longPressTriggered = true;
  state.selectedBatchKeys.add(batchKey(state.images[index]));
  state.batchLastIndex = index;
  updateBatchToolbar();
  renderImageList();
}

function exitBatchMode() {
  state.batchMode = false;
  clearBatchSelection();
  updateBatchToolbar();
  renderImageList();
}

function toggleBatchSelection(index, event = {}) {
  const img = state.images[index];
  if (!img) return;
  if (event.shiftKey && state.batchLastIndex >= 0) {
    const start = Math.min(state.batchLastIndex, index);
    const end = Math.max(state.batchLastIndex, index);
    for (let i = start; i <= end; i += 1) {
      state.selectedBatchKeys.add(batchKey(state.images[i]));
    }
  } else {
    const key = batchKey(img);
    if (state.selectedBatchKeys.has(key)) {
      state.selectedBatchKeys.delete(key);
    } else {
      state.selectedBatchKeys.add(key);
    }
    state.batchLastIndex = index;
  }
  updateBatchToolbar();
  renderImageList();
}

async function applyBatchStatus(status) {
  const purpose = $("batchPurposeSelect").value || $("purposeFilter")?.value || "";
  const items = batchItems();
  if (!state.batchMode || !items.length) {
    setSaveStatus("请先批量选择照片", "warn");
    return;
  }
  if (!purpose) {
    setSaveStatus("请选择训练任务", "warn");
    return;
  }
  setSaveStatus("批量保存中...", "saving");
  const result = await api("/api/images/batch-status", {
    method: "POST",
    body: JSON.stringify({ items, purpose, status }),
  });
  setSaveStatus(`批量已保存 ${result.updated_count || 0} 张`);
  clearBatchSelection();
  await loadImages({ autoSelect: false });
}

async function unassignBatchPurpose() {
  const purpose = $("batchPurposeSelect").value || $("purposeFilter")?.value || "";
  const items = batchItems();
  if (!state.batchMode || !items.length) {
    setSaveStatus("请先批量选择照片", "warn");
    return;
  }
  if (!purpose) {
    setSaveStatus("请选择训练任务", "warn");
    return;
  }
  const confirmed = await showConfirmDialog({
    title: "确认设为未分配",
    message: `将把选中的 ${items.length} 张照片从「${taskName(purpose)}」任务中移出。\n\n如果这些照片在该任务下已有标签框，相关标签框也会一起删除。`,
    okText: "确认移出",
  });
  if (!confirmed) return;
  setSaveStatus("批量移出中...", "saving");
  const result = await api("/api/images/batch-status", {
    method: "POST",
    body: JSON.stringify({ items, purpose, action: "unassign" }),
  });
  setSaveStatus(`已移出 ${result.updated_count || 0} 张`);
  clearBatchSelection();
  await loadImages({ autoSelect: false });
}

function loadTaskOverlayPosition() {
  try {
    return JSON.parse(localStorage.getItem("taskOverlayPosition") || "null");
  } catch (err) {
    return null;
  }
}

function saveTaskOverlayPosition(position) {
  localStorage.setItem("taskOverlayPosition", JSON.stringify(position));
}

function applyTaskOverlayPosition() {
  const overlay = $("taskOverlay");
  if (!overlay) return;
  const position = loadTaskOverlayPosition();
  if (!position) return;
  overlay.style.left = `${position.left}px`;
  overlay.style.top = `${position.top}px`;
}

function labelConfig() {
  return state.labelConfig || DEFAULT_LABEL_CONFIG;
}

function sortedItems(items) {
  return [...(items || [])].sort((a, b) => (a.sort || 0) - (b.sort || 0) || String(a.id).localeCompare(String(b.id)));
}

function trainingTasks({ visibleOnly = false } = {}) {
  const items = sortedItems(labelConfig().training_tasks);
  return visibleOnly ? items.filter((item) => item.visible !== false) : items;
}

function labelTypes({ visibleOnly = false } = {}) {
  const items = sortedItems(labelConfig().label_types);
  return visibleOnly ? items.filter((item) => item.visible !== false) : items;
}

function trainingTaskIds() {
  return trainingTasks().map((task) => task.id);
}

function taskName(id) {
  return trainingTasks().find((task) => task.id === id)?.name || id || "";
}

function shortcutText(shortcut) {
  return shortcut ? `(${shortcut.toUpperCase()})` : "";
}

function labelName(id) {
  return labelTypes().find((label) => label.id === id)?.name || id || "";
}

function labelType(id) {
  return labelTypes().find((label) => label.id === id) || null;
}

function valueAtPath(source, path) {
  return String(path || "").split(".").reduce((value, key) => (
    value && Object.prototype.hasOwnProperty.call(value, key) ? value[key] : ""
  ), source);
}

function defaultTextForClass(classId) {
  const label = labelType(classId);
  const info = state.currentContainer?.container_info || {};
  const source = {
    container_no: state.currentContainer?.container_no || state.currentImage?.container_no || "",
    seal_no: info.seal_no || state.currentImage?.seal_no || "",
    container_info: info,
    image: state.currentImage || {},
  };
  const configured = label?.text_source ? valueAtPath(source, label.text_source) : "";
  if (configured) return String(configured);
  if (label?.key === "container_no") return String(source.container_no || "");
  if (label?.key === "seal_no") return String(source.seal_no || "");
  return "";
}

function normalizeId(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function escapeAttr(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function loadConfig() {
  state.config = await api("/api/config");
  $("photoRootInput").value = state.config.photo_root;
}

async function loadSession() {
  try {
    const data = await api("/api/session");
    state.currentUser = data.user || "";
  } catch (err) {
    state.currentUser = "";
  }
  const el = $("currentUserMeta");
  if (el) el.textContent = state.currentUser ? `用户 ${state.currentUser}` : "";
}

async function loadLabelConfig() {
  state.labelConfig = await api("/api/label-config");
  renderLabelConfigControls();
  renderLabelSettings();
}

async function loadIndex() {
  state.index = await api("/api/index");
  $("datasetSummary").textContent = `${state.index.container_count || 0}箱/${state.index.image_count || 0}图`;
}

async function loadImages(options = {}) {
  const autoSelect = options.autoSelect !== false;
  const params = new URLSearchParams();
  const year = $("yearFilter").value;
  const month = $("monthFilter").value;
  const stage = $("stageFilter").value;
  const purpose = $("purposeFilter").value;
  const status = $("statusFilter").value;
  const reviewStatus = $("reviewFilter")?.value || "";
  const groupSize = $("groupSizeFilter")?.value || "";
  const group = $("groupFilter")?.value || "";
  const effectiveGroup = groupSize ? (group || "1") : "";
  const search = $("searchInput").value.trim();
  if (year) params.set("year", year);
  if (month) params.set("month", month);
  if (stage) params.set("stage", stage);
  if (purpose) params.set("purpose", purpose);
  if (status) params.set("status", status);
  if (reviewStatus) params.set("review_status", reviewStatus);
  if (groupSize) params.set("group_size", groupSize);
  if (groupSize && effectiveGroup) params.set("group", effectiveGroup);
  if (search) params.set("search", search);
  const advancedFilters = collectAdvancedFilters();
  if (advancedFilters.length) params.set("advanced", JSON.stringify(advancedFilters));
  params.set("limit", "500");
  const data = await api(`/api/images?${params.toString()}`);
  const returnedImages = data.images || [];
  state.images = stage
    ? returnedImages.filter((img) => String(img.stage || "") === stage)
    : returnedImages;
  state.selectedBatchKeys = new Set([...state.selectedBatchKeys].filter((key) => (
    state.images.some((img) => batchKey(img) === key)
  )));
  state.summary = data.summary || null;
  state.groups = state.summary?.groups || [];
  if (stage && state.summary) {
    const seen = new Set();
    const containers = [];
    state.images.forEach((img) => {
      if (seen.has(img.container_no)) return;
      seen.add(img.container_no);
      containers.push({ container_no: img.container_no, matched_images: 0 });
    });
    containers.forEach((row) => {
      row.matched_images = state.images.filter((img) => img.container_no === row.container_no).length;
    });
    state.summary = {
      ...state.summary,
      stage,
      image_count: state.images.length,
      container_count: containers.length,
      containers,
    };
  }
  state.periods = data.periods || state.periods;
  const periodDefaultsChanged = renderPeriodFilters();
  if (periodDefaultsChanged) {
    state.currentIndex = -1;
    await loadImages(options);
    return;
  }
  renderGroupFilters();
  renderProgress();
  renderImageList();
  updateBatchToolbar();
  if (autoSelect && state.images.length && state.currentIndex < 0) {
    selectImage(0);
  } else if (!state.images.length) {
    state.currentIndex = -1;
    state.currentContainer = null;
    state.currentImage = null;
  }
}

function renderPeriodFilters() {
  const years = state.periods.years || [];
  const yearSelect = $("yearFilter");
  const monthSelect = $("monthFilter");
  const currentYear = yearSelect.value;
  const currentMonth = monthSelect.value;
  const shouldApplyDefaults = !state.periodDefaultsApplied && !currentYear && !currentMonth && years.length;
  const latestYear = years[years.length - 1]?.year || "";

  yearSelect.innerHTML = '<option value="">全部</option>';
  years.forEach((row) => {
    const option = document.createElement("option");
    option.value = row.year;
    option.textContent = row.year;
    yearSelect.appendChild(option);
  });
  yearSelect.value = shouldApplyDefaults
    ? latestYear
    : years.some((row) => row.year === currentYear) ? currentYear : "";

  const selectedYear = yearSelect.value;
  const months = selectedYear
    ? (years.find((row) => row.year === selectedYear)?.months || [])
    : [...new Set(years.flatMap((row) => row.months || []))].sort();
  const latestMonth = months[months.length - 1] || "";

  monthSelect.innerHTML = '<option value="">全部</option>';
  months.forEach((month) => {
    const option = document.createElement("option");
    option.value = month;
    option.textContent = month;
    monthSelect.appendChild(option);
  });
  monthSelect.value = shouldApplyDefaults
    ? latestMonth
    : months.includes(currentMonth) ? currentMonth : "";
  if (shouldApplyDefaults) {
    state.periodDefaultsApplied = true;
  }
  return shouldApplyDefaults;
}

function renderGroupFilters() {
  const groupSizeSelect = $("groupSizeFilter");
  const groupSelect = $("groupFilter");
  if (!groupSizeSelect || !groupSelect) return;
  const current = groupSelect.value;
  const groups = state.groups || [];
  groupSelect.innerHTML = '<option value="">全部组</option>';
  groups.forEach((group) => {
    const option = document.createElement("option");
    option.value = String(group.index);
    option.textContent = group.label;
    groupSelect.appendChild(option);
  });
  groupSelect.disabled = !groupSizeSelect.value || !groups.length;
  groupSelect.value = groups.some((group) => String(group.index) === current)
    ? current
    : groupSizeSelect.value && groups.length ? String(groups[0].index) : "";
}

function bindPurposeCheckboxes() {
  document.querySelectorAll(".purposeBox").forEach((box) => {
    box.onchange = () => setActivePurpose(box.value, true);
  });
  document.querySelectorAll(".purpose-chip").forEach((chip) => {
    chip.onclick = (event) => {
      if (event.target.closest(".remove-purpose")) return;
      setActivePurpose(chip.dataset.purposeId, true);
    };
  });
  document.querySelectorAll(".remove-purpose").forEach((btn) => {
    btn.onclick = (event) => {
      event.stopPropagation();
      removeTrainingPurposeWithConfirm(btn.dataset.purposeId, true);
    };
  });
}

function bindClassRadios() {
  document.querySelectorAll("input[name='classRadio']").forEach((radio) => {
    radio.onchange = () => setClass(radio.value);
  });
}

function ensureStatusFilterOptions() {
  const filter = $("statusFilter");
  if (!filter) return;
  const hint = "\u672a\u5206\u914d\uff1a\u9009\u62e9\u4e86\u8bad\u7ec3\u4efb\u52a1\u65f6\uff0c\u8868\u793a\u672a\u5206\u914d\u7ed9\u5f53\u524d\u4efb\u52a1\uff1b\u5168\u90e8\u4efb\u52a1\u65f6\uff0c\u8868\u793a\u672a\u5206\u914d\u7ed9\u4efb\u4f55\u4efb\u52a1\u3002";
  filter.title = hint;
  if ([...filter.options].some((option) => option.value === "unassigned")) {
    const option = filter.querySelector("option[value='unassigned']");
    option.textContent = "\u672a\u5206\u914d";
    option.title = hint;
    return;
  }
  const option = document.createElement("option");
  option.value = "unassigned";
  option.title = hint;
  option.textContent = "\u672a\u5206\u914d";
  filter.insertBefore(option, filter.options[1] || null);
}

function optionHtml(value, text, selected = false) {
  return `<option value="${escapeAttr(value)}" ${selected ? "selected" : ""}>${escapeAttr(text)}</option>`;
}

function taskSelectOptions(selected = "") {
  return trainingTasks({ visibleOnly: true })
    .map((task) => optionHtml(task.id, task.name, task.id === selected))
    .join("");
}

function labelSelectOptions(selected = "") {
  return labelTypes({ visibleOnly: true })
    .map((label) => optionHtml(label.id, label.name, label.id === selected))
    .join("");
}

function statusSelectOptions(selected = "pending") {
  const statuses = [
    ["pending", "\u672a\u5904\u7406"],
    ["annotated", "\u5df2\u6807\u6ce8"],
    ["no_target", "\u65e0\u76ee\u6807"],
    ["excluded", "\u6392\u9664"],
    ["unassigned", "\u672a\u5206\u914d"],
  ];
  return statuses.map(([value, text]) => optionHtml(value, text, value === selected)).join("");
}

function reviewStatusSelectOptions(selected = "unreviewed") {
  const statuses = [
    ["unreviewed", "\u672a\u590d\u6838"],
    ["reviewed", "\u5df2\u590d\u6838"],
  ];
  return statuses.map(([value, text]) => optionHtml(value, text, value === selected)).join("");
}

function advancedOpOptions(field, selected = "") {
  const ops = field === "task_status" || field === "review_status"
    ? [["eq", "\u7b49\u4e8e"], ["ne", "\u4e0d\u7b49\u4e8e"]]
    : [["include", "\u5305\u542b"], ["exclude", "\u4e0d\u5305\u542b"]];
  const active = selected || ops[0][0];
  return ops.map(([value, text]) => optionHtml(value, text, value === active)).join("");
}

function renderAdvancedFilterRow(row) {
  const field = row.querySelector(".advanced-field").value;
  const op = row.querySelector(".advanced-op").value;
  const value = row.querySelector(".advanced-value")?.value || "";
  const status = row.querySelector(".advanced-status")?.value || "pending";
  row.querySelector(".advanced-op").innerHTML = advancedOpOptions(field, op);
  const valueCell = row.querySelector(".advanced-value-cell");
  if (field === "label") {
    valueCell.innerHTML = `
      <label>\u6807\u7b7e\u7c7b\u578b
        <select class="advanced-value">${labelSelectOptions(value)}</select>
      </label>
      <label>\u72b6\u6001
        <select class="advanced-status" disabled>
          <option value="">不适用</option>
        </select>
      </label>
    `;
    return;
  }
  if (field === "task_status") {
    valueCell.innerHTML = `
      <label>\u8bad\u7ec3\u4efb\u52a1
        <select class="advanced-value">${taskSelectOptions(value)}</select>
      </label>
      <label>\u72b6\u6001
        <select class="advanced-status">${statusSelectOptions(status)}</select>
      </label>
    `;
    return;
  }
  if (field === "review_status") {
    valueCell.innerHTML = `
      <label>\u8bad\u7ec3\u4efb\u52a1
        <select class="advanced-value">${taskSelectOptions(value)}</select>
      </label>
      <label>\u590d\u6838
        <select class="advanced-status">${reviewStatusSelectOptions(status || "unreviewed")}</select>
      </label>
    `;
    return;
  }
  valueCell.innerHTML = `
    <label>\u8bad\u7ec3\u4efb\u52a1
      <select class="advanced-value">${taskSelectOptions(value)}</select>
    </label>
    <label>\u72b6\u6001
      <select class="advanced-status" disabled>
        <option value="">不适用</option>
      </select>
    </label>
  `;
}

function addAdvancedFilterRow(rule = {}) {
  const rows = $("advancedFilterRows");
  if (!rows) return;
  const row = document.createElement("div");
  row.className = "advanced-filter-row";
  row.innerHTML = `
    <label>\u6761\u4ef6
      <select class="advanced-field">
        ${optionHtml("task", "\u8bad\u7ec3\u4efb\u52a1", rule.field === "task")}
        ${optionHtml("task_status", "\u4efb\u52a1\u72b6\u6001", rule.field === "task_status")}
        ${optionHtml("review_status", "\u590d\u6838\u72b6\u6001", rule.field === "review_status")}
        ${optionHtml("label", "\u6807\u7b7e\u7c7b\u578b", rule.field === "label")}
      </select>
    </label>
    <label>\u5339\u914d
      <select class="advanced-op"></select>
    </label>
    <div class="advanced-value-cell"></div>
    <button class="remove-advanced-filter" type="button">\u5220\u9664</button>
  `;
  rows.appendChild(row);
  row.querySelector(".advanced-field").value = rule.field || "task";
  row.querySelector(".advanced-op").value = rule.op || "";
  renderAdvancedFilterRow(row);
  if (rule.value) row.querySelector(".advanced-value").value = rule.value;
  if (rule.status && row.querySelector(".advanced-status")) row.querySelector(".advanced-status").value = rule.status;
  row.addEventListener("change", (event) => {
    if (event.target.classList.contains("advanced-field")) renderAdvancedFilterRow(row);
    state.currentIndex = -1;
    loadImages();
  });
  row.querySelector(".remove-advanced-filter").onclick = () => {
    row.remove();
    state.currentIndex = -1;
    loadImages();
  };
}

function collectAdvancedFilters() {
  return [...document.querySelectorAll(".advanced-filter-row")].map((row) => {
    const field = row.querySelector(".advanced-field")?.value || "";
    const op = row.querySelector(".advanced-op")?.value || "";
    const value = row.querySelector(".advanced-value")?.value || "";
    const status = row.querySelector(".advanced-status")?.value || "";
    return { field, op, value, status };
  }).filter((rule) => rule.field && rule.op && rule.value);
}

function collectCurrentFilterPayload() {
  const filters = {
    year: $("yearFilter").value,
    month: $("monthFilter").value,
    stage: $("stageFilter").value,
    purpose: $("purposeFilter").value,
    status: $("statusFilter").value,
    review_status: $("reviewFilter")?.value || "",
    group_size: $("groupSizeFilter")?.value || "",
    group: $("groupFilter")?.value || "",
    search: $("searchInput").value.trim(),
  };
  const advanced = collectAdvancedFilters();
  if (advanced.length) filters.advanced = JSON.stringify(advanced);
  return filters;
}

async function applyTaskReset(action) {
  const purpose = $("resetTaskSelect").value || $("purposeFilter").value || "";
  if (!purpose) {
    setSaveStatus("请选择要重置的训练任务", "warn");
    return;
  }
  const task = taskName(purpose);
  const count = state.summary?.image_count || state.images.length || 0;
  const isRemove = action === "remove_task";
  const confirmed = await showConfirmDialog({
    title: isRemove ? "确认移除任务" : "确认批量重置",
    message: isRemove
      ? `将把当前筛选范围内的 ${count} 张照片从「${task}」任务中移出。\n\n这些照片在该任务下的标签框也会一起删除。此操作会直接修改照片目录里的 JSON 文件。`
      : `将把当前筛选范围内的 ${count} 张照片，在「${task}」任务下重置为「未处理」。\n\n已有标签框不会删除，但复核状态会回到未复核。`,
    okText: isRemove ? "确认移除" : "确认重置",
    danger: isRemove,
  });
  if (!confirmed) return;
  setSaveStatus("任务重置中...", "saving");
  const result = await api("/api/task-reset", {
    method: "POST",
    body: JSON.stringify({
      purpose,
      action,
      filters: collectCurrentFilterPayload(),
    }),
  });
  setSaveStatus(`任务重置完成 ${result.updated_count || 0} 张`);
  state.currentIndex = -1;
  clearBatchSelection();
  await loadIndex();
  await loadImages();
}

function createAdvancedFilterUi() {
  const toggle = $("advancedFilterToggle");
  const filters = document.querySelector(".filters");
  if (!toggle || !filters || $("advancedFilterPanel")) return;
  const panel = document.createElement("section");
  panel.id = "advancedFilterPanel";
  panel.className = "advanced-filter-panel";
  panel.hidden = true;
  panel.innerHTML = `
    <div class="advanced-filter-head">
      <span class="advanced-filter-title">\u9ad8\u7ea7\u7b5b\u9009</span>
      <div class="advanced-filter-actions">
        <button id="addAdvancedFilterBtn" type="button">\u6dfb\u52a0\u6761\u4ef6</button>
        <button id="clearAdvancedFilterBtn" type="button">\u6e05\u7a7a</button>
      </div>
    </div>
    <div id="advancedFilterRows" class="advanced-filter-rows"></div>
    <div class="task-reset-panel">
      <span class="advanced-filter-title">任务重置</span>
      <select id="resetTaskSelect"></select>
      <button id="resetTaskPendingBtn" type="button">当前范围重置为未处理</button>
      <button id="removeTaskBtn" type="button">当前范围移除任务</button>
    </div>
  `;
  filters.appendChild(panel);
  toggle.onclick = () => {
    panel.hidden = !panel.hidden;
    toggle.classList.toggle("active", !panel.hidden);
    if (!panel.hidden && !panel.querySelector(".advanced-filter-row")) addAdvancedFilterRow();
  };
  panel.querySelector("#addAdvancedFilterBtn").onclick = () => addAdvancedFilterRow();
  panel.querySelector("#clearAdvancedFilterBtn").onclick = () => {
    $("advancedFilterRows").innerHTML = "";
    state.currentIndex = -1;
    loadImages();
  };
  panel.querySelector("#resetTaskPendingBtn").onclick = () => applyTaskReset("reset_pending").catch((err) => setSaveStatus(err.message, "error"));
  panel.querySelector("#removeTaskBtn").onclick = () => applyTaskReset("remove_task").catch((err) => setSaveStatus(err.message, "error"));
}

function renderLabelConfigControls() {
  const currentFilter = $("purposeFilter").value;
  const purposeFilter = $("purposeFilter");
  purposeFilter.innerHTML = '<option value="">全部任务</option>';
  trainingTasks({ visibleOnly: true }).forEach((task) => {
    const option = document.createElement("option");
    option.value = task.id;
    option.textContent = task.name;
    purposeFilter.appendChild(option);
  });
  purposeFilter.value = trainingTaskIds().includes(currentFilter) ? currentFilter : "";
  renderBatchPurposeOptions();
  renderResetTaskOptions();

  const purposeChecks = $("purposeChecks");
  purposeChecks.innerHTML = "";
  trainingTasks({ visibleOnly: true }).forEach((task) => {
    const chip = document.createElement("button");
    chip.className = "purpose-chip";
    chip.type = "button";
    chip.dataset.purposeId = task.id;
    const shortcut = shortcutText(task.shortcut || "");
    chip.innerHTML = `
      <input class="purposeBox" type="checkbox" value="${escapeAttr(task.id)}" tabindex="-1">
      <span>${escapeAttr(task.name)}${escapeAttr(shortcut)}</span>
      <span class="remove-purpose" data-purpose-id="${escapeAttr(task.id)}" title="取消任务">×</span>
    `;
    purposeChecks.appendChild(chip);
  });
  bindPurposeCheckboxes();

  const currentClass = selectedClassId();
  const classRadios = $("classRadios");
  classRadios.innerHTML = "";
  const visibleLabels = labelTypes({ visibleOnly: true });
  visibleLabels.forEach((item, index) => {
    const label = document.createElement("label");
    const shortcut = item.shortcut ? `(${item.shortcut})` : "";
    label.innerHTML = `<input type="radio" name="classRadio" value="${escapeAttr(item.id)}" ${index === 0 ? "checked" : ""}>${escapeAttr(item.name)}${escapeAttr(shortcut)}`;
    classRadios.appendChild(label);
  });
  const activeClass = visibleLabels.some((item) => item.id === currentClass) ? currentClass : visibleLabels[0]?.id || "";
  const activeRadio = activeClass ? document.querySelector(`input[name='classRadio'][value='${activeClass}']`) : null;
  if (activeRadio) activeRadio.checked = true;
  bindClassRadios();
  document.querySelectorAll(".advanced-filter-row").forEach((row) => renderAdvancedFilterRow(row));
  if (state.currentImage) {
    updateActivePurposeControls();
    applyStatusRadios();
    applyReviewRadios();
  }
}

function nextSort(items) {
  return Math.max(0, ...items.map((item) => Number(item.sort) || 0)) + 10;
}

function nextNumericId(items) {
  return String(Math.max(0, ...items.map((item) => Number(item.id) || 0)) + 1);
}

function taskRowHtml(task) {
  return `
    <div class="settings-row task-row" data-task-id="${escapeAttr(task.id)}">
      <div class="readonly-id">ID ${escapeAttr(task.id)}</div>
      <label>名称<input class="task-name" value="${escapeAttr(task.name)}"></label>
      <label>Key<input class="task-key" value="${escapeAttr(task.key || "")}"></label>
      <label>快捷键<input class="task-shortcut" list="taskShortcutOptions" maxlength="1" value="${escapeAttr(task.shortcut || "")}"></label>
      <label>排序<input class="task-sort" type="number" value="${task.sort || 0}"></label>
      <label class="check-inline"><input class="task-visible" type="checkbox" ${task.visible !== false ? "checked" : ""}>显示</label>
      <button class="remove-row" type="button">删除</button>
    </div>
  `;
}

function taskShortcutDatalistHtml() {
  const options = TASK_SHORTCUT_KEYS
    .map((key) => `<option value="${key}">${key.toUpperCase()}</option>`)
    .join("");
  return `<datalist id="taskShortcutOptions">${options}</datalist>`;
}

function labelListItemHtml(item, index) {
  const shortcut = item.shortcut ? ` · ${escapeAttr(item.shortcut)}` : "";
  return `
    <button class="label-list-item ${item.id === state.selectedLabelId ? "active" : ""}" data-label-id="${escapeAttr(item.id)}" type="button">
      <span class="label-list-order">${index + 1}</span>
      <span class="label-list-name">${escapeAttr(item.name)}</span>
      <span class="label-list-meta">${item.visible === false ? "隐藏" : "显示"}${shortcut}</span>
    </button>
  `;
}

function collectLabelConfigFromSettings() {
  const current = labelConfig();
  const tasks = [...document.querySelectorAll(".task-row")].map((row, index) => ({
    id: row.dataset.taskId,
    name: row.querySelector(".task-name").value.trim(),
    key: normalizeId(row.querySelector(".task-key").value.trim()) || row.dataset.taskId,
    shortcut: row.querySelector(".task-shortcut").value.trim().slice(0, 1).toLowerCase(),
    visible: row.querySelector(".task-visible").checked,
    sort: Number(row.querySelector(".task-sort").value) || (index + 1) * 10,
  })).filter((task) => task.id && task.name);
  const labels = sortedItems(current.label_types).map((label, index) => ({
    ...label,
    sort: Number(label.sort) || (index + 1) * 10,
  })).filter((label) => label.id && label.name);
  return { schema_version: 1, training_tasks: tasks, label_types: labels };
}

function renderLabelSettings(config = labelConfig()) {
  const tasks = sortedItems(config.training_tasks);
  const labels = sortedItems(config.label_types);
  const taskList = $("taskSettingsList");
  const labelList = $("labelSettingsList");
  if (!state.selectedLabelId || !labels.some((item) => item.id === state.selectedLabelId)) {
    state.selectedLabelId = labels[0]?.id || "";
  }
  taskList.innerHTML = taskShortcutDatalistHtml() + tasks.map(taskRowHtml).join("");
  labelList.innerHTML = labels.map(labelListItemHtml).join("");
  document.querySelectorAll(".settings-row .remove-row").forEach((btn) => {
    btn.onclick = () => {
      btn.closest(".settings-row").remove();
      state.labelConfig = collectLabelConfigFromSettings();
      renderLabelSettings();
    };
  });
  document.querySelectorAll(".label-list-item").forEach((btn) => {
    btn.onclick = () => {
      state.labelConfig = collectLabelConfigFromSettings();
      state.selectedLabelId = btn.dataset.labelId;
      renderLabelSettings();
    };
  });
  renderLabelDetail(labels.find((item) => item.id === state.selectedLabelId), tasks);
}

function renderLabelDetail(item, tasks) {
  const panel = $("labelDetailPanel");
  if (!item) {
    panel.innerHTML = '<div class="settings-empty">请先新增一个标签。</div>';
    return;
  }
  const taskChecks = tasks.map((task) => `
    <label class="check-inline">
      <input class="detail-label-task" type="checkbox" value="${escapeAttr(task.id)}" ${item.task_ids?.includes(task.id) ? "checked" : ""}>${escapeAttr(task.name)}
    </label>
  `).join("");
  panel.innerHTML = `
    <div class="settings-detail-head">
      <h3>${escapeAttr(item.name)}</h3>
      <div class="settings-detail-actions">
        <button id="moveLabelUpBtn" type="button">上移</button>
        <button id="moveLabelDownBtn" type="button">下移</button>
        <button id="deleteLabelBtn" type="button">删除</button>
      </div>
    </div>
    <div class="settings-detail-form">
      <div class="readonly-id detail-id">ID ${escapeAttr(item.id)}</div>
      <label>名称<input id="labelDetailName" value="${escapeAttr(item.name)}"></label>
      <label>Key<input id="labelDetailKey" value="${escapeAttr(item.key || "")}"></label>
      <label>文字来源<input id="labelDetailTextSource" value="${escapeAttr(item.text_source || "")}"></label>
      <label>快捷键<input id="labelDetailShortcut" maxlength="1" value="${escapeAttr(item.shortcut || "")}"></label>
      <label class="check-inline"><input id="labelDetailVisible" type="checkbox" ${item.visible !== false ? "checked" : ""}>显示</label>
      <div class="task-links detail-task-links">${taskChecks}</div>
    </div>
  `;
  ["labelDetailName", "labelDetailKey", "labelDetailTextSource", "labelDetailShortcut", "labelDetailVisible"].forEach((id) => {
    $(id).oninput = () => updateSelectedLabelFromDetail(false);
    $(id).onchange = () => updateSelectedLabelFromDetail(true);
  });
  document.querySelectorAll(".detail-label-task").forEach((box) => {
    box.onchange = () => updateSelectedLabelFromDetail(true);
  });
  $("moveLabelUpBtn").onclick = () => moveSelectedLabel(-1);
  $("moveLabelDownBtn").onclick = () => moveSelectedLabel(1);
  $("deleteLabelBtn").onclick = deleteSelectedLabel;
}

function updateSelectedLabelFromDetail(shouldRender = false) {
  if (!$("labelDetailName")) return;
  const config = collectLabelConfigFromSettings();
  const labels = sortedItems(config.label_types);
  const item = labels.find((label) => label.id === state.selectedLabelId);
  if (!item) return;
  item.name = $("labelDetailName").value.trim() || item.name;
  item.key = normalizeId($("labelDetailKey").value.trim()) || item.id;
  item.text_source = $("labelDetailTextSource").value.trim();
  item.shortcut = $("labelDetailShortcut").value.trim().slice(0, 1);
  item.visible = $("labelDetailVisible").checked;
  item.task_ids = [...document.querySelectorAll(".detail-label-task")]
    .filter((box) => box.checked)
    .map((box) => box.value);
  state.selectedLabelId = item.id;
  state.labelConfig = { ...config, label_types: labels };
  if (shouldRender) renderLabelSettings();
}

function moveSelectedLabel(delta) {
  const config = collectLabelConfigFromSettings();
  const labels = sortedItems(config.label_types);
  const index = labels.findIndex((label) => label.id === state.selectedLabelId);
  const nextIndex = index + delta;
  if (index < 0 || nextIndex < 0 || nextIndex >= labels.length) return;
  const [item] = labels.splice(index, 1);
  labels.splice(nextIndex, 0, item);
  labels.forEach((label, i) => {
    label.sort = (i + 1) * 10;
  });
  state.labelConfig = { ...config, label_types: labels };
  renderLabelSettings();
}

function deleteSelectedLabel() {
  const config = collectLabelConfigFromSettings();
  const labels = sortedItems(config.label_types).filter((label) => label.id !== state.selectedLabelId);
  state.selectedLabelId = labels[0]?.id || "";
  state.labelConfig = { ...config, label_types: labels };
  renderLabelSettings();
}

function statusCount(name) {
  return state.summary?.status_counts?.[name] || 0;
}

function checkedValues(selector) {
  return [...document.querySelectorAll(selector)]
    .filter((box) => box.checked)
    .map((box) => box.value);
}

function purposesForClass(classId) {
  return labelTypes().find((item) => item.id === classId)?.task_ids || [];
}

function inferredPurposesFromAnnotations(annotations = []) {
  const purposes = [];
  annotations.forEach((ann) => {
    purposesForClass(ann.class_id).forEach((purpose) => {
      if (!purposes.includes(purpose)) purposes.push(purpose);
    });
  });
  return purposes;
}

function hasAnnotationForPurpose(purpose, annotations = state.annotations) {
  return annotations.some((ann) => annotationMatchesPurpose(ann, purpose));
}

function annotationMatchesPurpose(ann, purpose) {
  return !!purpose && (ann.purpose === purpose || purposesForClass(ann.class_id).includes(purpose));
}

function annotationsForPurpose(purpose) {
  return state.annotations.filter((ann) => annotationMatchesPurpose(ann, purpose));
}

function normalizeImageModel(img) {
  if (!img) return;
  img.photo_types = img.photo_types || [];
  const purposes = Array.isArray(img.training_purposes) ? img.training_purposes : [];
  const inferredPurposes = inferredPurposesFromAnnotations(img.annotations || []);
  const validTaskIds = trainingTaskIds();
  const validPurposes = [
    ...new Set([...purposes, ...inferredPurposes].filter((purpose) => validTaskIds.includes(purpose))),
  ];
  const oldStates = img.purpose_states || {};
  img.training_purposes = validPurposes;
  img.purpose_states = {};
  validPurposes.forEach((purpose) => {
    const current = oldStates[purpose];
    const status = typeof current === "string" ? current : current?.status;
    const reviewStatus = typeof current === "object" ? current.review_status : "";
    img.purpose_states[purpose] = {
      status: TASK_STATUSES.includes(status) ? status : hasAnnotationForPurpose(purpose, img.annotations || []) ? "annotated" : "pending",
      review_status: REVIEW_STATUSES.includes(reviewStatus) ? reviewStatus : "unreviewed",
      updated_at: typeof current === "object" ? current.updated_at || null : null,
      reviewed_at: typeof current === "object" ? current.reviewed_at || null : null,
      updated_by: typeof current === "object" ? current.updated_by || null : null,
      reviewed_by: typeof current === "object" ? current.reviewed_by || null : null,
    };
  });
  if (!validPurposes.includes(img.active_purpose)) {
    img.active_purpose = validPurposes[0] || "";
  }
}

function activePurpose() {
  return state.currentImage?.active_purpose || checkedValues(".purposeBox")[0] || $("purposeFilter")?.value || "";
}

function explicitPurpose() {
  if (!state.currentImage) return "";
  const filterPurpose = $("purposeFilter")?.value || "";
  if (filterPurpose) return filterPurpose;
  if (state.currentImage.active_purpose) return state.currentImage.active_purpose;
  const checked = checkedValues(".purposeBox");
  if (checked.length) return checked[0];
  return "";
}

function requirePurposeForStatus() {
  const purpose = explicitPurpose();
  if (purpose) return purpose;
  setSaveStatus("请先选择训练任务", "warn");
  applyStatusRadios();
  return "";
}

function activePurposeStatus() {
  const purpose = activePurpose();
  return purposeStatus(purpose);
}

function purposeStatus(purpose) {
  if (!purpose || !state.currentImage) return "pending";
  const value = state.currentImage.purpose_states?.[purpose];
  if (typeof value === "string") return value;
  return value?.status || "pending";
}

function statusName(status) {
  const names = {
    pending: "未处理",
    annotated: "已标注",
    no_target: "无目标",
    excluded: "排除",
  };
  return names[status] || status || "";
}

function purposeReviewStatus(purpose) {
  if (!purpose || !state.currentImage) return "unreviewed";
  const value = state.currentImage.purpose_states?.[purpose];
  if (typeof value !== "object") return "unreviewed";
  return REVIEW_STATUSES.includes(value?.review_status) ? value.review_status : "unreviewed";
}

function markPurposeUnreviewed(purpose) {
  if (!purpose || !state.currentImage?.purpose_states?.[purpose]) return;
  const stateValue = state.currentImage.purpose_states[purpose];
  if (typeof stateValue !== "object") return;
  stateValue.review_status = "unreviewed";
  stateValue.reviewed_at = null;
  stateValue.reviewed_by = null;
}

function setActivePurpose(value, shouldSave = false) {
  const box = document.querySelector(`.purposeBox[value='${value}']`);
  if (!box || !state.currentImage) return;
  if (!state.currentImage.training_purposes.includes(value)) {
    state.currentImage.training_purposes.push(value);
  }
  const previousState = state.currentImage.purpose_states?.[value];
  const previousStatus = typeof previousState === "string" ? previousState : previousState?.status;
  state.currentImage.purpose_states = state.currentImage.purpose_states || {};
  state.currentImage.purpose_states[value] = {
    status: TASK_STATUSES.includes(previousStatus) ? previousStatus : "pending",
    review_status: REVIEW_STATUSES.includes(previousState?.review_status) ? previousState.review_status : "unreviewed",
    updated_at: typeof previousState === "object" ? previousState.updated_at || null : null,
    reviewed_at: typeof previousState === "object" ? previousState.reviewed_at || null : null,
    updated_by: state.currentUser || (typeof previousState === "object" ? previousState.updated_by || null : null),
    reviewed_by: typeof previousState === "object" ? previousState.reviewed_by || null : null,
  };
  state.currentImage.active_purpose = value;
  box.checked = true;
  updateActivePurposeControls();
  applyStatusRadios();
  applyReviewRadios();
  if (shouldSave) queueAutoSave();
}

function setReviewStatus(value, shouldSave = true) {
  const radio = document.querySelector(`input[name='reviewRadio'][value='${value}']`);
  let purpose = requirePurposeForStatus();
  if (!radio || !state.currentImage || !purpose) return false;
  if (!state.currentImage.training_purposes.includes(purpose)) {
    setActivePurpose(purpose, false);
  }
  if (value === "reviewed" && purposeStatus(purpose) === "pending") {
    setSaveStatus("未处理任务不能复核", "warn");
    applyReviewRadios();
    return false;
  }
  radio.checked = true;
  const previousState = state.currentImage.purpose_states?.[purpose];
  const previousStatus = typeof previousState === "string" ? previousState : previousState?.status;
  state.currentImage.purpose_states = state.currentImage.purpose_states || {};
  state.currentImage.purpose_states[purpose] = {
    status: TASK_STATUSES.includes(previousStatus) ? previousStatus : "pending",
    review_status: value,
    updated_at: typeof previousState === "object" ? previousState.updated_at || null : null,
    reviewed_at: value === "reviewed" ? new Date().toISOString() : null,
    updated_by: typeof previousState === "object" ? previousState.updated_by || null : null,
    reviewed_by: value === "reviewed" ? state.currentUser || "current_user" : null,
  };
  if (shouldSave) queueAutoSave();
  return true;
}

function removeTrainingPurpose(value, shouldSave = false, options = {}) {
  if (!state.currentImage) return false;
  const deleteAnnotations = options.deleteAnnotations !== false;
  const relatedAnnotations = deleteAnnotations ? annotationsForPurpose(value) : [];
  if (relatedAnnotations.length) {
    const relatedIds = new Set(relatedAnnotations.map((ann) => ann.id));
    state.annotations = state.annotations.filter((ann) => !relatedIds.has(ann.id));
    state.selectedAnnotationIds = state.selectedAnnotationIds.filter((id) => !relatedIds.has(id));
    state.selectedAnnotationId = state.selectedAnnotationIds[0] || null;
  }
  state.currentImage.training_purposes = state.currentImage.training_purposes.filter((purpose) => purpose !== value);
  if (state.currentImage.purpose_states) delete state.currentImage.purpose_states[value];
  if (state.currentImage.active_purpose === value) {
    state.currentImage.active_purpose = state.currentImage.training_purposes[0] || "";
  }
  renderAnnotations();
  updateActivePurposeControls();
  applyStatusRadios();
  applyReviewRadios();
  if (shouldSave) queueAutoSave();
  return true;
}

async function removeTrainingPurposeWithConfirm(value, shouldSave = false) {
  const relatedAnnotations = annotationsForPurpose(value);
  if (relatedAnnotations.length) {
    const confirmed = await showConfirmDialog({
      title: "确认取消训练任务",
      message: `取消训练任务「${taskName(value)}」会删除 ${relatedAnnotations.length} 个关联标签框。\n\n确定继续吗？`,
      okText: "确认取消",
      cancelText: "保留",
    });
    if (!confirmed) {
      updateActivePurposeControls();
      applyStatusRadios();
      return false;
    }
  }
  return removeTrainingPurpose(value, shouldSave, { confirmDelete: false });
}

function ensurePurposesForClass(classId) {
  const purposes = purposesForClass(classId);
  purposes.forEach((purpose) => setActivePurpose(purpose, false));
  return purposes;
}

function applyStatusRadios() {
  const status = activePurposeStatus();
  document.querySelectorAll("input[name='statusRadio']").forEach((radio) => {
    radio.checked = radio.value === status;
  });
}

function applyReviewRadios() {
  const status = purposeReviewStatus(activePurpose());
  document.querySelectorAll("input[name='reviewRadio']").forEach((radio) => {
    radio.checked = radio.value === status;
  });
}

function updateActivePurposeControls() {
  if (!state.currentImage) return;
  const active = state.currentImage.active_purpose || "";
  document.querySelectorAll(".purposeBox").forEach((box) => {
    const selected = (state.currentImage.training_purposes || []).includes(box.value);
    const chip = box.closest(".purpose-chip");
    box.checked = selected;
    chip?.classList.toggle("checked", selected);
    chip?.classList.toggle("active-purpose", box.value === active);
  });
  renderTaskOverlay();
}

function renderTaskOverlay() {
  const overlay = $("taskOverlay");
  if (!overlay) return;
  const img = state.currentImage;
  if (!img) {
    overlay.innerHTML = "";
    return;
  }
  const active = img.active_purpose || "";
  const purposes = img.training_purposes || [];
  overlay.innerHTML = purposes.map((purpose) => `
    <span class="task-badge ${purpose === active ? "active" : ""}">
      <span>${escapeAttr(taskName(purpose))}</span>
      <span class="task-badge-status">${escapeAttr(statusName(purposeStatus(purpose)))}</span>
    </span>
  `).join("");
  applyTaskOverlayPosition();
}

function renderProgress() {
  if (!state.summary) {
    $("progressMeta").textContent = "未加载进度";
    return;
  }
  const current = state.currentImage;
  const containerNo = state.currentContainer?.container_no || current?.container_no;
  const containerIndex = containerNo
    ? (state.summary.containers || []).findIndex((row) => row.container_no === containerNo)
    : -1;
  const photoText = state.images.length && state.currentIndex >= 0
    ? `当前列表 ${state.currentIndex + 1}/${state.images.length}`
    : `当前列表 0/${state.images.length}`;
  const containerText = containerIndex >= 0
    ? `当前箱 ${containerIndex + 1}/${state.summary.container_count}`
    : `当前箱 0/${state.summary.container_count}`;
  $("progressMeta").textContent = `${containerText} · ${photoText}`;
}

function currentContainerTitle(img) {
  const info = state.currentContainer?.container_info || {};
  const das = img?.das || {};
  const cpmId = info.cpm_id || info.das_container_id || das.cpm_id || state.currentContainer?.das_container_id || "";
  const containerNo = state.currentContainer?.container_no || img?.container_no || "";
  const sealNo = info.seal_no || img?.seal_no || "";
  const parts = [];
  if (cpmId) parts.push(cpmId);
  if (containerNo) parts.push(containerNo);
  if (sealNo) parts.push(sealNo);
  return parts.join(" - ");
}

function shortPhotoPath(fileName) {
  return String(fileName || "").replace(/\.[^.\\/]+$/, "");
}

function cpmText(img) {
  const cpmId = img?.das_container_id || img?.das?.cpm_id || "";
  return cpmId ? `CPM ${cpmId}` : "CPM -";
}

function imageKey(containerNo, fileName) {
  return `${containerNo || ""}/${fileName || ""}`;
}

function currentImageKey() {
  if (!state.currentContainer || !state.currentImage) return "";
  return imageKey(state.currentContainer.container_no, state.currentImage.file_name);
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value || null));
}

function createSaveSnapshot() {
  if (!state.currentContainer || !state.currentImage) return null;
  const payload = {
    container_no: state.currentContainer.container_no,
    file_name: state.currentImage.file_name,
    image_width: state.currentImage.image_width || $("photo").naturalWidth || null,
    image_height: state.currentImage.image_height || $("photo").naturalHeight || null,
    photo_types: cloneJson(state.currentImage.photo_types || []),
    training_purposes: cloneJson(state.currentImage.training_purposes || []),
    active_purpose: state.currentImage.active_purpose || "",
    purpose_states: cloneJson(state.currentImage.purpose_states || {}),
    annotations: cloneJson(state.annotations || []),
    notes: $("notesInput").value,
    user: state.currentUser || "",
  };
  return {
    key: imageKey(payload.container_no, payload.file_name),
    oldIndex: state.currentIndex,
    payload,
  };
}

function mergeSavedImage(snapshot, saved) {
  const savedKey = imageKey(snapshot.payload.container_no, snapshot.payload.file_name);
  const listIndex = state.images.findIndex((img) => imageKey(img.container_no, img.file_name) === savedKey);
  if (listIndex >= 0) {
    state.images[listIndex] = { ...state.images[listIndex], ...saved };
  }
  if (state.currentContainer?.container_no === snapshot.payload.container_no) {
    const containerIndex = state.currentContainer.images?.findIndex((img) => img.file_name === snapshot.payload.file_name);
    if (containerIndex >= 0) {
      state.currentContainer.images[containerIndex] = saved;
    }
  }
  if (currentImageKey() === savedKey) {
    state.currentImage = saved;
    state.annotations = cloneJson(saved.annotations || []);
    renderCurrent();
    return;
  }
  renderImageList();
}

function renderImageList() {
  const list = $("imageList");
  list.innerHTML = "";
  if (!state.images.length) {
    list.innerHTML = '<div class="item-meta" style="padding:12px">没有匹配的照片。先到设置里扫描目录。</div>';
    return;
  }
  state.images.forEach((img, index) => {
    const btn = document.createElement("button");
    const selected = state.selectedBatchKeys.has(batchKey(img));
    btn.className = `image-item ${index === state.currentIndex ? "active" : ""} ${selected ? "batch-selected" : ""}`;
    btn.onclick = (event) => {
      if (state.longPressTriggered) {
        state.longPressTriggered = false;
        return;
      }
      if (state.batchMode) {
        toggleBatchSelection(index, event);
      } else {
        selectImage(index);
      }
    };
    btn.onpointerdown = () => {
      if (state.batchMode) return;
      clearTimeout(state.longPressTimer);
      state.longPressTriggered = false;
      state.longPressTimer = setTimeout(() => enterBatchMode(index), 450);
    };
    btn.onpointerup = () => clearTimeout(state.longPressTimer);
    btn.onpointerleave = () => clearTimeout(state.longPressTimer);
    btn.onpointercancel = () => clearTimeout(state.longPressTimer);
    btn.innerHTML = `
      <img class="thumb" src="${mediaUrl(img.dataset_path)}" alt="">
      <div>
        <div class="item-title">${img.container_no}</div>
        <div class="item-meta">${cpmText(img)}</div>
        <div class="item-meta">${shortPhotoPath(img.file_name)}</div>
      </div>
    `;
    list.appendChild(btn);
  });
}

async function flushPendingSave() {
  if (!state.pendingSaveSnapshot) return;
  const snapshot = state.pendingSaveSnapshot;
  state.pendingSaveSnapshot = null;
  clearTimeout(state.saveTimer);
  await saveCurrent(snapshot);
}

async function selectImage(index, options = {}) {
  const requestedRow = state.images[index];
  const requestedKey = requestedRow ? imageKey(requestedRow.container_no, requestedRow.file_name) : "";
  if (options.flushSave !== false) {
    await flushPendingSave();
  }
  if (requestedKey) {
    const refreshedIndex = state.images.findIndex((img) => imageKey(img.container_no, img.file_name) === requestedKey);
    if (refreshedIndex >= 0) {
      index = refreshedIndex;
    }
  }
  state.currentIndex = index;
  const row = state.images[index];
  if (!row) return;
  const data = await api(`/api/container?container_no=${encodeURIComponent(row.container_no)}`);
  state.currentContainer = data;
  state.currentImage = data.images.find((img) => img.file_name === row.file_name);
  normalizeImageModel(state.currentImage);
  state.annotations = JSON.parse(JSON.stringify(state.currentImage.annotations || []));
  state.selectedAnnotationId = null;
  state.selectedAnnotationIds = [];
  state.drawing = null;
  state.editing = null;
  state.selectionBox = null;
  resetZoom();
  renderCurrent();
  renderImageList();
}

function renderCurrent() {
  const img = state.currentImage;
  if (!img) return;
  normalizeImageModel(img);
  state.isRendering = true;
  $("currentTitle").textContent = `${currentContainerTitle(img)} / ${img.file_name}`;
  $("currentMeta").textContent = "";
  renderProgress();
  $("emptyState").style.display = "none";
  $("photo").style.display = "block";
  $("photo").src = mediaUrl(img.dataset_path);
  $("notesInput").value = img.notes || "";
  updateActivePurposeControls();
  applyStatusRadios();
  applyReviewRadios();
  renderTaskOverlay();
  renderAnnotations();
  state.isRendering = false;
}

function formatDasMeta(img) {
  const das = img.das || {};
  const parts = [];
  if (das.cpm_id) parts.push(`CPM ${das.cpm_id}`);
  if (das.photo_id) parts.push(`#${das.photo_id}`);
  if (das.photo_label) parts.push(das.photo_label);
  if (img.seal_no) parts.push(`Seal ${img.seal_no}`);
  return parts.length ? parts.join(" · ") : "无 DAS 元数据";
}

function renderAnnotations() {
  const list = $("annotationList");
  list.innerHTML = "";
  state.annotations.forEach((ann, index) => {
    const row = document.createElement("div");
    row.className = `annotation-row ${state.selectedAnnotationIds.includes(ann.id) ? "active" : ""}`;
    row.innerHTML = `
      <div class="annotation-row-head">
        <strong>${index + 1}. ${labelName(ann.class_id)}</strong>
        <button data-index="${index}" type="button">删除</button>
      </div>
      <span>任务：${taskName(ann.purpose)}</span>
      <label class="annotation-text-field">文本
        <input class="annotation-text-input" data-id="${escapeAttr(ann.id)}" value="${escapeAttr(ann.text || "")}" placeholder="标准答案">
      </label>
      <span>x:${Math.round(ann.x)} y:${Math.round(ann.y)} w:${Math.round(ann.width)} h:${Math.round(ann.height)}</span>
    `;
    row.onclick = (event) => {
      if (event.target.closest("button") || event.target.closest("input")) return;
      selectAnnotation(ann.id);
    };
    row.querySelector(".annotation-text-input").oninput = (event) => {
      ann.text = event.target.value.trim();
      markPurposeUnreviewed(ann.purpose);
      applyReviewRadios();
      queueAutoSave(500);
    };
    row.querySelector("button").onclick = () => {
      markPurposeUnreviewed(ann.purpose);
      state.annotations.splice(index, 1);
      if (state.selectedAnnotationId === ann.id) state.selectedAnnotationId = null;
      state.selectedAnnotationIds = state.selectedAnnotationIds.filter((id) => id !== ann.id);
      renderAnnotations();
      refreshStatusAfterAnnotationChange();
      drawOverlay();
      queueAutoSave();
    };
    list.appendChild(row);
  });
  drawOverlay();
}

function selectedAnnotation() {
  return state.annotations.find((ann) => ann.id === state.selectedAnnotationId) || null;
}

function selectAnnotation(id) {
  state.selectedAnnotationId = id;
  state.selectedAnnotationIds = id ? [id] : [];
  const ann = selectedAnnotation();
  if (ann) {
    const classRadio = document.querySelector(`input[name='classRadio'][value='${ann.class_id}']`);
    if (classRadio) classRadio.checked = true;
    if (ann.purpose) setActivePurpose(ann.purpose);
  }
  renderAnnotations();
  drawOverlay();
}

function selectAnnotations(ids) {
  state.selectedAnnotationIds = [...new Set(ids)];
  state.selectedAnnotationId = state.selectedAnnotationIds[0] || null;
  const ann = selectedAnnotation();
  if (ann) {
    const classRadio = document.querySelector(`input[name='classRadio'][value='${ann.class_id}']`);
    if (classRadio) classRadio.checked = true;
  }
  renderAnnotations();
  drawOverlay();
}

function deleteSelectedAnnotation() {
  if (!state.selectedAnnotationIds.length) return false;
  const selected = new Set(state.selectedAnnotationIds);
  state.annotations.forEach((ann) => {
    if (selected.has(ann.id)) markPurposeUnreviewed(ann.purpose);
  });
  const before = state.annotations.length;
  state.annotations = state.annotations.filter((ann) => !selected.has(ann.id));
  if (state.annotations.length === before) return false;
  state.selectedAnnotationId = null;
  state.selectedAnnotationIds = [];
  renderAnnotations();
  refreshStatusAfterAnnotationChange();
  queueAutoSave();
  return true;
}

function setStatus(value, shouldSave = true) {
  const radio = document.querySelector(`input[name='statusRadio'][value='${value}']`);
  let purpose = requirePurposeForStatus();
  if (!radio || !state.currentImage || !purpose) return false;
  if (!state.currentImage.training_purposes.includes(purpose)) {
    setActivePurpose(purpose, false);
  }
  radio.checked = true;
  state.currentImage.purpose_states = state.currentImage.purpose_states || {};
  const previousState = state.currentImage.purpose_states[purpose];
  state.currentImage.purpose_states[purpose] = {
    status: value,
    review_status: "unreviewed",
    updated_at: new Date().toISOString(),
    reviewed_at: null,
    updated_by: state.currentUser || (typeof previousState === "object" ? previousState.updated_by || null : null),
    reviewed_by: null,
  };
  state.currentImage.status = value;
  renderTaskOverlay();
  if (shouldSave) queueAutoSave();
  return true;
}

async function setStatusAndAdvance(value) {
  if (!state.currentImage) return;
  const startIndex = state.currentIndex;
  const currentKey = `${state.currentContainer?.container_no || ""}/${state.currentImage.file_name}`;
  if (!setStatus(value, false)) return;
  await saveCurrent();
  if (!state.images.length) return;
  const refreshedIndex = state.images.findIndex((img) => `${img.container_no}/${img.file_name}` === currentKey);
  const targetIndex = refreshedIndex >= 0 ? refreshedIndex + 1 : startIndex;
  const nextIndex = Math.min(Math.max(targetIndex, 0), state.images.length - 1);
  if (nextIndex >= 0) {
    await selectImage(nextIndex);
  }
}

function hasAnnotationsForPurpose(purpose) {
  return !!purpose && hasAnnotationForPurpose(purpose);
}

function refreshStatusAfterAnnotationChange() {
  if (!state.currentImage) return;
  [...(state.currentImage.training_purposes || [])].forEach((purpose) => {
    const purposeState = state.currentImage.purpose_states?.[purpose];
    const status = typeof purposeState === "string" ? purposeState : purposeState?.status;
    if (status === "annotated" && !hasAnnotationsForPurpose(purpose)) {
      removeTrainingPurpose(purpose, false, { deleteAnnotations: false, confirmDelete: false });
    }
  });
  if (!state.currentImage.training_purposes.includes(state.currentImage.active_purpose)) {
    state.currentImage.active_purpose = state.currentImage.training_purposes[0] || "";
  }
  updateActivePurposeControls();
  applyStatusRadios();
  applyReviewRadios();
  renderTaskOverlay();
}

function setClass(value) {
  const radio = document.querySelector(`input[name='classRadio'][value='${value}']`);
  if (!radio) return;
  radio.checked = true;
  if (state.selectedAnnotationIds.length) {
    const previousPurposes = [...(state.currentImage?.training_purposes || [])];
    const classPurposes = ensurePurposesForClass(value);
    const selected = new Set(state.selectedAnnotationIds);
    state.annotations.forEach((ann) => {
      if (selected.has(ann.id)) {
        markPurposeUnreviewed(ann.purpose);
        ann.class_id = value;
        if (classPurposes[0]) ann.purpose = classPurposes[0];
        markPurposeUnreviewed(ann.purpose);
      }
    });
    previousPurposes.forEach((purpose) => {
      if (purposeStatus(purpose) === "annotated" && !hasAnnotationsForPurpose(purpose)) {
        removeTrainingPurpose(purpose, false, { deleteAnnotations: false, confirmDelete: false });
      }
    });
    updateActivePurposeControls();
    applyStatusRadios();
    applyReviewRadios();
    renderAnnotations();
    queueAutoSave();
  }
}

function updateCanvasSize() {
  const wrap = $("canvasWrap");
  const canvas = $("overlay");
  canvas.width = wrap.clientWidth;
  canvas.height = wrap.clientHeight;
  const photo = $("photo");
  if (photo.complete && photo.naturalWidth) {
    const fitScale = Math.min(wrap.clientWidth / photo.naturalWidth, wrap.clientHeight / photo.naturalHeight);
    const scale = fitScale * state.zoom;
    const displayWidth = photo.naturalWidth * scale;
    const displayHeight = photo.naturalHeight * scale;
    const x = (wrap.clientWidth - displayWidth) / 2 + state.panX;
    const y = (wrap.clientHeight - displayHeight) / 2 + state.panY;
    state.imageRect = {
      x,
      y,
      width: displayWidth,
      height: displayHeight,
      naturalWidth: photo.naturalWidth,
      naturalHeight: photo.naturalHeight,
    };
    photo.style.left = `${x}px`;
    photo.style.top = `${y}px`;
    photo.style.width = `${displayWidth}px`;
    photo.style.height = `${displayHeight}px`;
  } else {
    state.imageRect = null;
  }
  drawOverlay();
}

function resetZoom() {
  state.zoom = 1;
  state.panX = 0;
  state.panY = 0;
}

function clampZoom(value) {
  return Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, value));
}

function zoomAt(point, direction) {
  if (!state.imageRect) return;
  const before = state.imageRect;
  if (point.x < before.x || point.x > before.x + before.width || point.y < before.y || point.y > before.y + before.height) {
    return;
  }
  const nextZoom = clampZoom(state.zoom * (direction < 0 ? 1.18 : 1 / 1.18));
  if (Math.abs(nextZoom - state.zoom) < 0.001) return;
  if (nextZoom === MIN_ZOOM) {
    resetZoom();
    updateCanvasSize();
    return;
  }
  const imageX = (point.x - before.x) / before.width;
  const imageY = (point.y - before.y) / before.height;
  state.zoom = nextZoom;
  updateCanvasSize();
  const after = state.imageRect;
  if (!after) return;
  state.panX += point.x - (after.x + imageX * after.width);
  state.panY += point.y - (after.y + imageY * after.height);
  updateCanvasSize();
}

function imageToCanvasRect(ann) {
  const r = state.imageRect;
  if (!r) return null;
  return {
    x: r.x + ann.x / r.naturalWidth * r.width,
    y: r.y + ann.y / r.naturalHeight * r.height,
    width: ann.width / r.naturalWidth * r.width,
    height: ann.height / r.naturalHeight * r.height,
  };
}

function pointToImage(point) {
  const r = state.imageRect;
  if (!r) return null;
  return {
    x: (point.x - r.x) / r.width * r.naturalWidth,
    y: (point.y - r.y) / r.height * r.naturalHeight,
  };
}

function hitTestAnnotation(point) {
  for (let index = state.annotations.length - 1; index >= 0; index -= 1) {
    const ann = state.annotations[index];
    const r = imageToCanvasRect(ann);
    if (!r) continue;
    const handle = 10;
    const onResizeSe =
      point.x >= r.x + r.width - handle &&
      point.x <= r.x + r.width + handle &&
      point.y >= r.y + r.height - handle &&
      point.y <= r.y + r.height + handle;
    if (onResizeSe) return { ann, mode: "resize-se" };
    const onResizeNw =
      point.x >= r.x - handle &&
      point.x <= r.x + handle &&
      point.y >= r.y - handle &&
      point.y <= r.y + handle;
    if (onResizeNw) return { ann, mode: "resize-nw" };
    const inside =
      point.x >= r.x &&
      point.x <= r.x + r.width &&
      point.y >= r.y &&
      point.y <= r.y + r.height;
    if (inside) return { ann, mode: "move" };
  }
  return null;
}

function canvasToImageRect(rect) {
  const r = state.imageRect;
  const x1 = Math.max(r.x, Math.min(rect.x, rect.x + rect.width));
  const y1 = Math.max(r.y, Math.min(rect.y, rect.y + rect.height));
  const x2 = Math.min(r.x + r.width, Math.max(rect.x, rect.x + rect.width));
  const y2 = Math.min(r.y + r.height, Math.max(rect.y, rect.y + rect.height));
  return {
    x: (x1 - r.x) / r.width * r.naturalWidth,
    y: (y1 - r.y) / r.height * r.naturalHeight,
    width: (x2 - x1) / r.width * r.naturalWidth,
    height: (y2 - y1) / r.height * r.naturalHeight,
  };
}

function normalizeCanvasRect(rect) {
  const x1 = Math.min(rect.x, rect.x + rect.width);
  const y1 = Math.min(rect.y, rect.y + rect.height);
  const x2 = Math.max(rect.x, rect.x + rect.width);
  const y2 = Math.max(rect.y, rect.y + rect.height);
  return { x: x1, y: y1, width: x2 - x1, height: y2 - y1 };
}

function rectContainsRect(outer, inner) {
  return (
    inner.x >= outer.x &&
    inner.y >= outer.y &&
    inner.x + inner.width <= outer.x + outer.width &&
    inner.y + inner.height <= outer.y + outer.height
  );
}

function annotationIdsInside(rect) {
  const area = normalizeCanvasRect(rect);
  return state.annotations
    .filter((ann) => {
      const annRect = imageToCanvasRect(ann);
      return annRect && rectContainsRect(area, annRect);
    })
    .map((ann) => ann.id);
}

function brightnessAtCanvasPoint(point) {
  const photo = $("photo");
  const r = state.imageRect;
  if (!photo?.complete || !photo.naturalWidth || !r) return 80;
  if (point.x < r.x || point.x > r.x + r.width || point.y < r.y || point.y > r.y + r.height) return 80;
  try {
    const sample = state.sampleCanvas || document.createElement("canvas");
    state.sampleCanvas = sample;
    sample.width = 1;
    sample.height = 1;
    const ctx = sample.getContext("2d", { willReadFrequently: true });
    const ix = (point.x - r.x) / r.width * photo.naturalWidth;
    const iy = (point.y - r.y) / r.height * photo.naturalHeight;
    ctx.drawImage(photo, ix, iy, 1, 1, 0, 0, 1, 1);
    const [red, green, blue] = ctx.getImageData(0, 0, 1, 1).data;
    return red * 0.299 + green * 0.587 + blue * 0.114;
  } catch (err) {
    return 80;
  }
}

function overlayColors(rect, selected = false) {
  return selected
    ? { stroke: "#ffd166", fill: "rgba(255, 209, 102, 0.16)" }
    : { stroke: "#00d1c1", fill: "rgba(0, 209, 193, 0.14)" };
}

function drawingStroke(rect) {
  const normalized = normalizeCanvasRect(rect);
  const center = {
    x: normalized.x + normalized.width / 2,
    y: normalized.y + normalized.height / 2,
  };
  return brightnessAtCanvasPoint(center) > 165 ? "#dc2626" : "#ffd166";
}

function drawResizeHandle(ctx, x, y, color) {
  ctx.fillStyle = color;
  ctx.fillRect(x - 5, y - 5, 10, 10);
}

function drawCrosshair(ctx) {
  if (!state.pointer || !state.imageRect || state.drawing || state.editing || state.selectionBox) return;
  const r = state.imageRect;
  const p = state.pointer;
  if (p.x < r.x || p.x > r.x + r.width || p.y < r.y || p.y > r.y + r.height) return;
  const light = brightnessAtCanvasPoint(p) > 165;
  ctx.save();
  ctx.strokeStyle = light ? "rgba(220, 38, 38, 0.85)" : "rgba(255, 209, 102, 0.9)";
  ctx.lineWidth = 1;
  ctx.setLineDash([5, 4]);
  ctx.beginPath();
  ctx.moveTo(Math.max(r.x, p.x - CROSSHAIR_LENGTH), p.y);
  ctx.lineTo(Math.min(r.x + r.width, p.x + CROSSHAIR_LENGTH), p.y);
  ctx.moveTo(p.x, Math.max(r.y, p.y - CROSSHAIR_LENGTH));
  ctx.lineTo(p.x, Math.min(r.y + r.height, p.y + CROSSHAIR_LENGTH));
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function drawOverlay() {
  const canvas = $("overlay");
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.lineWidth = 2;
  ctx.font = "13px Microsoft YaHei, Arial";
  for (const ann of state.annotations) {
    const r = imageToCanvasRect(ann);
    if (!r) continue;
    const selected = state.selectedAnnotationIds.includes(ann.id);
    const colors = overlayColors(r, selected);
    ctx.strokeStyle = colors.stroke;
    ctx.fillStyle = colors.fill;
    ctx.strokeRect(r.x, r.y, r.width, r.height);
    ctx.fillRect(r.x, r.y, r.width, r.height);
    if (selected) {
      drawResizeHandle(ctx, r.x, r.y, colors.stroke);
      drawResizeHandle(ctx, r.x + r.width, r.y + r.height, colors.stroke);
    }
    ctx.fillStyle = colors.stroke;
    const purposeText = ann.purpose ? `[${ann.purpose}] ` : "";
    ctx.fillText(`${purposeText}${labelName(ann.class_id)}${ann.text ? " " + ann.text : ""}`, r.x + 4, Math.max(16, r.y - 5));
  }
  if (state.drawing) {
    const r = state.drawing;
    ctx.strokeStyle = drawingStroke(r);
    ctx.strokeRect(r.x, r.y, r.width, r.height);
  }
  if (state.selectionBox) {
    const r = normalizeCanvasRect(state.selectionBox);
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = "#7c3aed";
    ctx.fillStyle = "rgba(124, 58, 237, 0.12)";
    ctx.strokeRect(r.x, r.y, r.width, r.height);
    ctx.fillRect(r.x, r.y, r.width, r.height);
    ctx.setLineDash([]);
  }
  drawCrosshair(ctx);
}

function pointerPos(event) {
  const rect = $("overlay").getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

async function saveCurrent(snapshot = null) {
  snapshot = snapshot || createSaveSnapshot();
  if (!snapshot) return;
  const seq = ++state.saveSeq;
  setSaveStatus("保存中...", "saving");
  try {
    const saved = await api("/api/image", { method: "POST", body: JSON.stringify(snapshot.payload) });
    if (seq === state.saveSeq) {
      mergeSavedImage(snapshot, saved);
      setSaveStatus("已保存");
    }
  } catch (err) {
    setSaveStatus("保存失败", "error");
    throw err;
  }
}

function queueAutoSave(delay = 0) {
  if (state.isRendering || !state.currentImage) return;
  state.pendingSaveSnapshot = createSaveSnapshot();
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => {
    const snapshot = state.pendingSaveSnapshot;
    state.pendingSaveSnapshot = null;
    saveCurrent(snapshot).catch((err) => console.error(err));
  }, delay);
}

function selectedClassId() {
  const radio = document.querySelector("input[name='classRadio']:checked");
  return radio ? radio.value : labelTypes({ visibleOnly: true })[0]?.id || "";
}

function workbenchActive() {
  return $("workbenchView")?.classList.contains("active");
}

function selectNextImage() {
  if (state.currentIndex < state.images.length - 1) {
    selectImage(state.currentIndex + 1);
  }
}

function bindEvents() {
  ensureStatusFilterOptions();
  createAdvancedFilterUi();
  document.querySelectorAll(".tab[data-view]").forEach((btn) => btn.onclick = () => setView(btn.dataset.view));
  document.querySelectorAll(".config-tab").forEach((btn) => {
    btn.onclick = () => {
      state.labelConfig = collectLabelConfigFromSettings();
      updateSelectedLabelFromDetail(false);
      document.querySelectorAll(".config-tab").forEach((item) => item.classList.toggle("active", item === btn));
      $("photosConfigPanel").classList.toggle("active", btn.dataset.configTab === "photos");
      $("labelsConfigPanel").classList.toggle("active", btn.dataset.configTab === "labels");
      $("tasksConfigPanel").classList.toggle("active", btn.dataset.configTab === "tasks");
      $("labelConfigActions").hidden = btn.dataset.configTab === "photos";
      renderLabelSettings();
    };
  });
  $("downloadPageBtn").onclick = () => {
    window.location.href = "/download";
  };
  $("logoutBtn").onclick = async () => {
    await fetch("/logout", { method: "POST" });
    window.location.href = "/login";
  };
  $("saveConfigBtn").onclick = async () => {
    const config = await api("/api/config", { method: "POST", body: JSON.stringify({ photo_root: $("photoRootInput").value }) });
    setLog(config);
    await loadConfig();
  };
  $("addTaskBtn").onclick = () => {
    updateSelectedLabelFromDetail(false);
    const draft = collectLabelConfigFromSettings();
    const id = nextNumericId(draft.training_tasks);
    draft.training_tasks.push({
      id,
      name: "新训练任务",
      visible: true,
      sort: nextSort(draft.training_tasks),
    });
    state.labelConfig = draft;
    renderLabelSettings(draft);
  };
  $("addLabelBtn").onclick = () => {
    updateSelectedLabelFromDetail(false);
    const draft = collectLabelConfigFromSettings();
    const id = nextNumericId(draft.label_types);
    draft.label_types.push({
      id,
      name: "新标签",
      shortcut: "",
      task_ids: [],
      visible: true,
      sort: nextSort(draft.label_types),
    });
    state.selectedLabelId = id;
    state.labelConfig = draft;
    renderLabelSettings(draft);
  };
  $("saveLabelConfigBtn").onclick = async () => {
    updateSelectedLabelFromDetail(false);
    const payload = collectLabelConfigFromSettings();
    const saved = await api("/api/label-config", { method: "POST", body: JSON.stringify(payload) });
    state.labelConfig = saved;
    renderLabelConfigControls();
    renderLabelSettings();
    if (state.currentImage) {
      normalizeImageModel(state.currentImage);
      renderCurrent();
    }
    setLog(saved);
    await loadImages();
  };
  $("scanBtn").onclick = async () => {
    setLog("正在扫描照片目录...");
    const data = await api("/api/scan", { method: "POST", body: "{}" });
    setLog(data);
    await loadIndex();
    await loadImages();
  };
  $("purposeFilter").onchange = () => {
    if ($("batchPurposeSelect")) $("batchPurposeSelect").value = $("purposeFilter").value;
    if ($("resetTaskSelect")) $("resetTaskSelect").value = $("purposeFilter").value;
    $("groupFilter").value = "";
    state.currentIndex = -1;
    loadImages();
  };
  $("statusFilter").onchange = () => {
    $("groupFilter").value = "";
    state.currentIndex = -1;
    loadImages();
  };
  $("reviewFilter").onchange = () => {
    $("groupFilter").value = "";
    state.currentIndex = -1;
    loadImages();
  };
  $("groupSizeFilter").onchange = () => {
    $("groupFilter").value = "";
    state.currentIndex = -1;
    loadImages();
  };
  $("groupFilter").onchange = () => {
    state.currentIndex = -1;
    loadImages();
  };
  $("yearFilter").onchange = () => {
    $("monthFilter").value = "";
    $("groupFilter").value = "";
    state.currentIndex = -1;
    loadImages();
  };
  $("monthFilter").onchange = () => {
    $("groupFilter").value = "";
    state.currentIndex = -1;
    loadImages();
  };
  $("stageFilter").onchange = () => {
    $("groupFilter").value = "";
    state.currentIndex = -1;
    loadImages();
  };
  $("searchInput").oninput = () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(() => {
      $("groupFilter").value = "";
      state.currentIndex = -1;
      loadImages();
    }, 250);
  };
  $("prevBtn").onclick = () => state.currentIndex > 0 && selectImage(state.currentIndex - 1);
  $("nextBtn").onclick = () => state.currentIndex < state.images.length - 1 && selectImage(state.currentIndex + 1);
  $("batchModeBtn").onclick = exitBatchMode;
  $("batchClearBtn").onclick = clearBatchSelection;
  $("batchPendingBtn").onclick = () => applyBatchStatus("pending").catch((err) => setSaveStatus(err.message, "error"));
  $("batchNoTargetBtn").onclick = () => applyBatchStatus("no_target").catch((err) => setSaveStatus(err.message, "error"));
  $("batchExcludeBtn").onclick = () => applyBatchStatus("excluded").catch((err) => setSaveStatus(err.message, "error"));
  $("batchUnassignBtn").onclick = () => unassignBatchPurpose().catch((err) => setSaveStatus(err.message, "error"));
  $("thumbSizeInput").oninput = (event) => setThumbSize(event.target.value);
  $("filmstripToggleBtn").onclick = (event) => {
    event.preventDefault();
    event.stopPropagation();
    toggleFilmstripHeight();
  };
  $("filmstripResizer").onpointerdown = (event) => {
    if (event.target.closest(".filmstrip-resizer-handle")) return;
    event.preventDefault();
    state.resizingFilmstrip = {
      startY: event.clientY,
      startHeight: state.filmstripHeight,
    };
    $("filmstripResizer").classList.add("dragging");
    $("filmstripResizer").setPointerCapture(event.pointerId);
  };
  $("filmstripResizer").onpointermove = (event) => {
    if (!state.resizingFilmstrip) return;
    const delta = state.resizingFilmstrip.startY - event.clientY;
    setFilmstripHeight(state.resizingFilmstrip.startHeight + delta);
  };
  $("filmstripResizer").onpointerup = () => {
    state.resizingFilmstrip = null;
    $("filmstripResizer").classList.remove("dragging");
    updateCanvasSize();
  };
  $("filmstripResizer").onpointercancel = () => {
    state.resizingFilmstrip = null;
    $("filmstripResizer").classList.remove("dragging");
    updateCanvasSize();
  };
  $("clearBoxesBtn").onclick = () => {
    state.annotations.forEach((ann) => markPurposeUnreviewed(ann.purpose));
    state.annotations = [];
    state.selectedAnnotationId = null;
    state.selectedAnnotationIds = [];
    renderAnnotations();
    refreshStatusAfterAnnotationChange();
    queueAutoSave();
  };
  document.querySelectorAll("input[name='statusRadio']").forEach((radio) => {
    radio.onchange = () => setStatus(radio.value);
  });
  document.querySelectorAll("input[name='reviewRadio']").forEach((radio) => {
    radio.onchange = () => setReviewStatus(radio.value);
  });
  $("notesInput").oninput = () => queueAutoSave(700);
  $("imageList").addEventListener("wheel", (event) => {
    const list = $("imageList");
    if (!list) return;
    if (event.ctrlKey) {
      const direction = event.deltaY < 0 ? 1 : -1;
      setThumbSize(state.thumbSize + direction * 10);
      $("thumbSizeInput").value = String(state.thumbSize);
      event.preventDefault();
      return;
    }
    if (list.scrollWidth <= list.clientWidth) return;
    const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
    const step = Math.max(-36, Math.min(36, delta * 0.35));
    list.scrollLeft += step;
    event.preventDefault();
  }, { passive: false });

  $("photo").onload = () => {
    if (state.currentImage) {
      state.currentImage.image_width = $("photo").naturalWidth || state.currentImage.image_width || null;
      state.currentImage.image_height = $("photo").naturalHeight || state.currentImage.image_height || null;
    }
    updateCanvasSize();
    requestAnimationFrame(updateCanvasSize);
  };
  window.onresize = () => {
    setFilmstripHeight(state.filmstripHeight);
    updateCanvasSize();
  };
  $("overlay").oncontextmenu = (event) => event.preventDefault();
  $("taskOverlay").onpointerdown = (event) => {
    const overlay = $("taskOverlay");
    const wrapRect = $("canvasWrap").getBoundingClientRect();
    const rect = overlay.getBoundingClientRect();
    state.draggingTaskOverlay = {
      startX: event.clientX,
      startY: event.clientY,
      left: rect.left - wrapRect.left,
      top: rect.top - wrapRect.top,
    };
    overlay.setPointerCapture(event.pointerId);
    event.preventDefault();
  };
  $("taskOverlay").onpointermove = (event) => {
    if (!state.draggingTaskOverlay) return;
    const overlay = $("taskOverlay");
    const wrap = $("canvasWrap");
    const nextLeft = state.draggingTaskOverlay.left + event.clientX - state.draggingTaskOverlay.startX;
    const nextTop = state.draggingTaskOverlay.top + event.clientY - state.draggingTaskOverlay.startY;
    const maxLeft = Math.max(0, wrap.clientWidth - overlay.offsetWidth - 8);
    const maxTop = Math.max(0, wrap.clientHeight - overlay.offsetHeight - 8);
    const left = Math.max(8, Math.min(maxLeft, nextLeft));
    const top = Math.max(8, Math.min(maxTop, nextTop));
    overlay.style.left = `${left}px`;
    overlay.style.top = `${top}px`;
    saveTaskOverlayPosition({ left, top });
  };
  $("taskOverlay").onpointerup = () => {
    state.draggingTaskOverlay = null;
  };
  $("taskOverlay").onpointercancel = () => {
    state.draggingTaskOverlay = null;
  };
  $("overlay").addEventListener("wheel", (event) => {
    if (!state.currentImage || !state.imageRect) return;
    event.preventDefault();
    state.pointer = pointerPos(event);
    zoomAt(state.pointer, event.deltaY);
  }, { passive: false });
  $("overlay").onmouseenter = (event) => {
    state.pointer = pointerPos(event);
    drawOverlay();
  };
  $("overlay").onmousemove = (event) => {
    state.pointer = pointerPos(event);
    if (!state.drawing && !state.editing && !state.selectionBox) drawOverlay();
  };
  $("overlay").onpointerleave = () => {
    state.pointer = null;
    drawOverlay();
  };
  $("overlay").onpointerdown = (event) => {
    if (!state.currentImage || !state.imageRect) return;
    event.preventDefault();
    const pos = pointerPos(event);
    state.pointer = pos;
    if (event.button === 2) {
      state.drawing = null;
      state.editing = null;
      state.selectionBox = { x: pos.x, y: pos.y, width: 0, height: 0 };
      $("overlay").setPointerCapture(event.pointerId);
      drawOverlay();
      return;
    }
    if (event.button !== 0) return;
    const hit = hitTestAnnotation(pos);
    if (hit) {
      if (hit.mode === "move" && hit.ann.class_id !== selectedClassId()) {
        state.selectedAnnotationId = null;
        state.selectedAnnotationIds = [];
        state.drawing = { x: pos.x, y: pos.y, width: 0, height: 0 };
        $("overlay").setPointerCapture(event.pointerId);
        drawOverlay();
        return;
      }
      const imagePoint = pointToImage(pos);
      selectAnnotation(hit.ann.id);
      state.editing = {
        mode: hit.mode,
        ann: hit.ann,
        startImage: imagePoint,
        original: { x: hit.ann.x, y: hit.ann.y, width: hit.ann.width, height: hit.ann.height },
      };
      $("overlay").setPointerCapture(event.pointerId);
      return;
    }
    state.selectedAnnotationId = null;
    state.selectedAnnotationIds = [];
    state.drawing = { x: pos.x, y: pos.y, width: 0, height: 0 };
  };
  $("overlay").onpointermove = (event) => {
    state.pointer = pointerPos(event);
    if (state.selectionBox) {
      const pos = state.pointer;
      state.selectionBox.width = pos.x - state.selectionBox.x;
      state.selectionBox.height = pos.y - state.selectionBox.y;
      drawOverlay();
      return;
    }
    if (state.editing && state.imageRect) {
      const imagePoint = pointToImage(pointerPos(event));
      const dx = imagePoint.x - state.editing.startImage.x;
      const dy = imagePoint.y - state.editing.startImage.y;
      const ann = state.editing.ann;
      const original = state.editing.original;
      if (state.editing.mode === "move") {
        ann.x = Math.round(Math.max(0, Math.min(state.imageRect.naturalWidth - original.width, original.x + dx)));
        ann.y = Math.round(Math.max(0, Math.min(state.imageRect.naturalHeight - original.height, original.y + dy)));
      } else if (state.editing.mode === "resize-se") {
        ann.width = Math.round(Math.max(6, Math.min(state.imageRect.naturalWidth - original.x, original.width + dx)));
        ann.height = Math.round(Math.max(6, Math.min(state.imageRect.naturalHeight - original.y, original.height + dy)));
      } else if (state.editing.mode === "resize-nw") {
        const right = original.x + original.width;
        const bottom = original.y + original.height;
        const nextX = Math.round(Math.max(0, Math.min(right - 6, original.x + dx)));
        const nextY = Math.round(Math.max(0, Math.min(bottom - 6, original.y + dy)));
        ann.x = nextX;
        ann.y = nextY;
        ann.width = Math.round(right - nextX);
        ann.height = Math.round(bottom - nextY);
      }
      drawOverlay();
      return;
    }
    if (!state.drawing) {
      drawOverlay();
      return;
    }
    const pos = state.pointer;
    state.drawing.width = pos.x - state.drawing.x;
    state.drawing.height = pos.y - state.drawing.y;
    drawOverlay();
  };
  $("overlay").onpointerup = () => {
    if (state.selectionBox) {
      const box = state.selectionBox;
      state.selectionBox = null;
      const area = normalizeCanvasRect(box);
      const ids = area.width > 4 && area.height > 4 ? annotationIdsInside(area) : [];
      selectAnnotations(ids);
      return;
    }
    if (state.editing) {
      markPurposeUnreviewed(state.editing.ann?.purpose);
      state.editing = null;
      applyReviewRadios();
      renderAnnotations();
      queueAutoSave();
      return;
    }
    if (!state.drawing || !state.imageRect) return;
    const imgRect = canvasToImageRect(state.drawing);
    state.drawing = null;
    if (imgRect.width > 5 && imgRect.height > 5) {
      const classId = selectedClassId();
      const classPurposes = ensurePurposesForClass(classId);
      const annPurpose = classPurposes[0] || activePurpose();
      const annText = defaultTextForClass(classId);
      const ann = {
        id: crypto.randomUUID(),
        type: "bbox",
        purpose: annPurpose,
        class_id: classId,
        text: annText,
        x: Math.round(imgRect.x),
        y: Math.round(imgRect.y),
        width: Math.round(imgRect.width),
        height: Math.round(imgRect.height),
      };
      state.annotations.push(ann);
      state.selectedAnnotationId = null;
      state.selectedAnnotationIds = [];
      const statusPurposes = classPurposes.length ? classPurposes : annPurpose ? [annPurpose] : [];
      statusPurposes.forEach((purpose) => {
        setActivePurpose(purpose, false);
        setStatus("annotated", false);
      });
    }
    renderAnnotations();
    queueAutoSave();
  };

  document.addEventListener("keydown", (event) => {
    const tag = event.target?.tagName?.toLowerCase();
    if (event.key === "Escape" && state.batchMode) {
      event.preventDefault();
      exitBatchMode();
      return;
    }
    if (event.key === " " && workbenchActive()) {
      event.preventDefault();
      selectNextImage();
      return;
    }
    if (tag === "input" || tag === "textarea" || tag === "select") return;
    if (event.key === "[" && workbenchActive()) {
      event.preventDefault();
      setThumbSize(state.thumbSize - 10);
      $("thumbSizeInput").value = String(state.thumbSize);
      return;
    }
    if (event.key === "]" && workbenchActive()) {
      event.preventDefault();
      setThumbSize(state.thumbSize + 10);
      $("thumbSizeInput").value = String(state.thumbSize);
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (state.currentIndex > 0) selectImage(state.currentIndex - 1);
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      if (state.currentIndex < state.images.length - 1) selectImage(state.currentIndex + 1);
      return;
    }
    if (event.key === "Delete" || event.key === "Backspace") {
      if (deleteSelectedAnnotation()) event.preventDefault();
      return;
    }
    const classShortcuts = {};
    labelTypes({ visibleOnly: true }).forEach((item) => {
      if (item.shortcut) classShortcuts[item.shortcut.toLowerCase()] = item.id;
    });
    const taskShortcuts = {};
    trainingTasks({ visibleOnly: true }).forEach((item) => {
      if (item.shortcut) taskShortcuts[item.shortcut.toLowerCase()] = item.id;
    });
    const statusShortcuts = {
      z: "pending",
      c: "no_target",
      x: "excluded",
    };
    const key = event.key.toLowerCase();
    if (classShortcuts[key]) {
      event.preventDefault();
      setClass(classShortcuts[key]);
      return;
    }
    if (statusShortcuts[key]) {
      event.preventDefault();
      if (key === "c" || key === "x") {
        setStatusAndAdvance(statusShortcuts[key]).catch((err) => console.error(err));
        return;
      }
      setStatus(statusShortcuts[key]);
      return;
    }
    if (taskShortcuts[key]) {
      event.preventDefault();
      setActivePurpose(taskShortcuts[key], true);
    }
  });
}

async function init() {
  bindEvents();
  setThumbSize(state.thumbSize);
  updateBatchToolbar();
  await loadSession();
  await loadConfig();
  await loadLabelConfig();
  await loadIndex();
  await loadImages();
}

init().catch((err) => setLog(err.message));
