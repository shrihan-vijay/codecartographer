const DEFAULT_BACKEND_URL = "http://localhost:8000";

const form = document.getElementById("ask-form");
const repoPathInput = document.getElementById("repo-path");
const questionInput = document.getElementById("question");
const errorEl = document.getElementById("error");
const progressEl = document.getElementById("progress");
const answerEl = document.getElementById("answer");
const citationsEl = document.getElementById("citations");
const submitBtn = document.getElementById("submit-btn");

async function loadSettings() {
  const { backendUrl, repoPath } = await chrome.storage.local.get(["backendUrl", "repoPath"]);
  if (repoPath) repoPathInput.value = repoPath;
  return backendUrl || DEFAULT_BACKEND_URL;
}

function describeToolCall(toolName, args) {
  switch (toolName) {
    case "search_code":
      return `Searching for "${args.query}"...`;
    case "get_callers":
      return `Finding callers of ${args.symbol_name}...`;
    case "get_callees":
      return `Finding what ${args.symbol_name} calls...`;
    case "read_source":
      return `Reading ${args.file_path}:${args.start_line}-${args.end_line}...`;
    default:
      return `Calling ${toolName}...`;
  }
}

function addProgressStep(text) {
  const previous = progressEl.querySelector("li.active");
  if (previous) previous.classList.remove("active");
  const li = document.createElement("li");
  li.className = "active";
  li.textContent = text;
  progressEl.appendChild(li);
}

function renderFinal(data) {
  const activeStep = progressEl.querySelector("li.active");
  if (activeStep) activeStep.classList.remove("active");
  answerEl.textContent = data.answer;
  for (const c of data.citations) {
    const li = document.createElement("li");
    const lines = c.start_line
      ? `:${c.start_line}${c.end_line && c.end_line !== c.start_line ? `-${c.end_line}` : ""}`
      : "";
    li.textContent = `${c.file_path}${lines}${c.symbol_name ? `  (${c.symbol_name})` : ""}`;
    citationsEl.appendChild(li);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const backendUrl = await loadSettings();
  const repoPath = repoPathInput.value;
  const question = questionInput.value;

  submitBtn.disabled = true;
  errorEl.textContent = "";
  answerEl.textContent = "";
  citationsEl.innerHTML = "";
  progressEl.innerHTML = "";
  addProgressStep("Thinking...");

  try {
    const response = await fetch(`${backendUrl}/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repo_path: repoPath, question }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || `Request failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newlineIndex;
      while ((newlineIndex = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newlineIndex);
        buffer = buffer.slice(newlineIndex + 1);
        if (!line.trim()) continue;

        const evt = JSON.parse(line);
        if (evt.type === "tool_call") {
          addProgressStep(describeToolCall(evt.tool_name, evt.arguments));
        } else if (evt.type === "final") {
          renderFinal(evt);
        } else if (evt.type === "error") {
          throw new Error(evt.detail);
        }
      }
    }

    await chrome.storage.local.set({ repoPath });
  } catch (err) {
    errorEl.textContent =
      err instanceof TypeError
        ? `Couldn't reach ${backendUrl} — is the backend running, and its origin granted in Backend settings?`
        : err.message;
  } finally {
    submitBtn.disabled = false;
  }
});

loadSettings();
