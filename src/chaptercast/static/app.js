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
  const response = await fetch(path, init);
  const data = await response.json();
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
    const item = document.createElement("li");
    item.textContent = `${result.title} — ${result.paragraphs} paragraphs, ${result.characters} characters`;
    document.getElementById("imported-chapters").prepend(item);
    importStatus.textContent = `Imported “${result.title}” privately. Narration is not available yet.`;
  } catch (error) {
    importStatus.textContent = error.message || "Could not reach the server. Try again.";
    fallback.hidden = error.code !== "ffn_blocked";
  } finally {
    submit.disabled = false;
  }
});
