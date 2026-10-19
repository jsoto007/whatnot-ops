// Chat Search — reads shows saved by Chat Scraper, or a downloaded .txt file.
const $ = id => document.getElementById(id);
let index = [];          // saved shows
let current = null;      // { id, label, log: [{time,text}] }
let fileShow = null;     // a show loaded from a .txt file

function prettyDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}
function startTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function fillShows(selectId) {
  const sel = $('show');
  sel.innerHTML = '';
  if (fileShow) sel.add(new Option('File: ' + fileShow.label, 'file'));
  index.forEach(s => sel.add(new Option(`${prettyDate(s.date)} show (started ${startTime(s.startedAt)}) · ${s.count} msgs`, s.id)));
  if (!sel.options.length) sel.add(new Option('No saved shows yet', ''));
  if (selectId && [...sel.options].some(o => o.value === selectId)) sel.value = selectId;
}

function loadIndex(selectId) {
  chrome.storage.local.get('cs_index', r => {
    index = r.cs_index || [];
    fillShows(selectId || (current && current.id) || (index[0] && index[0].id));
    openShow($('show').value);
  });
}

function openShow(id) {
  if (id === 'file') { current = fileShow; run(); return; }
  if (!id) { current = null; run(); return; }
  chrome.storage.local.get(id, r => {
    const s = r[id];
    current = s ? { id, label: id, log: s.log, updatedAt: s.updatedAt } : null;
    run();
  });
}

function isLive() {
  return current && current.id !== 'file' && current.updatedAt && Date.now() - current.updatedAt < 60000;
}

function esc(s) { return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
function highlight(text, q) {
  if (!q) return esc(text);
  const i = text.toLowerCase().indexOf(q);
  if (i < 0) return esc(text);
  return esc(text.slice(0, i)) + '<mark>' + esc(text.slice(i, i + q.length)) + '</mark>' + highlight(text.slice(i + q.length), q);
}

function run() {
  $('live').classList.toggle('on', !!isLive());
  const res = $('results'), sum = $('summary');
  res.innerHTML = '';
  if (!current) {
    sum.textContent = '';
    res.innerHTML = '<div class="empty">No shows saved yet. Start Chat Scraper on a Whatnot show, or load a .txt file.</div>';
    return;
  }
  const log = current.log;
  const q = $('q').value.trim().toLowerCase();
  if (!q) {
    sum.innerHTML = `<b>${log.length}</b> messages in this show. Type a buyer name or word to search.`;
    return;
  }
  const ctx = +$('ctx').value;
  const hits = [];
  log.forEach((r, i) => { if (r.text.toLowerCase().includes(q)) hits.push(i); });
  sum.innerHTML = `<b>${hits.length}</b> message${hits.length === 1 ? '' : 's'} matching "${esc(q)}" out of ${log.length}.`;
  if (!hits.length) { res.innerHTML = '<div class="empty">No matches. Try part of the name.</div>'; return; }

  // Merge matches that sit close together so context isn't repeated.
  const blocks = [];
  hits.forEach(i => {
    const a = Math.max(0, i - ctx), b = Math.min(log.length - 1, i + ctx);
    const last = blocks[blocks.length - 1];
    if (last && a <= last.b + 1) { last.b = b; last.hits.push(i); }
    else blocks.push({ a, b, hits: [i] });
  });

  const frag = document.createDocumentFragment();
  blocks.forEach(bl => {
    const div = document.createElement('div');
    div.className = 'hit';
    const n = bl.hits.length;
    div.innerHTML = `<div class="hit-head"><span>${log[bl.hits[0]].time}${n > 1 ? ' · ' + n + ' matches' : ''}</span><button>Copy</button></div>`;
    for (let i = bl.a; i <= bl.b; i++) {
      const m = bl.hits.includes(i);
      const line = document.createElement('div');
      line.className = 'line' + (m ? ' m' : '');
      line.innerHTML = `<span class="t">${esc(log[i].time)}</span><span>${m ? highlight(log[i].text, q) : esc(log[i].text)}</span>`;
      div.appendChild(line);
    }
    div.querySelector('button').onclick = () => {
      const txt = [];
      for (let i = bl.a; i <= bl.b; i++) txt.push(log[i].time + '  ' + log[i].text);
      navigator.clipboard.writeText(txt.join('\n')).then(toast);
    };
    frag.appendChild(div);
  });
  res.appendChild(frag);
}

let toastTimer;
function toast(msg) {
  const t = $('toast'); t.textContent = typeof msg === 'string' ? msg : 'Copied';
  t.classList.add('on'); clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove('on'), 1400);
}

// ---------- .txt files (from Chat Scraper or the old console script) ----------
function parseTxt(body) {
  return body.split(/\r?\n/).filter(l => l.trim() && !l.startsWith('#')).map(l => {
    const tab = l.indexOf('\t');
    return tab > -1 ? { time: l.slice(0, tab), text: l.slice(tab + 1) } : { time: '', text: l };
  });
}
$('load').onclick = () => $('file').click();
$('file').onchange = e => {
  const f = e.target.files[0]; if (!f) return;
  f.text().then(body => {
    fileShow = { id: 'file', label: f.name, log: parseTxt(body) };
    fillShows('file'); openShow('file');
  });
  e.target.value = '';
};

$('dl').onclick = () => {
  if (!current) return;
  const body = current.log.map(r => r.time + '\t' + r.text).join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([body], { type: 'text/plain' }));
  const meta = index.find(s => s.id === current.id);
  a.download = meta ? `Whatnot_${meta.date}_show.txt` : current.label;
  a.click();
};
$('del').onclick = () => {
  if (!current || current.id === 'file') { fileShow = null; current = null; loadIndex(); return; }
  if (!confirm('Delete this saved show from Chat Search? Download it first if you might need it.')) return;
  const id = current.id;
  chrome.storage.local.get('cs_index', r => {
    const idx = (r.cs_index || []).filter(s => s.id !== id);
    chrome.storage.local.set({ cs_index: idx }, () => chrome.storage.local.remove(id, () => { current = null; loadIndex(); toast('Deleted'); }));
  });
};

$('show').onchange = e => openShow(e.target.value);
$('go').onclick = run;
let typing;
$('q').addEventListener('input', () => { clearTimeout(typing); typing = setTimeout(run, 200); });
$('ctx').onchange = run;

// Live updates while the show is still recording.
chrome.storage.onChanged.addListener(changes => {
  if (changes.cs_index) {
    index = changes.cs_index.newValue || [];
    const keep = $('show').value;
    fillShows(keep);
  }
  if (current && changes[current.id] && changes[current.id].newValue) {
    current.log = changes[current.id].newValue.log;
    current.updatedAt = changes[current.id].newValue.updatedAt;
    run();
  }
});
setInterval(() => $('live').classList.toggle('on', !!isLive()), 15000);

loadIndex();
