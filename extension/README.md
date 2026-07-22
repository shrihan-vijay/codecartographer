# CodeCartographer Chrome extension

A side panel client for the Phase 3 agentic Q&A backend. See
`specs/phase-4-chrome-extension.md` for the design. No build step — plain
HTML/CSS/JS, loaded directly by Chrome.

## Local development

1. Have the backend running against an already-indexed repo:
   ```
   uv run uvicorn codecartographer.api:app
   ```
2. In Chrome, go to `chrome://extensions`, enable "Developer mode" (top right),
   click "Load unpacked", and select this `extension/` directory.
3. Click the extension's toolbar icon to open the side panel. Enter the
   indexed repo's path and a question.
4. If the backend isn't at the default `http://localhost:8000`, open the
   extension's options page (right-click the toolbar icon → "Options", or the
   "Backend settings" link in the side panel) and change it — Chrome will
   prompt for permission to reach the new origin the first time.

Reloading after an edit: click the refresh icon for the extension on
`chrome://extensions`, then reopen the side panel.

## Packaging for the Chrome Web Store

No build step is needed — the Store accepts the source directory zipped as-is:

```
cd extension
zip -r ../codecartographer-extension.zip . -x '*.DS_Store'
```

Upload the resulting zip through the [Chrome Web Store developer
dashboard](https://chrome.google.com/webstore/devconsole) (requires a
one-time $5 developer registration fee and goes through manual review before
it's publicly listed — both outside the scope of this repo).
