# shared

The contract between the extension and the API.

- `chat-event-batch.schema.json` — JSON Schema (draft 2020-12) for the body of
  `POST /api/ingest`. The extension validates with Ajv before sending
  (`extension/src/upload/schema.ts`), the API validates with `jsonschema`
  (`api/app/services/ingest.py`).
- `fixtures/` — example batches, valid and invalid. Both sides run the same
  files through their validator in their test suites, so a schema change
  that breaks one side fails that side's tests. Both read every
  `batch-valid*` and `batch-invalid*` file in the folder.
- A new field is added as optional (`show.channel` and `show.movedOnAt`, Chat
  Scraper 1.10.0), so laptops still on an older build keep uploading. Deploy the
  API before the extension: the API refuses a field it does not know.
  Rolling the API back below that after a laptop installed the build: the
  extension sends a refused batch of lines once more without `show.channel`
  (only for the API's own `show: fails additionalProperties` 400, never for a
  422 or any other 400), so lines are not lost, but the old API then cannot
  check the channel. A moved-on report it refuses stays owed until the API is
  ahead again. Roll the extension back with the API when you can.
- `show.channel` is the channel the page showed when the batch's lines were
  captured. A line has no channel field in the contract: the extension keeps
  each line's own and sends one batch per show and channel (none when none was
  read), never a channel the show learned later.
- A batch has at least one row, except the laptop's word that the show's page
  moved on to another channel: `show.movedOnAt` with `rows: []`, sent once per
  move after the show's rows (the schema's `if`/`then`).
