/**
 * Extension pages double-clicked in the unzipped folder run as plain file pages:
 * Chrome still defines `window.chrome`, but with no extension APIs, so they can
 * neither read saved shows nor connect anything.
 */
export const isExtensionPage = (): boolean => !!globalThis.chrome?.runtime?.id

/** Replaces the page's main content with how to open it from Chrome instead. */
export function showOpenedAsFile(page: string, cannot: string): void {
  const p = Object.assign(document.createElement('p'), {
    id: 'not-installed',
    textContent:
      `This copy of ${page} was opened as a file, so it cannot ${cannot}. ` +
      'Install the folder with Load unpacked in chrome://extensions, then open it from Chrome: ' +
      'click the Chat Scraper icon in the toolbar (on Whatnot it shows the panel, anywhere else Chat Search, ' +
      'and both have Settings), or right-click the icon and choose Options.',
  })
  p.style.color = '#FF8A80'
  document.querySelector('main')?.replaceChildren(p)
}
