// Chat Scraper v1.2 — built on Sparkles's proven click-to-pick console script.
(() => {
  if (window.__csExt) return;
  window.__csExt = true;

  const STORE_KEY = 'cs_session';
  const WORDS_KEY = 'cs_watchwords';
  const RESUME_HOURS = 12;

  let log = [];             // { time, text, ts }
  let seen = new WeakSet();
  let observer = null, rowSelector = null, chatBox = null;
  let state = 'idle';       // idle | picking | recording | stopped
  let startedAt = null;
  let watchWords = [];
  let dirty = false;

  // ---------- capture engine (same logic as the console script) ----------
  const clean = t => (t || '').replace(/\s*\n\s*/g, ' | ').trim();
  const stamp = () => new Date().toLocaleTimeString();
  const sig = el => el.tagName + '|' + el.className;

  function isDuplicate(text, initial) {
    if (initial) {
      // After a resume, messages still on screen are already saved.
      return log.slice(-300).some(r => r.text === text);
    }
    // Skip Whatnot re-drawing the same message, but keep a real repeat said later.
    const now = Date.now();
    for (let i = log.length - 1; i >= 0 && now - log[i].ts < 5000; i--) {
      if (log[i].text === text) return true;
    }
    return false;
  }

  function record(el, initial) {
    if (seen.has(el)) return;
    seen.add(el);
    const text = clean(el.innerText);
    if (!text || isDuplicate(text, initial)) return;
    const row = { time: stamp(), text, ts: Date.now() };
    log.push(row);
    dirty = true;
    if (matchWord(text)) addFlag(row);
    if (findTerm) queueFind();
    updateCount();
  }

  function scan(node, initial) {
    if (!node || node.nodeType !== 1 || !rowSelector) return;
    if (host.contains(node)) return;
    if (node.matches(rowSelector)) record(node, initial);
    node.querySelectorAll(rowSelector).forEach(el => record(el, initial));
  }

  function scrollParent(el) {
    while (el && el !== document.body) {
      const s = getComputedStyle(el);
      if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) return el;
      el = el.parentElement;
    }
    return document.body;
  }

  function findRow(target, box) {
    let best = null, bestCount = 1, el = target;
    while (el && el !== box && el.parentElement) {
      const count = [...el.parentElement.children].filter(c => sig(c) === sig(el)).length;
      if (count >= bestCount) { best = el; bestCount = count; }
      el = el.parentElement;
    }
    return bestCount >= 3 ? best : null;
  }

  function buildSelector(el) {
    const tag = el.tagName.toLowerCase();
    const classes = [...el.classList].map(c => '.' + CSS.escape(c)).join('');
    return tag + classes;
  }

  function onPick(e) {
    if (e.composedPath().includes(host)) return; // clicks on our own panel
    e.preventDefault(); e.stopPropagation();
    document.removeEventListener('click', onPick, true);
    const box = scrollParent(e.target);
    const row = findRow(e.target, box);
    if (!row) {
      setStatus("Couldn't recognise that as a chat message. Press Start and click a different message.", 'bad');
      state = log.length ? 'stopped' : 'idle';
      render();
      return;
    }
    rowSelector = buildSelector(row);
    chatBox = box;
    box.style.outline = '3px solid #69BE28';
    setTimeout(() => (box.style.outline = ''), 4000);

    if (!startedAt) startedAt = new Date().toISOString();
    scan(box, true);
    observer = new MutationObserver(muts => muts.forEach(m => m.addedNodes.forEach(n => scan(n, false))));
    observer.observe(document.body, { childList: true, subtree: true });
    state = 'recording';
    setStatus('Watching chat.', 'good');
    save();
    render();
  }

  function startPicking() {
    state = 'picking';
    setStatus('Now click one chat message (on the words, not the name).', 'info');
    render();
    document.addEventListener('click', onPick, true);
  }

  function stopRecording() {
    if (observer) observer.disconnect();
    observer = null;
    document.removeEventListener('click', onPick, true);
    state = log.length ? 'stopped' : 'idle';
    save();
  }

  // ---------- auto-save ----------
  const MAX_SHOWS = 30;
  function save() {
    dirty = false;
    try {
      const now = Date.now();
      const data = { [STORE_KEY]: { log, startedAt, updatedAt: now, url: location.pathname } };
      if (startedAt && log.length) {
        const id = 'cs_show_' + startedAt;
        data[id] = { log, startedAt, updatedAt: now };
        chrome.storage.local.get('cs_index', r => {
          let idx = (r.cs_index || []).filter(x => x.id !== id);
          idx.unshift({ id, startedAt, updatedAt: now, count: log.length, date: showDate(startedAt) });
          idx.sort((a, b) => b.startedAt.localeCompare(a.startedAt));
          const drop = idx.slice(MAX_SHOWS).map(x => x.id);
          idx = idx.slice(0, MAX_SHOWS);
          data.cs_index = idx;
          chrome.storage.local.set(data, () => { if (drop.length) chrome.storage.local.remove(drop); });
        });
      } else {
        chrome.storage.local.set(data);
      }
    } catch (e) { /* extension reloaded; ignore */ }
  }
  setInterval(() => { if (dirty) save(); }, 3000);
  window.addEventListener('beforeunload', () => { if (dirty) save(); });

  // ---------- show date (Chicago time, before 5 AM counts as the night before) ----------
  function showDate(iso) {
    const d = new Date(iso || Date.now());
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/Chicago', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', hour12: false
    }).formatToParts(d).map(p => [p.type, p.value]));
    let date = new Date(Date.UTC(+parts.year, +parts.month - 1, +parts.day));
    if (+parts.hour % 24 < 5) date = new Date(date.getTime() - 86400000);
    return date.toISOString().slice(0, 10);
  }

  // ---------- download ----------
  function download(label) {
    if (!log.length) { setStatus('Nothing captured yet.', 'bad'); return; }
    const flagged = log.filter(r => matchWord(r.text));
    const lines = log.map(r => r.time + '\t' + r.text);
    const header = [
      '# Whatnot show chat — ' + showDate(startedAt),
      '# Started: ' + (startedAt ? new Date(startedAt).toLocaleString() : 'unknown'),
      '# Saved:   ' + new Date().toLocaleString(),
      '# Messages: ' + log.length,
      ''
    ];
    const footer = flagged.length ? [
      '', '# FLAGGED (' + watchWords.join(', ') + ')',
      ...flagged.map(r => '# ' + r.time + '\t' + r.text)
    ] : [];
    const body = [...header, ...lines, ...footer].join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([body], { type: 'text/plain' }));
    a.download = `Whatnot_${showDate(startedAt)}_${label}.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    setStatus('Downloaded ' + log.length + ' messages.', 'good');
  }

  // ---------- watch words ----------
  function matchWord(text) {
    if (!watchWords.length) return false;
    const t = text.toLowerCase();
    return watchWords.some(w => t.includes(w));
  }
  function setWords(str) {
    watchWords = str.split(',').map(w => w.trim().toLowerCase()).filter(Boolean);
    try { chrome.storage.local.set({ [WORDS_KEY]: watchWords }); } catch (e) {}
    rebuildFlags();
  }

  // ---------- panel ----------
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;';
  const root = host.attachShadow({ mode: 'open' });
  root.innerHTML = `
  <style>
    :host { all: initial; }
    .p { width: 290px; font: 13px/1.4 system-ui, -apple-system, Segoe UI, sans-serif; color: #fff;
         background: #002244; border: 2px solid #69BE28; border-radius: 12px; box-shadow: 0 6px 24px rgba(0,0,0,.4); overflow: hidden; }
    .hd { display: flex; align-items: center; gap: 8px; padding: 8px 10px; background: #001833; cursor: default; }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: #A5ACAF; flex: none; }
    .dot.on { background: #69BE28; animation: pulse 1.4s infinite; }
    .dot.pick { background: #4FC3F7; }
    @keyframes pulse { 50% { opacity: .35; } }
    .ttl { font-weight: 700; flex: 1; }
    .cnt { font-variant-numeric: tabular-nums; color: #69BE28; font-weight: 700; }
    .x { background: none; border: 0; color: #A5ACAF; font-size: 16px; cursor: pointer; padding: 0 2px; }
    .bd { padding: 10px; display: grid; gap: 8px; }
    .bd.min { display: none; }
    .st { font-size: 12px; color: #A5ACAF; min-height: 16px; }
    .st.good { color: #69BE28; } .st.bad { color: #FF8A80; } .st.info { color: #4FC3F7; }
    .row { display: flex; gap: 6px; flex-wrap: wrap; }
    button.b { flex: 1; border: 0; border-radius: 8px; padding: 7px 8px; font: 600 12px system-ui, sans-serif; cursor: pointer;
               background: #69BE28; color: #002244; }
    button.b.sec { background: #0B3A66; color: #fff; }
    button.b:disabled { opacity: .4; cursor: default; }
    label { font-size: 11px; color: #A5ACAF; display: block; margin-bottom: 3px; }
    input { width: 100%; box-sizing: border-box; border: 1px solid #0B3A66; background: #001833; color: #fff;
            border-radius: 6px; padding: 6px; font: 12px system-ui, sans-serif; }
    .fl { max-height: 150px; overflow-y: auto; display: grid; gap: 4px; }
    .fi { background: #0B3A66; border-left: 3px solid #69BE28; border-radius: 4px; padding: 4px 6px; font-size: 12px; word-break: break-word; }
    .fi b { color: #69BE28; font-weight: 600; margin-right: 4px; }
    .fr .fi, #fr .fi { border-left-color: #4FC3F7; cursor: pointer; }
    #fr .fi:hover { background: #124a80; }
    #fr { margin-top: 6px; }
    .empty { font-size: 11px; color: #A5ACAF; }
  </style>
  <div class="p">
    <div class="hd">
      <span class="dot" id="dot"></span><span class="ttl">Chat Scraper</span>
      <span class="cnt" id="cnt">0</span>
      <button class="x" id="min" title="Minimise">–</button>
    </div>
    <div class="bd" id="bd">
      <div class="st" id="st">Ready. Open the show, then press Start.</div>
      <div class="row" id="resumeRow" hidden>
        <button class="b" id="resume">Resume last show</button>
        <button class="b sec" id="fresh">Start fresh</button>
      </div>
      <div class="row">
        <button class="b" id="start">Start</button>
        <button class="b" id="stop">Stop &amp; Save</button>
      </div>
      <div class="row">
        <button class="b" id="search">Open Chat Search</button>
      </div>
      <div class="row">
        <button class="b sec" id="backup">Save backup copy</button>
        <button class="b sec" id="clear">Clear</button>
      </div>
      <div>
        <label for="find">Find buyer (live)</label>
        <input id="find" placeholder="type a username">
        <div class="fl" id="fr" hidden></div>
      </div>
      <div>
        <label for="words">Watch words / usernames (comma separated)</label>
        <input id="words" placeholder="mine, sold, claim">
      </div>
      <div>
        <label>Flagged messages</label>
        <div class="fl" id="fl"><div class="empty">Nothing flagged yet.</div></div>
      </div>
    </div>
  </div>`;
  const $ = id => root.getElementById(id);

  function setStatus(msg, kind) { const s = $('st'); s.textContent = msg; s.className = 'st ' + (kind || ''); }
  function updateCount() { $('cnt').textContent = log.length; }

  function addFlag(r) {
    const fl = $('fl');
    const empty = fl.querySelector('.empty'); if (empty) empty.remove();
    const div = document.createElement('div');
    div.className = 'fi';
    div.innerHTML = '<b></b><span></span>';
    div.querySelector('b').textContent = r.time;
    div.querySelector('span').textContent = r.text;
    fl.prepend(div);
    while (fl.children.length > 30) fl.lastChild.remove();
  }
  function rebuildFlags() {
    const fl = $('fl'); fl.innerHTML = '';
    const hits = log.filter(r => matchWord(r.text)).slice(-30);
    if (!hits.length) fl.innerHTML = '<div class="empty">Nothing flagged yet.</div>';
    hits.forEach(addFlag);
  }

  // ---------- find buyer ----------
  let findTerm = '';
  function renderFind() {
    const fr = $('fr');
    if (!findTerm) { fr.hidden = true; fr.innerHTML = ''; return; }
    fr.hidden = false; fr.innerHTML = '';
    const hits = log.filter(r => r.text.toLowerCase().includes(findTerm)).slice(-15).reverse();
    if (!hits.length) { fr.innerHTML = '<div class="empty">No messages from that name yet.</div>'; return; }
    hits.forEach(r => {
      const div = document.createElement('div');
      div.className = 'fi'; div.title = 'Click to copy';
      div.innerHTML = '<b></b><span></span>';
      div.querySelector('b').textContent = r.time;
      div.querySelector('span').textContent = r.text;
      div.onclick = () => {
        navigator.clipboard.writeText(r.time + '  ' + r.text).then(() => setStatus('Copied.', 'good'), () => {});
      };
      fr.appendChild(div);
    });
  }
  let findTimer = null;
  function queueFind() { clearTimeout(findTimer); findTimer = setTimeout(renderFind, 300); }

  function render() {
    $('dot').className = 'dot' + (state === 'recording' ? ' on' : state === 'picking' ? ' pick' : '');
    $('start').disabled = state === 'recording' || state === 'picking';
    $('start').textContent = log.length && state !== 'recording' ? 'Continue' : 'Start';
    $('stop').disabled = state !== 'recording';
    $('backup').disabled = !log.length;
    $('clear').disabled = state === 'recording' || !log.length;
    updateCount();
  }

  $('start').onclick = () => { $('resumeRow').hidden = true; startPicking(); };
  $('stop').onclick = () => { stopRecording(); download('show'); render(); };
  $('search').onclick = () => {
    if (dirty || log.length) save();
    setTimeout(() => chrome.runtime.sendMessage({ cmd: 'openSearch' }), 200);
  };
  $('backup').onclick = () => download('backup');
  $('clear').onclick = () => {
    if (!confirm('Clear the panel and start a new show? This show stays saved in Chat Search.')) return;
    log = []; seen = new WeakSet(); startedAt = null; rowSelector = null;
    try { chrome.storage.local.remove(STORE_KEY); } catch (e) {}
    state = 'idle'; rebuildFlags(); setStatus('Cleared.', ''); render();
  };
  $('min').onclick = () => { $('bd').classList.toggle('min'); $('min').textContent = $('bd').classList.contains('min') ? '+' : '–'; };
  $('find').addEventListener('input', e => { findTerm = e.target.value.trim().toLowerCase(); renderFind(); });
  $('words').addEventListener('change', e => setWords(e.target.value));
  $('resume').onclick = () => { $('resumeRow').hidden = true; setStatus('Resumed ' + log.length + ' saved messages.', 'good'); startPicking(); };
  $('fresh').onclick = () => {
    log = []; startedAt = null;
    try { chrome.storage.local.remove(STORE_KEY); } catch (e) {}
    $('resumeRow').hidden = true; rebuildFlags(); setStatus('Ready. Press Start.', ''); render();
  };

  chrome.runtime.onMessage.addListener(msg => {
    if (msg.cmd === 'toggle') host.style.display = host.style.display === 'none' ? '' : 'none';
  });

  // ---------- startup ----------
  document.documentElement.appendChild(host);
  chrome.storage.local.get([STORE_KEY, WORDS_KEY], data => {
    if (Array.isArray(data[WORDS_KEY])) {
      watchWords = data[WORDS_KEY];
      $('words').value = watchWords.join(', ');
    }
    const s = data[STORE_KEY];
    if (s && s.log && s.log.length && Date.now() - s.updatedAt < RESUME_HOURS * 3600000) {
      log = s.log; startedAt = s.startedAt;
      $('resumeRow').hidden = false;
      setStatus('Found ' + log.length + ' saved messages from earlier. Resume or start fresh?', 'info');
      rebuildFlags();
    }
    render();
  });
})();
