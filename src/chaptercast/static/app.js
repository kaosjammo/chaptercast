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
