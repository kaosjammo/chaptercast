"use strict";

const status = document.getElementById("connection-status");
const button = document.getElementById("check-connection");

async function checkConnection() {
  button.disabled = true;
  status.textContent = "Checking connection…";
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch("/health", {
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response.ok || (await response.json()).status !== "ok") {
      throw new Error("Health check failed");
    }
    status.textContent = "Connected to ChapterCast.";
  } catch {
    status.textContent = "Could not reach ChapterCast. Check that the local server is running.";
  } finally {
    clearTimeout(timeout);
    button.disabled = false;
  }
}

button.addEventListener("click", checkConnection);
checkConnection();

const form = document.getElementById("import-form");
const source = document.getElementById("import-source");
const importStatus = document.getElementById("import-status");
const submit = document.getElementById("import-submit");
const fileInput = document.getElementById("chapter-file");
const chapterSelect = document.getElementById("epub-chapter");
const epubFields = document.getElementById("epub-fields");
const fallback = document.getElementById("ffn-fallback");
let uploadId = null;

function chooseSource(value) {
  source.value = value;
  for (const name of ["text", "upload", "url"]) {
    document.getElementById(`${name}-fields`).hidden = value !== name;
  }
  fallback.hidden = true;
  submit.textContent = value === "upload" && uploadId ? "Import selected chapter" : "Import chapter";
}
source.addEventListener("change", () => chooseSource(source.value));
fileInput.addEventListener("change", () => {
  uploadId = null;
  epubFields.hidden = true;
  chapterSelect.replaceChildren();
  submit.textContent = "Import chapter";
});
document.getElementById("fallback-paste").addEventListener("click", () => {
  chooseSource("text");
  document.getElementById("chapter-text").focus();
});
document.getElementById("fallback-upload").addEventListener("click", () => {
  chooseSource("upload");
  fileInput.focus();
});

async function importRequest(path, init) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30000);
  let response, data;
  try {
    response = await fetch(path, {...init, cache: "no-store", signal: controller.signal});
    data = await response.json();
  } catch {
    throw new Error("Could not reach ChapterCast. Check the connection and try again.");
  } finally {
    clearTimeout(timeout);
  }
  if (!response.ok) {
    const error = new Error(data.message || "Import failed.");
    error.code = data.code;
    throw error;
  }
  return data;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submit.disabled = true;
  fallback.hidden = true;
  importStatus.textContent = "Importing…";
  const title = document.getElementById("import-title").value.trim() || null;
  const exclude_notes = document.getElementById("exclude-notes").checked;
  const json = (data) => ({method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  try {
    let result;
    if (source.value === "text") {
      result = await importRequest("/api/import/text", json({title, exclude_notes, text: document.getElementById("chapter-text").value}));
    } else if (source.value === "url") {
      result = await importRequest("/api/import/url", json({title, exclude_notes, url: document.getElementById("chapter-url").value.trim()}));
    } else if (uploadId) {
      result = await importRequest("/api/import/epub", json({title, exclude_notes, upload_id: uploadId, chapter_id: chapterSelect.value}));
    } else {
      const file = fileInput.files[0];
      if (!file) throw new Error("Choose a TXT or EPUB file.");
      const limit = file.name.toLowerCase().endsWith(".txt") ? 1048576 : 10485760;
      if (file.size > limit) throw new Error("File exceeds the upload size limit.");
      const query = new URLSearchParams({filename: file.name, exclude_notes: String(exclude_notes)});
      if (title) query.set("title", title);
      result = await importRequest(`/api/import/upload?${query}`, {method: "POST", headers: {"Content-Type": "application/octet-stream"}, body: file});
      if (result.status === "select_chapter") {
        uploadId = result.upload_id;
        chapterSelect.replaceChildren(...result.chapters.map((chapter) => {
          const option = document.createElement("option");
          option.value = chapter.id;
          option.textContent = chapter.title;
          return option;
        }));
        epubFields.hidden = false;
        submit.textContent = "Import selected chapter";
        importStatus.textContent = "EPUB uploaded. Choose a chapter, then import it.";
        chapterSelect.focus();
        return;
      }
    }
    libraryOffset = 0;
    await refreshLibrary();
    importStatus.textContent = `Imported “${result.title}” privately. Use Generate audio to narrate it.`;
  } catch (error) {
    importStatus.textContent = error.message || "Could not reach the server. Try again.";
    fallback.hidden = error.code !== "ffn_blocked";
  } finally {
    submit.disabled = false;
  }
});

