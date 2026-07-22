# Phase 4: Chrome extension client for cited codebase Q&A

Status: Implemented

## Goal

Make the Phase 3 agentic Q&A tool usable as a Chrome side panel instead of a separate browser
tab, and package it so it's installable from (and eventually publishable to) the Chrome Web
Store — a shippable artifact on top of the existing agent/graph/embeddings work, not a new
backend capability.

## Scope

- In scope: a Manifest V3 extension with a side panel UI that is a near-verbatim port of
  `static/index.html`'s existing streaming Q&A UX (question box, live tool-call progress,
  answer + citations); an options page to configure the backend base URL (default
  `http://localhost:8000`) and remember it plus the last-used `repo_path` via
  `chrome.storage.local`; runtime permission handling so the extension can call a local backend
  on an arbitrary host/port without a server-side CORS change; extension icons; a packaging
  path (zip the source directory, no build step) suitable for both "Load unpacked" during dev
  and Chrome Web Store submission.
- Out of scope: any GitHub/GitLab page integration (auto-detecting `repo_path` from the current
  tab, inline code annotations, text-selection-to-question) — genuinely valuable but a separate
  follow-on once the standalone side panel works; multi-repo switching UI beyond one remembered
  `repo_path`; auth/multi-user support; a remotely-hosted backend (the extension only ever talks
  to a backend the user is already running themselves, same trust boundary as Phase 3's local
  web UI); actually submitting to and paying for a Chrome Web Store developer account — a manual
  step for the user to take once the extension works, not something this phase automates.

## Design

**No backend changes.** Chrome extension pages (side panel, options page) and the background
service worker are exempt from CORS when the target origin is covered by a granted
`host_permissions` entry — this is documented MV3 behavior, not a workaround. So `POST
/ask/stream` and `GET /` on `codecartographer.api` are consumed exactly as they exist today;
this phase adds no server-side code. If a real browser test shows this assumption wrong, that's
a spec-invalidating finding to fix before calling this phase done, not a "quick CORS middleware
patch" to bolt on.

**New top-level `extension/` directory**, sibling to `src/`, not inside
`src/codecartographer/static/`. The extension is a separate deployable artifact (zipped for the
Store, never served by FastAPI) — `static/` stays as-is since the backend still serves its own
plain web UI independently of the extension.

```
extension/
  manifest.json
  sidepanel.html       # port of static/index.html's form/progress/answer/citations markup
  sidepanel.js         # same streaming-fetch logic, plus chrome.storage.local read/write
  options.html
  options.js
  icons/16.png 48.png 128.png
  README.md            # load-unpacked instructions + how to zip for Store submission
```

**`manifest.json`** (Manifest V3):
- `permissions`: `["storage", "sidePanel"]`.
- `host_permissions`: `["http://localhost:8000/*"]` — covers the documented default out of the
  box with no permission prompt.
- `optional_host_permissions`: `["http://*/*", "https://*/*"]` — if the user points the options
  page at a different host/port (e.g. a different local port), `options.js` calls
  `chrome.permissions.request({origins: [...]})` for that specific origin from the save button's
  click handler (required to be a user gesture under MV3). Declaring the broad pattern as
  *optional* rather than *required* keeps the default install permission-light, which also
  matters for eventual Store review — reviewers scrutinize broad required host permissions.
- `side_panel.default_path`: `sidepanel.html`.
- `options_page`: `options.html`.

**`sidepanel.js`** carries over `static/index.html`'s existing logic (progress-step rendering
per `tool_call` event, final answer + citations rendering, ndjson stream parsing over
`fetch().body.getReader()`) essentially unchanged, with three additions:
1. On load, read `backendUrl` and `repoPath` from `chrome.storage.local` and pre-fill the form.
2. Fetch target becomes `${backendUrl}/ask/stream` instead of the relative `/ask/stream` that
   works today only because the static page is served by the same FastAPI process.
3. On a successful ask, persist the entered `repoPath` back to `chrome.storage.local` so it's
   remembered next time the side panel opens (this phase's only fix to Phase 3's "retype the
   repo path every time" gap — full auto-detection stays out of scope, see above).

**`options.js`**: a form with `backendUrl` (default `http://localhost:8000`) and `repoPath`
fields. Save handler checks `chrome.permissions.contains` for the entered origin; if not
already granted, calls `chrome.permissions.request` before writing to `chrome.storage.local`,
surfacing a clear error if the user declines the prompt rather than silently failing later on
first fetch.

**No bundler.** Consistent with Phase 3's "no Node/npm build step" for `static/index.html`, the
extension is plain HTML/CSS/JS loaded directly via `chrome://extensions` → "Load unpacked" for
dev, and packaged for Store submission by zipping the same `extension/` directory — no
compilation step to keep in sync with the backend.

**Icons**: a simple placeholder mark (e.g. monospace "cc" glyph) at 16/48/128px — required by
the manifest and by Store listing requirements; not a design investment beyond "exists and looks
intentional."

## Acceptance criteria

- [x] `chrome://extensions` → "Load unpacked" on `extension/` loads with no manifest errors and
      requests only `storage`, `sidePanel`, and the default `localhost:8000` host permission.
      Confirmed by the user (extension ID `boghmjjhbjnjiopldkjjmgcacnmomfmj`).
- [x] With `uvicorn codecartographer.api:app` running locally against an already-indexed repo,
      opening the side panel, entering the repo path and a question, and submitting shows the
      same live tool-call progress steps as the existing `static/index.html` UI, followed by an
      answer — without opening a separate browser tab. Confirmed by screenshot: "Thinking..." →
      "Searching for 'indexer functionality'..." → full answer. Citations came back empty in this
      run because `llama3.1` ended its turn with free text instead of calling
      `provide_final_answer` — the exact tradeoff Phase 3's spec already documents, not a Phase 4
      bug; the side panel correctly rendered the (empty) citations list it was given.
- [x] Confirms the CORS-exemption design assumption above by observation: the request from the
      side panel to `/ask/stream` succeeded end-to-end with no CORS error blocking it, and no
      CORS middleware was added to `codecartographer.api` to make it work.
- [x] Changing the backend URL in the options page to a non-default origin (e.g. a different
      port) triggers a visible Chrome permission prompt. Confirmed by the user.
- [x] `repoPath` persists across closing and reopening the side panel (via `chrome.storage.local`)
      so it doesn't need to be retyped every time. Confirmed by the user.
- [x] `extension/README.md` documents load-unpacked steps for local dev and how to zip the
      directory for Chrome Web Store submission (the submission itself — developer account,
      $5 one-time registration fee, manual review — is a follow-up action for the user, not part
      of this phase's acceptance).
