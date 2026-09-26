// SG Events admin console. Content from other pages is only ever set as text or input
// values (never innerHTML), so a hostile event page can't inject script here.
'use strict';

const $ = (sel) => document.querySelector(sel);
const SGT = 'Asia/Singapore';
let categories = [];

async function api(path, options = {}) {
  const resp = await fetch(`/admin/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', 'X-Admin': '1', ...(options.headers || {}) },
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = Array.isArray(body.detail) ? body.detail[0]?.msg : body.detail;
    throw new Error(detail || `Request failed (${resp.status})`);
  }
  return body;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child != null) node.append(child);
  return node;
}

function toast(message) {
  const t = $('#toast');
  t.textContent = message;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 4000);
}

function sgt(iso) {
  return new Intl.DateTimeFormat('en-SG', {
    timeZone: SGT, weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(iso));
}

// --- status -------------------------------------------------------------------------------------

let statusLoaded = false;

async function loadStatus() {
  try {
    const s = await api('/status');
    if (!statusLoaded) {
      statusLoaded = true;
      setupAutosearch(s.keys);
    }
    const k = s.keys;
    const mark = (ok) => (ok ? '✓' : '✗');
    const onemap = k.onemap ? (k.onemap_can_renew ? '✓' : '✓ (token only, no auto-renew)') : '✗';
    $('#status').textContent =
      `${s.upcoming_events} upcoming events · ${s.manual_events} added by hand · ` +
      `OneMap ${onemap} · Voyage ${mark(k.voyage)} · Eventbrite ${mark(k.eventbrite)} · ` +
      `Tavily ${mark(k.tavily)} · Gemini ${mark(k.gemini)} · Anthropic ${mark(k.anthropic)}`;
  } catch (err) {
    $('#status').textContent = `Can't reach the API: ${err.message}`;
  }
}

// --- 1. search launcher -------------------------------------------------------------------------

const QUICK = ['career fair', 'job fair', 'volunteer', 'workshop', 'community event', 'talk', 'kids activities', 'free'];

function searchLinks(q, when) {
  const term = [q, when].filter(Boolean).join(' ');
  const enc = encodeURIComponent;
  const slug = q.toLowerCase().trim().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return [
    ['Google', `https://www.google.com/search?q=${enc(`${term} Singapore event`)}`, 'Everything, incl. organiser sites'],
    ['Google (news)', `https://www.google.com/search?tbm=nws&q=${enc(`${term} Singapore`)}`, 'News mentions: facts and link only'],
    ['Eventbrite', `https://www.eventbrite.sg/d/singapore--singapore/${slug || 'events'}/`, 'Ticketed and free events'],
    ['Peatix', `https://peatix.com/search?q=${enc(q)}&country=SG`, 'Community groups'],
    ['Meetup', `https://www.meetup.com/find/?keywords=${enc(q)}&location=sg--Singapore&source=EVENTS`, 'Meetups'],
    ['Luma Singapore', 'https://luma.com/singapore', 'Tech and startup (already crawled)'],
    ['visitsingapore', 'https://www.visitsingapore.com/whats-happening/all-happenings/', 'Festivals, arts, tourism'],
    ['onePA', 'https://www.onepa.gov.sg/events', 'Community clubs island-wide'],
  ];
}

function showSearch(q) {
  $('#q').value = q;
  const list = $('#search-links');
  list.replaceChildren(
    ...searchLinks(q, $('#when').value).map(([name, href, hint]) =>
      el('li', {}, el('a', { href, target: '_blank', rel: 'noopener noreferrer' }, name, el('small', { text: hint }))),
    ),
  );
}

// --- 2. bookmarklet -----------------------------------------------------------------------------