const generationStatus = document.getElementById("generation-status");
const previewSubmit = document.getElementById("preview-submit");
const previewAudio = document.getElementById("voice-preview");
const previewStatus = document.getElementById("preview-status");
const libraryList = document.getElementById("library-list");
const libraryStatus = document.getElementById("library-status");
const queueList = document.getElementById("queue-list");
const refreshButton = document.getElementById("refresh-library");
let libraryOffset = 0;
let libraryBusy = false;
let pollTimer;
let previewJob = null;
let previewLoaded = null;
const chapterCards = new Map();
const queueCards = new Map();

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function action(text, handler) {
  const node = element("button", text);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
function jobDescription(job) {
  if (!job) return "Imported. Ready to generate.";
  const settings = job.voice ? ` · ${job.voice} · narration ${job.speed}×` : "";
  if (job.status === "completed" && !job.outputs.length) return `Audio unavailable. Generate again.${settings}`;
  if (job.status === "running" || job.status === "queued") {
    const stage = job.status === "running" && job.completed_chunks === job.total_chunks ? "Encoding and validating" : job.status === "running" ? "Synthesising" : "Queued";
    return `${stage}: ${job.completed_chunks}/${job.total_chunks} chunks${settings}`;
  }
  return `${job.status === "completed" ? "Ready to play" : job.status}${job.error ? ` (${job.error}). Try generating again.` : ""}${settings}`;
}
function downloadLinks(container, job) {
  container.replaceChildren(...job.outputs.map(output => {
    const link = element("a", `Download ${output.format.toUpperCase()} (${(output.byte_size / 1048576).toFixed(1)} MiB)`);
    link.href = output.url;
    // The server owns the safe, title-based filename in Content-Disposition.
    link.setAttribute("download", "");
    return link;
  }));
}
async function generateAudio(path, extra, preview) {
  const target = preview ? previewStatus : generationStatus;
  previewSubmit.disabled = true;
  target.textContent = "Submitting local narration…";
  try {
    const worker = await importRequest("/api/jobs/worker", {});
    if (!worker.backend_ready) throw new Error("Local narration needs the tts extra and chaptercast-models setup. Imported chapters remain in your library.");
    const job = await importRequest(path, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({...extra, voice: document.getElementById("voice").value,
        speed: Number(document.getElementById("speed").value), formats: ["opus", "m4a"]}),
    });
    if (preview) {
      previewJob = job.id;
      if (previewLoaded !== job.id) {
        previewAudio.pause();
        previewAudio.hidden = true;
        previewAudio.removeAttribute("src");
        document.getElementById("generation-downloads").replaceChildren();
      }
    }
    target.textContent = `Job ${job.id}: ${jobDescription(job)}`;
    await refreshLibrary();
  } catch (error) {
    target.textContent = `${error.message} If a submission reached the server, refresh the library before retrying.`;
  } finally {
    previewSubmit.disabled = false;
  }
}
document.getElementById("preview-form").addEventListener("submit", event => {
  event.preventDefault();
  generateAudio("/api/jobs/preview", {}, true);
});
async function cancelJob(id, target) {
  try {
    await importRequest(`/api/jobs/${id}/cancel`, {method: "POST"});
    await refreshLibrary();
  } catch (error) { target.textContent = error.message; }
}
function renderLibrary(data) {
  const ids = new Set(data.chapters.map(chapter => chapter.id));
  for (const [id, card] of chapterCards) {
    if (!ids.has(id)) { card.item.remove(); chapterCards.delete(id); }
  }
  for (const chapter of data.chapters) {
    let card = chapterCards.get(chapter.id);
    if (!card) {
      const item = element("li");
      const title = element("h3");
      const description = element("p");
      const controls = element("div", undefined, "actions");
      const generate = action("Generate audio", () => generateAudio("/api/jobs", {chapter_id: chapter.id}, false));
      const play = action("Open player", () => openPlayer(card.chapter, card.ready));
      controls.append(generate, play);
      item.append(title, description, controls);
      card = {item, title, description, generate, play};
      chapterCards.set(chapter.id, card);
    }
    card.chapter = chapter;
    card.ready = chapter.jobs.find(job => job.status === "completed" && job.outputs.length);
    card.title.textContent = chapter.title;
    card.description.textContent = jobDescription(chapter.jobs[0]);
    card.generate.disabled = chapter.jobs.some(job => ["queued", "running"].includes(job.status));
    card.play.hidden = !card.ready;
    card.play.textContent = card.ready && chapter.jobs[0].id !== card.ready.id ? "Open previous audio" : "Open player";
    // Moving only when necessary preserves focus during polling.
    if (libraryList.children[ data.chapters.indexOf(chapter) ] !== card.item) libraryList.append(card.item);
  }
  libraryStatus.textContent = data.total ? `${data.total} imported chapter${data.total === 1 ? "" : "s"}.` : "Your library is empty. Import a chapter below to get started.";
  document.getElementById("library-page").textContent = data.total ? `${libraryOffset + 1}–${Math.min(libraryOffset + data.limit, data.total)} of ${data.total}` : "";
  document.getElementById("library-previous").disabled = libraryOffset === 0;
  document.getElementById("library-next").disabled = libraryOffset + data.limit >= data.total;
  const worker = data.worker;
  document.getElementById("worker-status").textContent = !worker.backend_ready ? "Narration unavailable: install the tts extra and set up local models. Your library and completed audio remain available." : worker.worker_owned ? "Local narration ready. One queue worker." : "This process does not own the queue worker. Another local process may be generating; queued jobs wait for an available worker.";
  const active = data.jobs.filter(job => ["queued", "running"].includes(job.status));
  const activeIds = new Set(active.map(job => job.id));
  for (const [id, card] of queueCards) {
    if (!activeIds.has(id)) { card.item.remove(); queueCards.delete(id); }
  }
  for (const job of active.reverse()) {
    let card = queueCards.get(job.id);
    if (!card) {
      const item = element("li");
      const title = element("h3", job.title);
      const description = element("p");
      const progress = element("progress");
      progress.max = 1;
      progress.setAttribute("aria-label", `Job ${job.id} synthesis progress`);
      const cancel = action("Cancel generation", () => cancelJob(job.id, generationStatus));
      item.append(title, description, progress, cancel);
      card = {item, description, progress};
      queueCards.set(job.id, card);
      queueList.append(item);
    }
    card.description.textContent = `Job ${job.id}: ${jobDescription(job)}`;
    card.progress.value = job.progress;
  }
  document.getElementById("queue-empty").hidden = active.length > 0;
  const preview = data.jobs.find(job => job.id === previewJob) || data.jobs.find(job => job.preview);
  if (preview) {
    previewStatus.textContent = jobDescription(preview);
    if (preview.status === "completed" && preview.outputs.length && previewLoaded !== preview.id) {
      previewLoaded = preview.id;
      previewAudio.src = preferredOutput(preview).url;
      previewAudio.hidden = false;
      downloadLinks(document.getElementById("generation-downloads"), preview);
    }
  }
  if (!selectionRestored) {
    selectionRestored = true;
    const saved = readStorage("chaptercast-selected");
    const chapter = data.chapters.find(chapter => chapter.id === saved?.id);
    const job = chapter?.jobs.find(job => job.id === saved.job && job.status === "completed" && job.outputs.length);
    if (job) openPlayer(chapter, job, false);
    // Restore selections outside this page without fetching private chapter text.
    else if (saved?.offset > 0 && libraryOffset === 0) { libraryOffset = saved.offset; selectionRestored = false; }
  }
}
async function refreshLibrary() {
  if (libraryBusy) return;
  libraryBusy = true;
  refreshButton.disabled = true;
  clearTimeout(pollTimer);
  try {
    const data = await importRequest(`/api/library?offset=${libraryOffset}&limit=20`, {});
    renderLibrary(data);
  } catch (error) {
    libraryStatus.textContent = `${error.message} Library updates paused; submitted jobs continue on the server. Refresh to reconnect.`;
  } finally {
    libraryBusy = false;
    refreshButton.disabled = false;
    pollTimer = setTimeout(refreshLibrary, 2500);
  }
}
refreshButton.addEventListener("click", refreshLibrary);
document.getElementById("library-previous").addEventListener("click", () => { libraryOffset = Math.max(0, libraryOffset - 20); refreshLibrary(); });
document.getElementById("library-next").addEventListener("click", () => { libraryOffset += 20; refreshLibrary(); });

