# shared

The contract between the extension and the API.

- `chat-event-batch.schema.json` — JSON Schema (draft 2020-12) for the body of
  `POST /api/ingest`. The extension validates with Ajv before sending
  (`extension/src/upload/schema.ts`), the API validates with `jsonschema`
  (`api/app/services/ingest.py`).
- `fixtures/` — example batches, valid and invalid. Both sides run the same
  files through their validator in their test suites, so a schema change
  that breaks one side fails that side's tests.