// Runs on the event page, in your browser: collects schema.org Event JSON-LD and opens it here.
function collectAndOpen() {
  var out = [];
  var seen = new Set();
  function walk(x) {
    if (!x || typeof x !== 'object') return;
    if (Array.isArray(x)) { x.forEach(walk); return; }
    var types = [].concat(x['@type'] || []).join(' ');
    if (/Event\b/.test(types) && !seen.has(x)) { seen.add(x); out.push(x); }
    ['@graph', 'subEvent', 'itemListElement', 'item'].forEach(function (k) { if (x[k]) walk(x[k]); });
  }
  document.querySelectorAll('script[type="application/ld+json"]').forEach(function (s) {
    try { walk(JSON.parse(s.textContent)); } catch (e) {}
  });
  function meta(n) {
    var m = document.querySelector('meta[property="' + n + '"],meta[name="' + n + '"]');
    return m ? m.content : '';
  }
  var data = {
    url: location.href,
    title: meta('og:title') || document.title,
    description: meta('og:description') || meta('description'),
    selection: String(getSelection()).slice(0, 5000),
    jsonld: out.slice(0, 20),
  };
  var s = JSON.stringify(data);
  if (s.length > 150000) {
    data.jsonld = data.jsonld.map(function (e) { var c = Object.assign({}, e); delete c.description; return c; });
    s = JSON.stringify(data);
  }
  window.open(ADMIN_ORIGIN + '/admin#import=' + encodeURIComponent(s), '_blank');
}

function setupBookmarklet() {
  const src = `(${collectAndOpen.toString()})()`.replace('ADMIN_ORIGIN', JSON.stringify(location.origin));
  const link = $('#bookmarklet');
  link.href = `javascript:${encodeURIComponent(src)}`;
  link.addEventListener('click', (e) => {
    e.preventDefault();
    toast('Drag this button to your bookmarks bar, then click it on an event page.');
  });
}

