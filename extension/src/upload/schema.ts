// The shared contract, validated before every send so a bad row is caught on the laptop, not by a 400.
import Ajv2020 from 'ajv/dist/2020'
import addFormats from 'ajv-formats'
import schema from '../../../shared/chat-event-batch.schema.json'
import type { ChatEventBatch } from './uploader'

const ajv = new Ajv2020({ allErrors: true, strict: true })
addFormats(ajv)
const validator = ajv.compile<ChatEventBatch>(schema)

export function validateBatch(batch: unknown): { ok: true } | { ok: false; errors: string[] } {
  if (validator(batch)) return { ok: true }
  return {
    ok: false,
    errors: (validator.errors ?? []).map((e) => `${e.instancePath || '$'}: ${e.message ?? 'invalid'}`),
  }
}
