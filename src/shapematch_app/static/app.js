"use strict";
const $ = (id) => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
if (fragment.has("session")) {
  sessionStorage.setItem("shapematch-session", fragment.get("session"));
  history.replaceState(null, "", location.pathname);
}
const token = sessionStorage.getItem("shapematch-session") || "";
let selectedQuery = "",
  pickerMode = "folder",
  directory = "",
  fileOffset = 0,
  fileSequence = 0,
  entryOffset = 0,
  navigationSequence = 0,
  preview = null,
  previewContext = null,
  booleanRequest = null,
  previewSequence = 0,
  lastJob = "",
  workersDirty = false,
  timer;
async function api(path, body) {
  const response = await fetch("/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "X-ShapeMatch-Session": token,
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || response.statusText);
  return data;
}
function error(e) {
  $("error").textContent = e.message || String(e);
}
function button(text, handler) {
  const b = document.createElement("button");
  b.textContent = text;
  b.onclick = () => Promise.resolve().then(handler).catch(error);
  return b;
}
function selectQuery(path, label = path.split(/[\\/]/).pop()) {
  fileSequence++;
  selectedQuery = path;
  $("query").textContent = label;
  $("query").title = path;
  $("autocomplete").value = label;
  clearTimeout(autocompleteTimer);
  $("suggestions").replaceChildren();
  $("more-files").hidden = true;
  $("error").textContent = "";
}
async function files(append = false) {
  const sequence = ++fileSequence;
  if (!append) {
    fileOffset = 0;
    $("suggestions").replaceChildren();
  }
  const text = $("autocomplete").value;
  const data = await api(
    "files?" + new URLSearchParams({ text, offset: fileOffset }),
  );
  if (sequence !== fileSequence || text !== $("autocomplete").value) return;
  for (const item of data.items) {
    const warnings = item.warnings || [];
    const b = button(item.relative + " · " + item.status + (warnings.length ? " · notes" : ""), () =>
      selectQuery(item.path, item.relative),
    );
    b.disabled = false;
    b.title = [item.error, ...warnings].filter(Boolean).join("\n");
    $("suggestions").append(b);
  }
  fileOffset += data.items.length;
  $("more-files").hidden = fileOffset >= data.total;
}
async function navigate(path = "", append = false) {
  const sequence = ++navigationSequence;
  $("path").disabled = true;
  const go = document.querySelector("#path-form button");
  go.disabled = true;
  try {
    if (!append) entryOffset = 0;
    const data = await api(
      "filesystem?" +
        new URLSearchParams({
          path,
          offset: entryOffset,
          allow_file: pickerMode === "file",
        }),
    );
    if (sequence !== navigationSequence) return;
    if (data.selected_file && pickerMode === "file") {
      selectQuery(data.selected_file);
      $("picker").close();
      return;
    }
    directory = data.path;
    $("path").value = directory;
    $("picker-error").textContent = data.errors.join("\n");
    if (!append) {
      $("entries").replaceChildren();
      $("breadcrumbs").replaceChildren();
      $("drives").replaceChildren();
      for (const drive of data.drives) {
        const o = document.createElement("option");
        o.value = drive;
        o.textContent = drive;
        $("drives").append(o);
      }
      const drive = data.drives.find((d) =>
        directory.toLowerCase().startsWith(d.toLowerCase()),
      );
      if (drive) $("drives").value = drive;
      for (const crumb of data.breadcrumbs)
        $("breadcrumbs").append(button(crumb.name, () => navigate(crumb.path)));
    }
    for (const item of data.items) {
      const b = button((item.directory ? "📁 " : "") + item.name, async () => {
        if (item.directory) await navigate(item.path);
        else {
          selectQuery(item.path);
          $("picker").close();
        }
      });
      b.disabled = pickerMode === "folder" && !item.directory;
      $("entries").append(b);
    }
    entryOffset += data.items.length;
    $("more-entries").hidden = entryOffset >= data.total;
  } catch (e) {
    if (sequence === navigationSequence)
      $("picker-error").textContent = e.message;
  } finally {
    if (sequence === navigationSequence) {
      $("path").disabled = false;
      go.disabled = false;
    }
  }
}
async function picker(mode) {
  pickerMode = mode;
  $("picker-title").textContent =
    mode === "folder" ? "Choose library folder" : "Choose a STEP query";
  $("pick-folder").hidden = mode !== "folder";
  $("picker").showModal();
  await navigate(directory || "");
}
function rankings(job) {
  $("results").replaceChildren();
  for (const [index, item] of job.results.entries()) {
    const row = document.createElement("tr"),
      cell = document.createElement("td");
    const name = item.relative.split(/[\\/]/).pop();
    cell.append(button(name, () => showPreview(job.id, index, item.path)));
    const path = document.createElement("small");
    path.textContent = item.relative;
    cell.append(path);
    row.append(cell);
    for (const value of [
      item.alignment_error,
      item.normalized_alignment_error,
      item.alignment_transform.scale,
    ]) {
      const td = document.createElement("td");
      td.textContent = Number(value).toPrecision(6);
      row.append(td);
    }
    $("results").append(row);
  }
}
async function refresh() {
  try {
    const state = await api("state");
    $("library").textContent = state.library || "Select a library folder";
    $("counts").textContent = Object.entries(state.counts)
      .map(([k, v]) => `${v} ${k}`)
      .join(" · ");
    const job = state.job;
    $("workers").max = state.max_workers;
    if (!workersDirty) $("workers").value = state.workers;
    $("active-workers").textContent = (state.active_workers || []).map(
      (item) => `${item.operation} · ${Math.floor(item.elapsed)}s · ${item.path}`,
    ).join("\n");
    const running = job && job.status === "running";
    for (const id of ["folder", "rescan", "search"])
      $(id).disabled = !!running || (!state.library && id !== "folder");
    $("cancel").disabled = !running;
    $("search-cancel").disabled = !running;
    if (job) {
      $("progress").textContent =
        `${job.kind}: ${job.status} · ${job.completed}/${job.total} ${job.kind === "search" ? "checked / shortlisted" : ""}${job.eligible !== undefined ? " · " + job.eligible + " eligible" : ""}\n${job.coverage}`;
      const elapsed = Math.max(0, (job.finished || Date.now() / 1000) - job.started);
      const rate = elapsed > 0 ? job.completed * 60 / elapsed : 0;
      $("progress").textContent += ` · ${job.phase || job.kind} · ${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s elapsed · ${rate.toFixed(1)} files/min · ${(state.active_workers || []).length}/${state.workers} workers active`;
      if (job.phase === "hashing") $("progress").textContent += ` · ${job.hashed || 0}/${job.total} hashed`;
      $("meter").max = Math.max(job.total, 1);
      $("meter").value = job.completed;
      $("errors").textContent =
        job.errors.map((e) => e.path + ": " + e.error).join("\n") +
        (job.error_count > 100
          ? `\nAdditional errors omitted; total: ${job.error_count}`
          : "");
      const signature = JSON.stringify([job.id, job.completed, job.status]);
      if (signature !== lastJob) {
        lastJob = signature;
        if (job.kind === "search") rankings(job);
        if (job.status !== "running") await files();
      }
    }
    if (!job) {
      $("results").replaceChildren();
      $("preview-status").textContent = "Select a query, search, then choose a result to compare in 3D.";
      $("progress").textContent = "Ready to scan";
      $("errors").textContent = "";
      $("meter").value = 0;
      lastJob = "";
    }
    $("search-progress").textContent = $("progress").textContent;
    document.body.dataset.ready = "true";
  } catch (e) {
    error(e);
  } finally {
    timer = setTimeout(refresh, 700);
  }
}
async function start(kind, body = {}) {
  await api("jobs/" + kind, body);
  if (kind === "search") {
    preview = null;
    previewContext = null;
    booleanRequest = null;
    Plotly.purge($("viewer"));
    $("preview-status").textContent = "Searching… Choose a result to compare in 3D.";
    $("results").replaceChildren();
    previewSequence++;
  }
  clearTimeout(timer);
  await refresh();
}
async function showPreview(id, index, path) {
  const sequence = ++previewSequence;
  preview = null;
  booleanRequest = null;
  previewContext = {id, index, path};
  $("comparison").hidden = false;
  $("preview-status").textContent = "Generating preview…";
  try {
    const result = await api(
      `jobs/${id}/preview/${index}?${new URLSearchParams({ path })}`,
    );
    if (sequence !== previewSequence) return;
    preview = result;
    await renderPreview();
    $("preview-status").textContent =
      `${result.source.name} / ${result.target.name} · Drag to rotate, scroll to zoom`;
    $("comparison").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    if (sequence === previewSequence)
      $("preview-status").textContent = e.message;
  }
}
function mesh(part, vertices, color, visible, opacity) {
  const triangles = part.triangles;
  return {
    type: "mesh3d",
    x: vertices.map((v) => v[0]),
    y: vertices.map((v) => v[1]),
    z: vertices.map((v) => v[2]),
    i: triangles.map((t) => t[0]),
    j: triangles.map((t) => t[1]),
    k: triangles.map((t) => t[2]),
    name: part.name,
    color,
    visible,
    opacity,
    flatshading: true,
  };
}
async function renderPreview() {
  const booleanMode = $("display-mode").value === "boolean";
  $("overlay-controls").hidden = booleanMode;
  $("boolean-controls").hidden = !booleanMode;
  $("boolean-status").hidden = !booleanMode;
  if (!preview) {
    $("boolean-status").textContent = "Select a search result to show common and exclusive solids.";
    return;
  }
  const vertices =
    $("placement").value === "original"
      ? preview.source.original
      : preview.source.aligned;
  let traces = [
      mesh(
        preview.source,
        vertices,
        "#149cbe",
        $("source-visible").checked,
        Number($("source-opacity").value) / 100,
      ),
      mesh(
        preview.target,
        preview.target.vertices,
        "#ed8530",
        $("target-visible").checked,
        Number($("target-opacity").value) / 100,
      ),
    ];
  if (booleanMode) {
    if (!preview.boolean) {
      $("boolean-status").textContent = "Computing common and exclusive solids…";
      Plotly.purge($("viewer"));
      if (!booleanRequest && previewContext) {
        const context = previewContext, sequence = previewSequence;
        const {id, index, path} = context;
        booleanRequest = api(`jobs/${id}/preview/${index}?` + new URLSearchParams({path, include_booleans: true}))
          .then(result => {
            if (sequence !== previewSequence) return;
            preview = result;
            booleanRequest = null;
            if (!preview.boolean) throw new Error("Boolean regions unavailable");
            return renderPreview();
          }).catch(e => {
            if (sequence !== previewSequence) return;
            booleanRequest = null;
            $("boolean-status").textContent = "Boolean regions unavailable: " + e.message + ". Switch to Part overlay or select the mode again to retry.";
          });
      }
      return;
    }
    const tolerance = Number($("boolean-tolerance").value);
    const placements = tolerance === preview.boolean_tolerance ? preview.boolean :
      preview.boolean_variants.find(level => level.tolerance === tolerance)?.placements;
    const partition = placements?.[$("placement").value];
    const keys = ["common", "only_a", "only_b"];
    const names = ["Common", "Only query", "Only result"];
    const colors = ["#809b87", "#149cbe", "#ed8530"];
    traces = keys.map((key, index) => {
      const region = partition?.regions[key] || {vertices: [], triangles: [], volume: 0};
      $(key + "-volume").textContent = partition?.error ? "Unavailable" : `(${Number(region.volume).toPrecision(5)} mm³)`;
      $(key + "-visible").disabled = !!partition?.error || !region.triangles.length;
      return mesh({...region, name: names[index]}, region.vertices, colors[index],
        !partition?.error && $(key + "-visible").checked,
        Number($(index === 0 ? "common-opacity" : "difference-opacity").value) / 100);
    });
    $("boolean-status").textContent = !partition || partition.error
      ? "Boolean regions unavailable: " + (partition?.error || "Missing tolerance data")
      : traces.some(t => t.visible && t.i.length) ? "Common: green · Only query: blue · Only result: orange. Volumes in mm³."
      : "No solid volume in the selected regions.";
  }
  await Plotly.react(
    $("viewer"), traces,
    {
      margin: { l: 0, r: 0, t: 0, b: 0 },
      scene: {
        aspectmode: "data",
        xaxis: { title: { text: "X (mm)" } },
        yaxis: { title: { text: "Y (mm)" } },
        zaxis: { title: { text: "Z (mm)" } },
      },
      uirevision: "comparison",
    },
    { responsive: true, displaylogo: false },
  );
}
$("folder").onclick = () => picker("folder").catch(error);
$("external").onclick = () => picker("file").catch(error);
$("close-picker").onclick = () => $("picker").close();
$("pick-folder").onclick = async () => {
  try {
    await api("library", { path: directory });
    $("picker").close();
    await files();
    clearTimeout(timer);
    await refresh();
  } catch (e) {
    $("picker-error").textContent = e.message;
  }
};
$("path-form").onsubmit = (e) => {
  e.preventDefault();
  navigate($("path").value);
};
$("drives").onchange = () => navigate($("drives").value);
$("more-entries").onclick = () => navigate(directory, true);
$("more-files").onclick = () => files(true).catch(error);
let autocompleteTimer;
$("autocomplete").oninput = () => {
  selectedQuery = "";
  $("query").textContent = "None — choose a file below";
  clearTimeout(autocompleteTimer);
  autocompleteTimer = setTimeout(() => files().catch(error), 150);
};
$("rescan").onclick = () => start("scan").catch(error);
$("search").onclick = () => {
  if (!selectedQuery) {
    error(new Error("Select a query file first"));
    return;
  }
  start("search", {
    query: selectedQuery,
    depth: $("depth").value,
    match_size: $("match-size").checked,
  }).catch(error);
};
$("cancel").onclick = () => api("cancel", {}).catch(error);
$("workers").oninput = () => { workersDirty = true; };
$("apply-workers").onclick = () => api("workers", { workers: Number($("workers").value) }).then(() => {
  workersDirty = false;
  clearTimeout(timer);
  return refresh();
}).catch(error);
for (const id of ["source-visible", "target-visible", "placement", "display-mode", "boolean-tolerance", "common-visible", "only_a-visible", "only_b-visible"])
  $(id).onchange = () => renderPreview().catch(error);