async function importFromHash() {
  const m = location.hash.match(/^#import=(.*)$/s);
  if (!m) return;
  history.replaceState(null, '', location.pathname); // keep page data out of history
  try {
    const page = JSON.parse(decodeURIComponent(m[1]));
    showDrafts(await api('/drafts', { method: 'POST', body: JSON.stringify(page) }));
  } catch (err) {
    toast(`Couldn't read that page: ${err.message}`);
  }
}

// --- 3. drafts ----------------------------------------------------------------------------------

function showDrafts(result) {
  const card = $('#drafts-card');
  card.hidden = false;
  const note = $('#note');
  note.hidden = !result.note;
  note.textContent = result.note || '';
  $('#drafts').replaceChildren(...result.drafts.map(draftForm));
  card.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function draftForm(draft) {
  const form = el('form', { className: 'draft' });
  const inputs = {};
  const allDay = /^\d{4}-\d{2}-\d{2}$/.test(draft.start || '');

  function field(name, label, props = {}, wide = false) {
    const id = `f-${name}-${Math.random().toString(36).slice(2, 8)}`;
    const input = props.multiline
      ? el('textarea', { id, value: draft[name] ?? '' })
      : el('input', { id, type: props.type || 'text', value: draft[name] ?? '', placeholder: props.placeholder || '' });
    if (props.required) input.required = true;
    inputs[name] = input;
    return el('div', { className: `field${wide ? ' wide' : ''}` }, el('label', { htmlFor: id, text: label }), input);
  }
  function check(name, label, checked) {
    const box = el('input', { type: 'checkbox', checked: Boolean(checked) });
    inputs[name] = box;
    return el('label', { className: 'check' }, box, label);
  }

  const dateType = allDay ? 'date' : 'datetime-local';
  const grid = el(
    'div', { className: 'grid' },
    field('title', 'Title', { required: true }, true),
    field('start', 'Starts (Singapore time)', { type: dateType, required: true }),
    field('end', 'Ends', { type: dateType }),
    field('venue', 'Venue', { placeholder: 'e.g. Sengkang Community Club' }),
    field('address', 'Address'),
    field('postal_code', 'Postal code', { placeholder: '6 digits' }),
    field('price', 'Price', { placeholder: 'Free · S$10 – S$25 · From S$8' }),
    field('organizer', 'Organizer'),
    field('registration_url', 'Registration link', { type: 'url' }),
    field('description', 'Description', { multiline: true }, true),
  );
  const allDayBox = check('all_day', 'All-day event', allDay);
  allDayBox.querySelector('input').addEventListener('change', (e) => {
    for (const key of ['start', 'end']) {
      const input = inputs[key];
      const value = input.value;
      input.type = e.target.checked ? 'date' : 'datetime-local';
      input.value = e.target.checked ? value.slice(0, 10) : value && value.length === 10 ? `${value}T09:00` : value;
    }
  });

  const cats = el('div', { className: 'chips', role: 'group', 'aria-label': 'Categories (up to 3)' });
  const chosen = new Set();
  for (const c of categories) {
    const chip = el('button', { type: 'button', className: 'chip', text: c.label, title: c.includes });
    chip.setAttribute('aria-pressed', 'false');
    chip.addEventListener('click', () => {
      if (chosen.has(c.id)) chosen.delete(c.id);
      else if (chosen.size < 3) chosen.add(c.id);
      else return toast('Up to 3 categories.');
      chip.setAttribute('aria-pressed', String(chosen.has(c.id)));
    });
    cats.append(chip);
  }

  const save = el('button', { type: 'submit', text: 'Save event' });
  const discard = el('button', { type: 'button', className: 'secondary', text: 'Discard', onclick: () => form.remove() });
  form.append(
    draft.url ? el('p', { className: 'source' }, 'From ', el('a', { href: draft.url, target: '_blank', rel: 'noopener noreferrer', text: draft.url })) : null,
    grid,
    el('div', { className: 'row' },
      allDayBox,
      check('is_online', 'Online event', draft.is_online),
      check('news', 'From a news article (store facts and link only)', draft.news)),
    el('p', { className: 'hint', text: 'Categories (optional, up to 3). Leave empty to let the classifier decide.' }),
    cats,
    el('div', { className: 'actions' }, save, discard),
  );

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = { ...draft };
    for (const key of ['title', 'start', 'end', 'venue', 'address', 'postal_code', 'price', 'organizer', 'registration_url', 'description']) {
      payload[key] = inputs[key].value.trim() || null;
    }
    payload.title = payload.title || '';
    payload.is_online = inputs.is_online.checked;
    payload.news = inputs.news.checked;
    save.disabled = true;
    save.textContent = 'Saving…';
    try {
      const r = await api('/events', { method: 'POST', body: JSON.stringify({ draft: payload, categories: [...chosen] }) });
      toast(r.merged_into ? `Saved: merged with an existing listing (#${r.id}).` : `Saved as event #${r.id}.`);
      form.remove();
      if (!$('#drafts').children.length) $('#drafts-card').hidden = true;
      loadRecent();
      loadStatus();
    } catch (err) {
      toast(err.message);
      save.disabled = false;
      save.textContent = 'Save event';
    }
  });
  return form;
}

// --- recent -------------------------------------------------------------------------------------

async function loadRecent() {
  try {
    const rows = await api('/events');
    const box = $('#recent');
    if (!rows.length) return box.replaceChildren(el('p', { className: 'hint', text: 'Nothing yet.' }));
    box.replaceChildren(
      el('table', {}, el('tbody', {}, ...rows.map((r) =>
        el('tr', {},
          el('td', { className: 'when', text: sgt(r.starts_at) }),
          el('td', {},
            r.url.startsWith('http') ? el('a', { href: r.url, target: '_blank', rel: 'noopener noreferrer', text: r.title }) : r.title,
            r.status !== 'active' ? ` (${r.status})` : ''),
          el('td', {}, el('button', {
            className: 'danger', type: 'button', text: 'Delete',
            onclick: async (e) => {
              if (!confirm(`Delete “${r.title}”?`)) return;
              e.target.disabled = true;
              try { await api(`/events/${r.id}`, { method: 'DELETE' }); loadRecent(); loadStatus(); }
              catch (err) { toast(err.message); e.target.disabled = false; }
            },
          })),
        ))),
      ),
    );
  } catch (err) {
    toast(err.message);
  }
}