const player = document.getElementById("chapter-player");
const playerSection = document.getElementById("player-section");
const playerStatus = document.getElementById("player-status");
const positionStatus = document.getElementById("position-status");
const seek = document.getElementById("seek");
const rate = document.getElementById("playback-rate");
const format = document.getElementById("playback-format");
const toggle = document.getElementById("player-toggle");
let selected = null;
let selectionRestored = false;
let restorePosition = 0;
let saveReady = false;
let lastSaved = 0;
function readStorage(key) {
  try { return JSON.parse(localStorage.getItem(key)); }
  catch { positionStatus.textContent = "Browser storage is unavailable. Position cannot be restored after reload."; return null; }
}
function writeStorage(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); }
  catch { positionStatus.textContent = "Browser storage is unavailable or full. Position cannot be saved after reload."; }
}
function positionKey() { return `chaptercast-position-${selected.chapter.id}-${selected.job.content_key}-${selected.job.settings_key}`; }
function preferredOutput(job) {
  return job.outputs.find(output => output.format === (player.canPlayType('audio/ogg; codecs="opus"') ? "opus" : "m4a")) || job.outputs[0];
}
function timeLabel(seconds) {
  const value = Math.max(0, Math.floor(seconds || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}
function updateTime() {
  const duration = Number.isFinite(player.duration) ? player.duration : 0;
  seek.max = duration || 1;
  seek.value = player.currentTime;
  seek.setAttribute("aria-valuetext", `${timeLabel(player.currentTime)} of ${timeLabel(duration)}`);
  document.getElementById("player-time").textContent = `${timeLabel(player.currentTime)} / ${duration ? timeLabel(duration) : "Loading…"}`;
}
function savePosition(force = false) {
  if (!selected || !saveReady || !Number.isFinite(player.currentTime)) return;
  if (!force && Date.now() - lastSaved < 2000) return;
  lastSaved = Date.now();
  writeStorage(positionKey(), {seconds: player.ended ? 0 : player.currentTime, rate: Number(rate.value), format: format.value});
}
function loadMedia(seconds) {
  saveReady = false;
  restorePosition = seconds;
  playerStatus.textContent = "Loading chapter audio…";
  for (const control of [toggle, seek, document.getElementById("skip-back"), document.getElementById("skip-forward")]) control.disabled = true;
  player.src = selected.job.outputs.find(output => output.format === format.value).url;
  player.load();
}
function openPlayer(chapter, job, focus = true) {
  if (!job) return;
  if (selected?.chapter.id === chapter.id && selected.job.id === job.id) {
    if (player.error) loadMedia(readStorage(positionKey())?.seconds || 0);
    if (focus) { playerSection.scrollIntoView({block: "start"}); toggle.focus({preventScroll: true}); }
    return;
  }
  savePosition(true);
  saveReady = false;
  player.pause();
  selected = {chapter, job};
  const saved = readStorage(positionKey());
  rate.value = ["0.5", "0.75", "1", "1.25", "1.5", "1.75", "2"].includes(String(saved?.rate)) ? String(saved.rate) : "1";
  format.replaceChildren(...job.outputs.map(output => {
    const option = element("option", output.format.toUpperCase()); option.value = output.format; return option;
  }));
  format.value = job.outputs.some(output => output.format === saved?.format) ? saved.format : preferredOutput(job).format;
  document.getElementById("player-title").textContent = chapter.title;
  downloadLinks(document.getElementById("player-downloads"), job);
  playerSection.hidden = false;
  writeStorage("chaptercast-selected", {id: chapter.id, job: job.id, offset: libraryOffset});
  loadMedia(Number.isFinite(saved?.seconds) && saved.seconds > 0 ? saved.seconds : 0);
  if (focus) { playerSection.scrollIntoView({block: "start"}); document.getElementById("player-heading").tabIndex = -1; document.getElementById("player-heading").focus({preventScroll: true}); }
}
player.addEventListener("loadedmetadata", () => {
  if (!selected || !Number.isFinite(player.duration) || player.duration <= 0) return;
  player.playbackRate = Number(rate.value);
  player.currentTime = Math.min(restorePosition, Math.max(0, player.duration - 0.1));
  saveReady = true;
  for (const control of [toggle, seek, document.getElementById("skip-back"), document.getElementById("skip-forward")]) control.disabled = false;
  playerStatus.textContent = restorePosition > 0 ? `Restored position at ${timeLabel(player.currentTime)}. Press Play to continue.` : "Ready. Press Play to start streaming.";
  updateTime();
});
toggle.addEventListener("click", async () => {
  if (!player.paused) player.pause();
  else {
    previewAudio.pause();
    try { await player.play(); }
    catch { playerStatus.textContent = "Playback could not start. Try Play again, change format, or download the file."; }
  }
});
player.addEventListener("play", () => { previewAudio.pause(); toggle.textContent = "Pause"; });
player.addEventListener("playing", () => { playerStatus.textContent = "Playing from this server."; });
player.addEventListener("pause", () => { toggle.textContent = "Play"; savePosition(true); if (saveReady) playerStatus.textContent = "Paused."; });
player.addEventListener("waiting", () => { if (saveReady) playerStatus.textContent = "Buffering audio from this server…"; });
player.addEventListener("error", () => {
  saveReady = false;
  for (const control of [toggle, seek, document.getElementById("skip-back"), document.getElementById("skip-forward")]) control.disabled = true;
  playerStatus.textContent = "Audio could not be loaded. Check the server connection, reopen the player, or choose another format. Downloads also require a completed, intact file.";
});
player.addEventListener("timeupdate", () => { updateTime(); savePosition(); });
player.addEventListener("seeked", () => savePosition(true));
player.addEventListener("ended", () => { savePosition(true); playerStatus.textContent = "Chapter finished. Saved position reset to the beginning."; });
previewAudio.addEventListener("play", () => player.pause());
previewAudio.addEventListener("error", () => { previewStatus.textContent = "Preview audio could not be loaded. Check the connection or download another format."; });
rate.addEventListener("change", () => { player.playbackRate = Number(rate.value); savePosition(true); });
player.addEventListener("ratechange", () => {
  if ([...rate.options].some(option => Number(option.value) === player.playbackRate)) rate.value = String(player.playbackRate);
  savePosition(true);
});
format.addEventListener("change", () => { const seconds = player.currentTime; savePosition(true); player.pause(); loadMedia(seconds); });
seek.addEventListener("input", () => { if (saveReady) player.currentTime = Math.min(Number(seek.value), player.duration); });
for (const [id, delta] of [["skip-back", -15], ["skip-forward", 15]]) {
  document.getElementById(id).addEventListener("click", () => { if (saveReady) player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + delta)); });
}
window.addEventListener("pagehide", () => savePosition(true));
document.addEventListener("visibilitychange", () => { if (document.hidden) savePosition(true); });
refreshLibrary();
