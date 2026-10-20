import 'fake-indexeddb/auto'

// jsdom has no CSS.escape; Chrome does.
if (typeof globalThis.CSS === 'undefined' || !globalThis.CSS.escape) {
  Object.defineProperty(globalThis, 'CSS', {
    value: { escape: (s: string) => s.replace(/([^\w-])/g, '\\$1') },
    configurable: true,
  })
}