// --- auto-search --------------------------------------------------------------------------------

const DECISION_LABELS = {
  saved: 'Saved', merged: 'Merged', known: 'Known', skipped: 'Skipped', error: 'Error', would_save: 'Would save',
};

function setupAutosearch(keys) {
  const disabled = $('#auto-disabled');
  if (!keys.tavily) {
    disabled.hidden = false;
    disabled.textContent = 'Auto-search needs a Tavily key: sign up free at tavily.com (1,000 searches a month), add TAVILY_API_KEY to .env, then restart the API.';
    $('#auto-run').disabled = true;
  }
  $('#auto-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const num = (id) => Number($(id).value) || null;
    const body = {
      queries: $('#auto-queries').value.split('\n').map((q) => q.trim()).filter(Boolean),
      max_events: num('#auto-events'),
      max_searches: num('#auto-searches'),
      max_pages: num('#auto-pages'),
      dry_run: $('#auto-dry').checked,
    };
    try {
      await api('/autosearch', { method: 'POST', body: JSON.stringify(body) });
      $('#auto-run').disabled = true;
      pollAutosearch();
    } catch (err) {
      toast(err.message);
    }
  });
  pollAutosearch(true);
}

async function pollAutosearch(once = false) {
  let s;
  try {
    s = await api('/autosearch');
  } catch (err) {
    return toast(err.message);
  }
  renderAutosearch(s);
  if (s.running) setTimeout(() => pollAutosearch(), 2000);
  else if (!once) {
    $('#auto-run').disabled = false;
    loadRecent();
    loadStatus();
  }
}

function renderAutosearch(s) {
  const c = s.counts || {};
  const n = (k) => c[k] || 0;
  let line;
  if (s.running) line = `Running… ${n('saved') + n('merged')} saved so far, ${n('skipped')} skipped.`;
  else if (s.error) line = `Stopped with an error: ${s.error}`;
  else if (s.stop_reason) {
    const saved = s.dry_run ? `${n('would_save')} would be saved` : `${n('saved')} saved, ${n('merged')} merged`;
    line = `Done: ${saved}, ${n('known')} already listed, ${n('skipped')} skipped. Stopped because ${s.stop_reason}.`;
  } else if (s.run) line = `Last run: ${s.run.started_at ? sgt(s.run.started_at) : ''} (${s.run.status}).`;
  else line = '';
  $('#auto-status').textContent = line;

  const rows = [...(s.decisions || [])].reverse();
  $('#auto-results').replaceChildren(
    rows.length
      ? el('table', {}, el('tbody', {}, ...rows.map((d) =>
          el('tr', {},
            el('td', {}, el('span', { className: `badge ${d.decision}`, text: DECISION_LABELS[d.decision] || d.decision })),
            el('td', {},
              el('a', { href: d.url, target: '_blank', rel: 'noopener noreferrer', text: d.title || d.url }),
              el('div', { className: 'reason', text: d.reason })),
          ))))
      : el('p', { className: 'hint', text: 'No runs yet.' }),
  );
}

// --- start --------------------------------------------------------------------------------------

async function start() {
  setupBookmarklet();
  $('#quick').replaceChildren(...QUICK.map((q) => el('button', { type: 'button', className: 'chip', text: q, onclick: () => showSearch(q) })));
  $('#search-form').addEventListener('submit', (e) => { e.preventDefault(); showSearch($('#q').value.trim()); });
  $('#when').addEventListener('change', () => $('#q').value && showSearch($('#q').value.trim()));
  $('#url-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const button = e.submitter;
    button.disabled = true;
    try { showDrafts(await api('/extract', { method: 'POST', body: JSON.stringify({ url: $('#url').value.trim() }) })); }
    catch (err) { toast(err.message); }
    finally { button.disabled = false; }
  });
  try { categories = await (await fetch('/categories')).json(); } catch { categories = []; }
  loadStatus();
  loadRecent();
  importFromHash();
  window.addEventListener('hashchange', importFromHash);
}

start();
