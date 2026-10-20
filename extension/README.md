# Chat Scraper (Chrome extension)

Captures a Whatnot live show's chat with timestamps, survives Whatnot
re-rendering the chat, hands every line to the background worker, which
stores it in IndexedDB and uploads it to the API. Chat Search
(`search.html`) searches saved shows or a downloaded `.txt`; Settings
(`options.html`) holds the server URL and device key.

## Build and load

```bash
npm install
npm run build          # dist/ — load this folder unpacked at chrome://extensions
npm run test           # Vitest unit tests (capture module, storage, migration)
npm run test:e2e       # builds dist-test/ and drives it with Playwright on the fixture page
```

`dist-test/` is the same bundle with `http://127.0.0.1/*` added to the
content-script matches so the fixture page at `e2e/fixture/chat.html` can
load it. Never load `dist-test/` for real use.

## How capture works

- `src/capture/` is pure DOM code with no `chrome.*` calls, so it runs in
  jsdom. `row.ts` turns the message you click into a CSS selector,
  `recorder.ts` watches the chat container with a MutationObserver and emits
  `CapturedRow`s, `dedup.ts` drops re-rendered copies, `text.ts` splits
  username from body.
- Rows are `{ id, showId, username, body, rawText, capturedAt, seq }`. `id`
  hashes show + username + body + a 2-second bucket, so a retry or a
  re-render sends the same id and the store ignores it.
- Self-healing: a watchdog re-detects the rows when the container leaves the
  page or when the chat keeps changing but nothing matches the selector
  (Whatnot shipped new class names). If nothing row-like is found it says so
  in the panel instead of silently stopping.
- A full re-render of the list (old rows back as new nodes, in order) is
  recognised by matching the batch against the recorded order, while a new
  message in the same batch is still recorded.
- The content script never observes `document.body`; it watches the chat's
  scroll container, or the row's list when the chat does not overflow yet.

## Upload

Rows are stored with `uploaded = 0` and drained by `src/upload/uploader.ts`
in batches of up to 200 per show to `POST /api/ingest`. Every row is cut to
the server's limits (`src/upload/limits.ts`, mirroring
`shared/chat-event-batch.schema.json`) when it is stored and again when it is
sent. A flush runs 2 s after new rows, on a retry alarm with exponential
backoff (5 s … 5 min) after a network failure, every minute as a sweep, when
the connection changes, and from the Settings page. A rejected key stops
retries until the connection changes; rows stay queued. A batch the server
refuses (400) is set aside (`uploaded = 2`) so it can never stall the queue;
Settings shows how many lines were set aside and why.

## Connection security

- The server address must be `https://` (plain `http://` only for
  localhost); fetches refuse redirects.
- The device key lives in the extension's own IndexedDB, not in
  `chrome.storage.local`, so the content script on whatnot.com cannot read it.
  It is bound to the server origin it was issued for: change the address and
  the key is not sent there; pair again.
- Connecting to a different shop than before sets unsent lines aside instead
  of uploading another shop's chat. Disconnect revokes the key on the server
  (`POST /api/ingest/disconnect`).
- The background only answers messages from this extension
  (`sender.id`), page-only commands are refused from content scripts, and the
  panel ignores synthetic (`!isTrusted`) clicks and inputs from page scripts.

The panel's second line shows
`saved N · uploaded` / `N to upload — <reason>`.

## Storage

The content script runs in Whatnot's origin, so IndexedDB there would not be
visible to the extension's own pages. Rows go to the background service
worker (`src/background.ts`) which owns the `chat-scraper` IndexedDB
(`src/storage/db.ts`): `shows`, `rows` (indexed by show + seq), `kv`. The
first run migrates v1.2's `chrome.storage.local` keys. Watch words stay in
`chrome.storage.local`.
