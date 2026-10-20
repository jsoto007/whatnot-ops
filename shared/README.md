# shared

The contract between the extension and the API lives here from Phase 2:
`chat-event-batch.schema.json`, validated with zod on the extension side and
`jsonschema` on the Flask side, with the same fixture files run through both.
