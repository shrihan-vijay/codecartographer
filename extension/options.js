const DEFAULT_BACKEND_URL = "http://localhost:8000";

const form = document.getElementById("settings-form");
const backendUrlInput = document.getElementById("backend-url");
const repoPathInput = document.getElementById("repo-path");
const statusEl = document.getElementById("status");

async function load() {
  const { backendUrl, repoPath } = await chrome.storage.local.get(["backendUrl", "repoPath"]);
  backendUrlInput.value = backendUrl || DEFAULT_BACKEND_URL;
  if (repoPath) repoPathInput.value = repoPath;
}

function showStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showStatus("", "");

  let origin;
  try {
    origin = new URL(backendUrlInput.value).origin;
  } catch {
    showStatus("Enter a full URL, e.g. http://localhost:8000", "error");
    return;
  }

  const originPattern = `${origin}/*`;
  const alreadyGranted = await chrome.permissions.contains({ origins: [originPattern] });

  if (!alreadyGranted) {
    // Must run inside this click handler's user-gesture context, or Chrome silently rejects it.
    const granted = await chrome.permissions.request({ origins: [originPattern] });
    if (!granted) {
      showStatus(
        `Permission to reach ${origin} was declined, so the side panel won't be able to call it.`,
        "error"
      );
      return;
    }
  }

  await chrome.storage.local.set({
    backendUrl: backendUrlInput.value.replace(/\/$/, ""),
    repoPath: repoPathInput.value,
  });
  showStatus("Saved.", "success");
});

load();