for (const id of ["source-opacity", "target-opacity", "common-opacity", "difference-opacity"])
  $(id).oninput = () => renderPreview().catch(error);
refresh();
files().catch(error);

function switchTab(settings) {
  $("settings-panel").hidden = !settings;
  $("search-panel").hidden = settings;
  for (const [id, active] of [["settings-tab", settings], ["search-tab", !settings]]) {
    if (active) $(id).setAttribute("aria-current", "page");
    else $(id).removeAttribute("aria-current");
  }
  if (!settings && preview) Plotly.Plots.resize($("viewer"));
}
$("settings-tab").onclick = () => switchTab(true);
$("search-tab").onclick = () => switchTab(false);
$("search-cancel").onclick = () => api("cancel", {}).catch(error);
async function uploadQuery(file) {
  if (!file || !/\.(step|stp)$/i.test(file.name)) throw new Error("Choose a .step or .stp file");
  $("drop-zone").textContent = "Loading " + file.name + "…";
  try {
    const response = await fetch("/api/query-upload?" + new URLSearchParams({name: file.name}), {
      method: "POST", headers: {"X-ShapeMatch-Session": token}, body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.statusText);
    selectQuery(data.path, file.name);
  } finally { $("drop-zone").textContent = "Drop a .step or .stp file here"; }
}
$("drop-zone").onclick = () => $("upload-file").click();
$("drop-zone").onkeydown = (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("upload-file").click(); }
};
$("upload-file").onchange = (e) => uploadQuery(e.target.files[0]).catch(error);
$("drop-zone").ondragover = (e) => { e.preventDefault(); $("drop-zone").classList.add("dragging"); };
$("drop-zone").ondragleave = () => $("drop-zone").classList.remove("dragging");
$("drop-zone").ondrop = (e) => {
  e.preventDefault();
  $("drop-zone").classList.remove("dragging");
  if (e.dataTransfer.files.length !== 1) return error(new Error("Drop one STEP file at a time"));
  uploadQuery(e.dataTransfer.files[0]).catch(error);
};

// Plotly must follow the available canvas size when toolbars wrap or modes change.
let viewerResizeFrame;
new ResizeObserver(() => {
  cancelAnimationFrame(viewerResizeFrame);
  viewerResizeFrame = requestAnimationFrame(() => {
    if ($("viewer").data && !$("search-panel").hidden) Plotly.Plots.resize($("viewer"));
  });
}).observe($("viewer"));
