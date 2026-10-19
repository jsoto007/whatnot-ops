// Toolbar button shows or hides the panel on the current Whatnot tab.
chrome.action.onClicked.addListener(tab => {
  chrome.tabs.sendMessage(tab.id, { cmd: 'toggle' }).catch(() => {
    // Not a Whatnot page: open Chat Search instead.
    chrome.tabs.create({ url: chrome.runtime.getURL('search.html') });
  });
});

chrome.runtime.onMessage.addListener(msg => {
  if (msg.cmd === 'openSearch') chrome.tabs.create({ url: chrome.runtime.getURL('search.html') });
});
