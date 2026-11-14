// 月 · 记录 — the whole page. Plain ES module, no build step.
// Tap = check, long-press (right-click on a computer) = details.

const PHASE = { menstrual: '月经期', follicular: '卵泡期', fertile: '排卵期', luteal: '黄体期' };
const PHASE_BG = { follicular: 'var(--fol)', fertile: 'var(--win)', luteal: 'var(--lut)', menstrual: 'rgba(184, 50, 59, 0.10)' };
const KIND_NAMES = { toggle: '勾选', medication: '药物', level: '等级', count: '计数', number: '数值', result: '结果', text: '文字' };
const WEEK = ['一', '二', '三', '四', '五', '六', '日'];
const WEEKDAY = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
const PIN_MARKS = ['✦', '✧'];
const LONG_PRESS_MS = 500;

const $view = document.getElementById('view');
const $sheet = document.getElementById('sheet');
const $toast = document.getElementById('toast');
const wide = window.matchMedia('(min-width: 960px)');

const state = { config: null, month: null, selected: null, onboardShown: false };
const flushers = new Set(); // pending text saves to run before the page is rebuilt
let pending = Promise.resolve();

// ---------------------------------------------------------------- dates

const pad = (n) => String(n).padStart(2, '0');
const fmt = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const parse = (s) => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };
const addDays = (s, n) => { const d = parse(s); d.setDate(d.getDate() + n); return fmt(d); };
const today = () => fmt(new Date());
const nowTime = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };
const md = (s) => { const d = parse(s); return `${d.getMonth() + 1}月${d.getDate()}日`; };
const mdw = (s) => `${md(s)} ${WEEKDAY[parse(s).getDay()]}`;
const isDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(s || '');

function rangeText(a, b) {
  if (a === b) return md(a);
  const [da, db] = [parse(a), parse(b)];
  return da.getMonth() === db.getMonth() ? `${md(a)}–${db.getDate()}日` : `${md(a)}–${md(b)}`;
}

function roman(n) {
  const table = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'],
    [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];
  let out = '';
  for (const [v, r] of table) while (n >= v) { out += r; n -= v; }
  return out;
}

const num = (v) => (v == null ? '' : String(Math.round(v * 100) / 100));

// ---------------------------------------------------------------- DOM + API helpers

function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === 'class') el.className = v;
      else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'value') el.value = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
  }
  for (const c of children.flat(Infinity)) {
    if (c != null && c !== false) el.append(c instanceof Node ? c : String(c));
  }
  return el;
}

const ICONS = {
  prev: '<path d="M15 5l-7 7 7 7"/>',
  next: '<path d="M9 5l7 7-7 7"/>',
  up: '<path d="M6 15l6-6 6 6"/>',
  down: '<path d="M6 9l6 6 6-6"/>',
};

function icon(name, size = 20) {
  const box = document.createElement('span');
  box.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;
  return box.firstChild;
}

const iconBtn = (name, label, onclick) => h('button', { type: 'button', class: 'icon-btn', 'aria-label': label, onclick }, icon(name));

async function api(path, { method = 'GET', body, query } = {}) {
  const url = new URL(path, document.baseURI); // relative, so the app also works under a sub-path
  if (query) for (const [k, v] of Object.entries(query)) if (v != null) url.searchParams.set(k, v);
  const res = await fetch(url, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      detail = typeof j.detail === 'string' ? j.detail : (j.detail?.[0]?.msg || detail);
    } catch { /* not JSON */ }
    throw new Error(detail);
  }
  return res.json();
}

function track(promise) {
  pending = pending.then(() => promise).catch(() => {});
  return promise;
}

const entryBody = (e) => ({ date: e.date, item_id: e.item_id, value: e.value, choice: e.choice, text: e.text, time: e.time });
const addEntry = (body) => api('api/entries', { method: 'POST', body });
const patchEntry = (id, body) => api(`api/entries/${id}`, { method: 'PATCH', body });
const deleteEntry = (id) => api(`api/entries/${id}`, { method: 'DELETE' });
const restoreEntry = (old) => patchEntry(old.id, { value: old.value, choice: old.choice, text: old.text, time: old.time });

// ---------------------------------------------------------------- tap / long-press

function pressable(el, { tap, long }) {
  let timer = null;
  let fired = false;
  let lastLong = 0;
  let sx = 0;
  let sy = 0;
  const clear = () => { clearTimeout(timer); timer = null; };
  const fireLong = (e) => { fired = true; lastLong = Date.now(); navigator.vibrate?.(10); long(e); };
  el.addEventListener('pointerdown', (e) => {
    fired = false;
    if (!long || e.button !== 0) return;
    sx = e.clientX; sy = e.clientY;
    clear();
    timer = setTimeout(() => { timer = null; fireLong(e); }, LONG_PRESS_MS);
  });
  el.addEventListener('pointermove', (e) => { if (timer && Math.hypot(e.clientX - sx, e.clientY - sy) > 10) clear(); });
  for (const t of ['pointerup', 'pointercancel', 'pointerleave']) el.addEventListener(t, clear);
  el.addEventListener('click', (e) => {
    if (fired) { e.preventDefault(); fired = false; return; }
    tap?.(e);
  });
  el.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    if (!long || Date.now() - lastLong < 800) return;
    clear();
    fireLong(e);
  });
  return el;
}

// ---------------------------------------------------------------- toast + actions

let toastTimer;
function toast(message, undo) {
  const [text, button] = $toast.children;
  text.textContent = message;
  button.hidden = !undo;
  button.onclick = async () => {
    $toast.hidden = true;
    try { await undo(); } catch (e) { toast(e.message); }
    await render();
  };
  $toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $toast.hidden = true; }, undo ? 6000 : 3500);
}

async function act(label, run, undo) {
  try {
    const result = await run();
    toast(label, undo ? () => undo(result) : null);
    await render();
  } catch (e) {
    toast(e.message);
  }
}

// ---------------------------------------------------------------- sheet (details)

function openSheet(title, sub, build) {
  const err = h('p', { class: 'error', role: 'alert' });
  const body = h('div', { class: 'sheet-body' },
    h('div', { class: 'grabber', 'aria-hidden': 'true' }),
    h('div', { class: 'block', style: 'gap:4px' }, h('h2', { id: 'sheet-title' }, title), sub ? h('p', { class: 'sub' }, sub) : null));
  const close = () => { if ($sheet.open) $sheet.close(); };
  // save(): run, close, toast with undo, rebuild the page; errors stay in the sheet
  const save = async (label, run, undo) => {
    err.textContent = '';
    try {
      const result = await run();
      close();
      toast(label, undo ? () => undo(result) : null);
      await render();
    } catch (e) {
      err.textContent = e.message;
    }
  };
  $sheet.replaceChildren(body);
  build({ body, err, close, save });
  if (!$sheet.open) $sheet.showModal();
}
$sheet.addEventListener('click', (e) => { if (e.target === $sheet) $sheet.close(); });

function group(label, control, forId, hint) {
  return h('div', { class: 'field-group' },
    forId ? h('label', { for: forId }, label) : h('span', { class: 'lbl' }, label),
    control,
    hint ? h('span', { class: 'hint' }, hint) : null);
}

function sheetButtons({ onDelete, onDone, doneText = '完成', deleteText = '删除这条' }) {
  return h('div', { class: 'sheet-actions' },
    onDelete ? h('button', { type: 'button', class: 'danger', onclick: onDelete }, deleteText) : h('span'),
    h('button', { type: 'button', class: 'primary', onclick: onDone }, doneText));
}

function stepper(input, step, min, max) {
  const move = (d) => {
    const v = Math.min(max, Math.max(min, Math.round((Number(input.value || 0) + d) * 10) / 10));
    input.value = String(v);
    input.dispatchEvent(new Event('input'));
  };
  return h('div', { class: 'stepper' },
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': `减 ${step}`, onclick: () => move(-step) }, '−'),
    input,
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': `加 ${step}`, onclick: () => move(step) }, '+'));
}

// ---------------------------------------------------------------- config lookups

const settings = () => state.config.settings;
const allItems = () => state.config.categories.flatMap((c) => c.items);
const itemById = (id) => allItems().find((i) => i.id === id);
const pinned = () => allItems().filter((i) => i.on_strip && !i.hidden);

function byItem(entries) {
  const out = {};
  for (const e of entries) (out[e.item_id] ||= []).push(e);
  return out;
}

function lhOutcome(item, e) {
  if (e.choice) return e.choice;
  if (item.role === 'lh' && e.value != null) return e.value >= settings().lh_positive_threshold ? '阳性' : '阴性';
  return null;
}

function summarize(entries) {
  const out = [];
  for (const [id, list] of Object.entries(byItem(entries))) {
    const item = itemById(id);
    if (!item || item.kind === 'switch') continue;
    const unit = item.unit || '';
    const sum = list.reduce((a, e) => a + (e.value || 0), 0);
    if (item.kind === 'level') out.push(`${item.name} ${num(list[0].value)}`);
    else if (item.kind === 'count') out.push(`${item.name} ${num(sum)} ${unit}`.trim());
    else if (item.kind === 'number') out.push(`${item.name} ${num(list[0].value)}${unit}`);
    else if (item.kind === 'medication') out.push(`${item.name} ${num(sum)}${unit}`);
    else if (item.kind === 'result') out.push(`${item.name} ${list.map((e) => lhOutcome(item, e) || `${num(e.value)}${unit}`).join('/')}`);
    else if (item.kind === 'text') out.push(item.name);
    else out.push(item.name);
  }
  return out;
}

// ---------------------------------------------------------------- shared day look

function spotColor(level) {
  if (level == null) return null;
  return level <= 1 ? 'var(--s1)' : level <= 2 ? 'var(--s2)' : 'var(--s3)';
}

function dayBackground(d) {
  if (d.spotting != null) return spotColor(d.spotting);
  if (d.period) return 'var(--period)';
  if (d.period_predicted) return 'rgba(184, 50, 59, 0.35)';
  return PHASE_BG[d.phase] || 'var(--fol)';
}

function marksFor(d) {
  const pins = pinned().map((i) => i.id);
  const out = [];
  if (d.lh_positive) out.push(h('span', { style: 'color:var(--ring)' }, '✦'));
  d.pins.forEach((id) => {
    const k = pins.indexOf(id);
    if (k >= 0) out.push(h('span', { style: 'color:var(--rose)' }, PIN_MARKS[k] || '✦'));
  });
  return out;
}

function legend(extra = true) {
  const sw = (style) => h('span', { class: 'sw', style });
  return h('div', { class: 'legend' },
    h('span', {}, sw('background:var(--period)'), '月经'),
    h('span', {}, sw('background:var(--s3)'), 'spotting'),
    h('span', {}, sw('background:var(--win);box-shadow:inset 0 0 0 1px var(--win-line)'), '排卵期'),
    h('span', {}, sw('box-shadow:inset 0 0 0 2px var(--ring)'), '排卵日'),
    h('span', {}, h('span', { style: 'color:var(--ring);font-size:10px' }, '✦'), 'LH 阳性'),
    extra ? pinned().map((i, k) => h('span', {}, h('span', { style: 'color:var(--rose);font-size:10px' }, PIN_MARKS[k]), i.name)) : null);
}

function phaseLine(s) {
  if (!s.phase) return '';
  return `第 ${s.cycle_day} 天 · ${PHASE[s.phase]}${s.estimated ? '（推算）' : ''}`;
}

// ---------------------------------------------------------------- calendar page

async function viewCalendar() {
  const t = today();
  const sel = state.selected || t;
  const month = state.month || sel.slice(0, 7);
  state.month = month;
  const first = parse(`${month}-01`);
  const gridStart = addDays(fmt(first), -((first.getDay() + 6) % 7)); // weeks start on Monday
  const gridEnd = addDays(gridStart, 41);
  const [cal, strip, day] = await Promise.all([
    api('api/calendar', { query: { from: gridStart, to: gridEnd, today: t } }),
    api('api/strip', { query: { date: sel, today: t } }),
    api(`api/days/${sel}`, { query: { today: t } }),
  ]);

  const shiftMonth = (n) => {
    const d = parse(`${month}-01`);
    d.setMonth(d.getMonth() + n);
    state.month = fmt(d).slice(0, 7);
    render();
  };
  const header = h('header', { class: 'top' },
    h('div', { class: 'title' },
      h('h1', {}, `${first.getMonth() + 1} 月`),
      h('span', { class: 'roman' }, `${roman(first.getFullYear())} · ${first.getFullYear()}`)),
    h('div', { class: 'row-actions' },
      iconBtn('prev', '上个月', () => shiftMonth(-1)),
      iconBtn('next', '下个月', () => shiftMonth(1)),
      h('button', { type: 'button', class: 'ghost-btn', onclick: () => { state.selected = t; state.month = t.slice(0, 7); render(); } }, '今天')));

  const left = h('div', {}, header, stripCard(strip, sel, t), monthGrid(cal.days, month, sel, t), selectedCard(day, sel, t));
  if (!wide.matches) return left;
  return h('div', { class: 'split' }, left, h('aside', { class: 'side', 'aria-label': '记录' }, recordPanel(day, sel, false)));
}

function stripCard(strip, sel, t) {
  if (!strip.cycle) {
    return h('section', { class: 'card framed', 'aria-label': '周期' },
      h('span', { class: 't' }, '这天还没有周期信息'),
      h('p', { class: 'sub' }, '在记录页打开「月经期」开关，从那天开始算周期。'));
  }
  const c = strip.cycle;
  const days = strip.days;
  const cols = `grid-template-columns:repeat(${days.length}, minmax(0, 1fr))`;
  const selDay = days.find((d) => d.date === sel) || days[0];
  const todayDay = days.find((d) => d.date === t);
  let title;
  let line;
  if (c.current) {
    title = `本周期 · 第 ${todayDay ? todayDay.cycle_day : '?'} 天`;
    const next = c.next_range ? rangeText(c.next_range[0], c.next_range[1]) : md(c.next_start);
    line = `${md(c.start)}开始 · 预计 ${next} 来月经`;
  } else {
    title = c.predicted ? `预测周期 · ${c.length} 天` : `${md(c.start)}开始 · ${c.length} 天`;
    line = `${md(c.start)} – ${md(c.last_day)}${c.predicted ? ' · 推算' : ''}`;
  }

  const cells = h('div', { class: 'cells', style: cols }, days.map((d) => {
    let style = `background:${dayBackground(d)};`;
    if (d.ovulation) style += 'box-shadow:inset 0 0 0 2px var(--ring);';
    if (d.date === t) style += 'outline:2px solid var(--ink);outline-offset:1px;';
    if (d.future) style += 'opacity:0.5;border:1px dashed var(--muted);';
    return h('div', { style, title: `${md(d.date)} · 第 ${d.cycle_day} 天` });
  }));
  const dots = h('div', { class: 'dots', style: cols }, days.map((d) => h('div', {}, marksFor(d))));

  // day numbers under the strip: 1, 10, 20, 30…, 今天, last
  const labels = new Map();
  const todayCol = todayDay ? todayDay.cycle_day : null;
  for (let n = 1; n <= days.length; n += n === 1 ? 9 : 10) labels.set(n, String(n));
  for (const k of [...labels.keys()]) if (days.length - k < 3) labels.delete(k);
  labels.set(days.length, String(days.length));
  if (todayCol) {
    for (const k of [...labels.keys()]) if (Math.abs(k - todayCol) < 3) labels.delete(k);
    labels.set(todayCol, '今天');
  }
  const labelRow = h('div', { class: 'labels', style: cols }, [...labels].sort((a, b) => a[0] - b[0]).map(([col, text]) =>
    h('span', { style: `grid-row:1;grid-column:${col}${col === days.length && text !== '今天' ? ';justify-self:end' : ''}` }, text)));

  return h('section', { class: 'card framed', 'aria-label': '周期条' },
    h('div', { class: 'card-head' },
      h('div', { class: 'block', style: 'gap:2px' }, h('span', { class: 't' }, title), h('span', { class: 'small' }, line)),
      selDay.phase ? h('span', { class: `pill ${selDay.phase}` }, `${PHASE[selDay.phase]}${selDay.estimated ? ' · 推算' : ''}`) : null),
    h('div', { class: 'strip' }, cells, dots, labelRow),
    legend());
}

function monthGrid(days, month, sel, t) {
  const grid = h('div', { class: 'grid' }, days.map((d) => {
    const other = d.date.slice(0, 7) !== month;
    let style = '';
    const shadows = [];
    let color = '';
    if (d.period) {
      style += `background:${d.period_auto && d.future ? 'rgba(184, 50, 59, 0.55)' : 'var(--period)'};`;
      color = 'color:var(--period-ink);font-weight:500;';
    } else if (d.period_predicted) {
      style += 'background:rgba(184, 50, 59, 0.16);border:1px dashed var(--period);';
      color = 'color:#f3c4c4;';
    } else if (d.phase) {
      style += `background:${PHASE_BG[d.phase]};${d.future ? 'opacity:0.6;' : ''}`;
    }
    if (d.date === t) shadows.push('inset 0 0 0 2px var(--ink)');
    if (d.ovulation) shadows.push(`inset 0 0 0 ${d.date === t ? 4 : 2}px var(--ring)`);
    if (shadows.length) style += `box-shadow:${shadows.join(',')};`;

    const marks = [];
    if (d.spotting != null) marks.push(h('span', { style: `color:${spotColor(d.spotting)}` }, '✦'));
    if (d.lh_positive) marks.push(h('span', { style: 'color:var(--ring)' }, '✦'));
    const aria = [md(d.date)];
    if (d.period) aria.push('月经');
    else if (d.period_predicted) aria.push('预计月经');
    if (d.spotting != null) aria.push('spotting');
    if (d.ovulation) aria.push('排卵日');
    if (d.lh_positive) aria.push('LH 阳性');
    if (d.records) aria.push(`${d.records} 条记录`);

    const cell = h('button', {
      type: 'button',
      class: `cell${other ? ' other' : ''}${d.date === sel ? ' selected' : ''}`,
      style,
      'aria-label': aria.join(' · '),
      'aria-pressed': String(d.date === sel),
    }, h('span', { class: 'n', style: color }, String(parse(d.date).getDate())), h('span', { class: 'm' }, marks));
    cell.addEventListener('click', () => {
      if (d.date === sel && !wide.matches) { location.hash = `#/day/${d.date}`; return; }
      state.selected = d.date;
      if (other) state.month = d.date.slice(0, 7);
      render();
    });
    return cell;
  }));
  return h('section', { class: 'month', 'aria-label': `${Number(month.slice(5))} 月日历` },
    h('div', { class: 'weekdays', 'aria-hidden': 'true' }, WEEK.map((w) => h('span', {}, w))), grid);
}

function selectedCard(day, sel, t) {
  const names = summarize(day.entries);
  const s = day.summary;
  const flags = [];
  if (s.period) flags.push(s.period_auto ? '月经（自动补）' : '月经');
  if (s.spotting != null) flags.push('spotting');
  const text = [...flags, ...names];
  return h('section', { class: 'card gap-top', 'aria-label': '选中的日子' },
    h('div', { class: 'card-head' }, h('span', { class: 't' }, mdw(sel)), h('span', { class: 'small' }, phaseLine(s))),
    h('p', { class: 'empty' }, text.length ? text.join(' · ') : (sel === t ? '今天还没有记录' : '这天没有记录')),
    h('a', { class: 'primary block-btn record-link', href: `#/day/${sel}` }, sel === t ? '记录今天' : '记录这天'));
}

// ---------------------------------------------------------------- record page

async function viewDay(date) {
  state.selected = date;
  state.month = date.slice(0, 7);
  const day = await api(`api/days/${date}`, { query: { today: today() } });
  return recordPanel(day, date, true);
}

function recordPanel(day, date, standalone) {
  const t = today();
  const s = day.summary;
  const entries = byItem(day.entries);
  const go = (d) => {
    if (standalone) location.hash = `#/day/${d}`;
    else { state.selected = d; state.month = d.slice(0, 7); render(); }
  };
  const header = h('header', { class: 'day-top' },
    h('div', { class: 'nav' },
      iconBtn('prev', '前一天', () => go(addDays(date, -1))),
      h('h1', {}, date === t ? `今天 · ${mdw(date)}` : mdw(date)),
      iconBtn('next', '后一天', () => go(addDays(date, 1)))),
    h('p', { class: 'sub center' }, phaseLine(s) || '还没有周期信息'));

  const sections = state.config.categories
    .filter((c) => !c.hidden)
    .map((c) => categorySection(c, date, entries, s))
    .filter(Boolean);
  return h('div', {}, header, h('div', { class: 'stack' }, periodSwitch(date, s, t), sections));
}

function periodSwitch(date, s, t) {
  const on = s.period;
  const put = (value) => api(`api/days/${date}/period`, { method: 'PUT', body: { on: value }, query: { today: t } });
  const sw = h('button', { type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(on), 'aria-label': '月经期' },
    h('span', { class: 'track' }, h('span', { class: 'knob' })));
  sw.addEventListener('click', () => act(on ? '月经期 · 关' : '月经期 · 开', () => put(!on), () => put(on)));
  const sub = s.period_auto
    ? h('span', { class: 'small' }, '自动补上的一天 · ',
      h('button', { type: 'button', class: 'link-btn', style: 'min-height:0;padding:0;font-size:12px', onclick: () => act('已确认', () => put(true)) }, '确认'))
    : h('span', { class: 'small' }, '关着时记的血量算 spotting');
  return h('section', { class: 'switch-card', 'aria-label': '月经开关' },
    h('div', { class: 'block', style: 'gap:2px' }, h('span', { class: 't' }, '月经期'), sub), sw);
}

function categorySection(cat, date, entries, s) {
  const items = cat.items.filter((i) => !i.hidden && i.kind !== 'switch');
  const sec = h('section', { class: 'section', 'aria-label': cat.name }, h('h2', {}, cat.name));
  if (!items.length) {
    if (cat.id !== 'meds') return null;
    sec.append(h('div', { class: 'chips' }, h('a', { class: 'chip dashed', href: '#/settings' }, '＋ 在设置里添加药物')),
      h('span', { class: 'hint' }, '点一下记默认量和当前时间，长按改量和时间'));
    return sec;
  }
  let i = 0;
  while (i < items.length) {
    const kind = items[i].kind;
    if (kind === 'toggle' || kind === 'medication') {
      const run = [];
      while (i < items.length && items[i].kind === kind) run.push(items[i++]);
      sec.append(kind === 'toggle' ? toggleRow(run, date, entries, cat) : medRow(run, date, entries));
    } else if (kind === 'number' || kind === 'count') {
      const run = [];
      while (i < items.length && ['number', 'count'].includes(items[i].kind)) run.push(items[i++]);
      sec.append(h('div', { class: 'measures' }, run.map((it) => (it.kind === 'number' ? numberRow(it, date, entries) : countRow(it, date, entries)))));
    } else {
      const it = items[i++];
      if (kind === 'level') sec.append(levelBlock(it, date, entries, s));
      else if (kind === 'result') sec.append(resultBlock(it, date, entries));
      else sec.append(noteBlock(it, date, entries, cat));
    }
  }
  return sec;
}

// -- toggles

function toggleRow(items, date, entries, cat) {
  const chips = h('div', { class: 'chips' }, items.map((item) => {
    const entry = (entries[item.id] || [])[0];
    const chip = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(!!entry) },
      item.name, entry?.text ? h('small', {}, entry.text) : null);
    return pressable(chip, {
      tap: () => (entry
        ? act(`已取消 · ${item.name}`, () => deleteEntry(entry.id), () => addEntry(entryBody(entry)))
        : act(`已记录 · ${item.name}`, () => addEntry({ date, item_id: item.id }), (made) => deleteEntry(made.id))),
      long: () => toggleSheet(item, date, entry),
    });
  }));
  if (cat.id === 'bleeding') return h('div', { class: 'block' }, h('span', { class: 'name', style: 'font-size:14px' }, '颜色'), chips);
  return chips;
}

function toggleSheet(item, date, entry) {
  openSheet(item.name, `${mdw(date)} · 写下自己的内容`, ({ body, err, save }) => {
    const note = h('textarea', { id: 'sheet-note', class: 'field', rows: 3, maxlength: 4000, placeholder: '比如：去了哪里、多久', value: entry?.text || '' });
    body.append(group('备注', note, 'sheet-note'), err, sheetButtons({
      onDelete: entry ? () => save(`已取消 · ${item.name}`, () => deleteEntry(entry.id), () => addEntry(entryBody(entry))) : null,
      onDone: () => save(`已记录 · ${item.name}`, () => addEntry({ date, item_id: item.id, text: note.value }),
        (made) => (entry ? restoreEntry(entry) : deleteEntry(made.id))),
    }));
  });
}

// -- levels (经量, 血块)

function levelBlock(item, date, entries, s) {
  const entry = (entries[item.id] || [])[0];
  const names = item.options || ['少', '中', '多', '过多'];
  const exact = entry && !Number.isInteger(entry.value) ? h('span', { class: 'small' }, num(entry.value)) : null;
  const tag = item.role === 'flow' ? h('span', { class: `tag${s.period ? ' period' : ''}` }, s.period ? '经量' : 'spotting') : null;
  const chips = h('div', { class: 'chips' }, names.map((name, k) => {
    const level = k + 1;
    const on = !!entry && Math.round(entry.value) === level;
    const chip = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(on) }, `${name} ${level}`);
    return pressable(chip, {
      tap: () => (on
        ? act(`已取消 · ${item.name}`, () => deleteEntry(entry.id), () => addEntry(entryBody(entry)))
        : act(`已记录 · ${item.name} ${name} ${level}`, () => addEntry({ date, item_id: item.id, value: level, time: entry?.time, text: entry?.text }),
          (made) => (entry ? restoreEntry(entry) : deleteEntry(made.id)))),
      long: () => levelSheet(item, date, entry, s),
    });
  }));
  return h('div', { class: 'block' },
    h('div', { class: 'block-head' }, h('span', { class: 'name' }, item.name, tag, exact), h('span', { class: 'hint' }, '长按填小数')),
    chips);
}

function levelSheet(item, date, entry, s) {
  const names = item.options || ['少', '中', '多', '过多'];
  const sub = `${md(date)}${item.role === 'flow' && !s.period ? ' · 月经开关关着，这一条算 spotting' : ''}`;
  openSheet(item.name, sub, ({ body, err, save }) => {
    const input = h('input', { id: 'sheet-level', class: 'big', type: 'number', inputmode: 'decimal', step: '0.1', min: '0.1', max: '9.9', value: entry ? String(entry.value) : '2' });
    const quick = h('div', { class: 'chips' });
    const paint = () => quick.replaceChildren(...names.map((name, k) => h('button', {
      type: 'button', class: 'chip on-bg', 'aria-pressed': String(Number(input.value) === k + 1),
      onclick: () => { input.value = String(k + 1); paint(); },
    }, `${name} ${k + 1}`)));
    input.addEventListener('input', paint);
    paint();
    const time = h('input', { id: 'sheet-time', class: 'field', type: 'time', value: entry?.time || '' });
    const note = h('textarea', { id: 'sheet-note', class: 'field', rows: 2, maxlength: 4000, placeholder: '比如：颜色、什么时候出现的', value: entry?.text || '' });
    body.append(
      group('快速选', quick),
      group('自己填（一位小数）', stepper(input, 0.5, 0.1, 9.9), 'sheet-level'),
      group('时间（可以不填）', time, 'sheet-time'),
      group('备注', note, 'sheet-note'),
      err,
      sheetButtons({
        onDelete: entry ? () => save(`已删除 · ${item.name}`, () => deleteEntry(entry.id), () => addEntry(entryBody(entry))) : null,
        onDone: () => save(`已记录 · ${item.name} ${input.value}`,
          () => addEntry({ date, item_id: item.id, value: Number(input.value), time: time.value || null, text: note.value }),
          (made) => (entry ? restoreEntry(entry) : deleteEntry(made.id))),
      }));
  });
}

// -- results (排卵试纸, 验孕)

function entryList(lines) {
  return lines.length ? h('div', { class: 'entries' }, lines) : null;
}

function resultLine(item, e) {
  const parts = [e.time || '—'];
  if (e.value != null) parts.push(`${num(e.value)} ${item.unit || ''}`.trim());
  const outcome = lhOutcome(item, e);
  if (outcome) parts.push(outcome);
  return parts.join(' · ');
}

function resultBlock(item, date, entries) {
  const list = entries[item.id] || [];
  const options = item.options || [];
  const chips = h('div', { class: 'chips' }, options.map((opt) => {
    const matching = list.filter((e) => lhOutcome(item, e) === opt);
    const on = matching.length > 0;
    const chip = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(on) }, opt);
    return pressable(chip, {
      tap: () => {
        if (!on) return act(`已记录 · ${item.name} ${opt}`, () => addEntry({ date, item_id: item.id, choice: opt, time: nowTime() }), (made) => deleteEntry(made.id));
        const only = matching[0];
        if (matching.length === 1 && only.value == null && !only.text) {
          return act(`已取消 · ${item.name} ${opt}`, () => deleteEntry(only.id), () => addEntry(entryBody(only)));
        }
        return resultSheet(item, date, matching[matching.length - 1], list);
      },
      long: () => resultSheet(item, date, null, list, opt),
    });
  }), options.length ? null : h('button', {
    type: 'button', class: 'chip dashed', onclick: () => resultSheet(item, date, null, list),
  }, '＋ 记一条'));
  const lines = list.map((e) => h('button', { type: 'button', class: 'entry-line', onclick: () => resultSheet(item, date, e, list) },
    h('span', {}, resultLine(item, e)), e.text ? h('span', { class: 'meta' }, e.text) : null));
  return h('div', { class: 'block' },
    h('div', { class: 'block-head' }, h('span', { class: 'name' }, item.name),
      h('span', { class: 'hint' }, item.role === 'lh' ? '长按填 LH 数值' : '长按写备注')),
    chips, entryList(lines));
}

function resultSheet(item, date, entry, list, preset) {
  const options = item.options || [];
  const numeric = item.role === 'lh' || !!item.unit || !options.length;
  const threshold = settings().lh_positive_threshold;
  openSheet(item.name, entry ? `${md(date)} · 改这一条` : `${md(date)} · 新的一条`, ({ body, err, save }) => {
    let mode = numeric && (entry ? entry.value != null : true) ? 'value' : 'choice';
    let choice = entry?.choice || preset || options[0];
    const valueInput = h('input', { id: 'sheet-value', class: 'big', type: 'number', inputmode: 'decimal', step: '0.5', min: '0', value: entry?.value != null ? String(entry.value) : '' });
    const verdict = h('span', { class: 'verdict' });
    const paintVerdict = () => {
      const v = Number(valueInput.value);
      const pos = item.role === 'lh' && valueInput.value !== '' && v >= threshold;
      verdict.textContent = valueInput.value === '' ? '—' : (item.role === 'lh' ? (pos ? '阳性' : '阴性') : num(v));
      verdict.className = `verdict${pos ? ' pos' : ''}`;
    };
    valueInput.addEventListener('input', paintVerdict);
    paintVerdict();
    const valuePane = group(item.role === 'lh' ? 'LH 读数' : '读数', h('div', { class: 'stepper' }, valueInput,
      item.unit ? h('span', { class: 'unit', style: 'white-space:nowrap;color:var(--muted)' }, item.unit) : null), 'sheet-value');
    if (item.role === 'lh') {
      valuePane.append(h('div', { class: 'block-head' }, h('span', { class: 'hint' }, `大于或等于 ${num(threshold)} 算阳性（设置里可以改）`), verdict));
    }
    const choiceChips = h('div', { class: 'chips' });
    const paintChoices = () => choiceChips.replaceChildren(...options.map((opt) => h('button', {
      type: 'button', class: 'chip on-bg', 'aria-pressed': String(opt === choice), onclick: () => { choice = opt; paintChoices(); },
    }, opt)));
    paintChoices();
    const choicePane = group('结果', choiceChips);
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': '记录方式' });
    const paintMode = () => {
      seg.replaceChildren(
        h('button', { type: 'button', 'aria-pressed': String(mode === 'value'), onclick: () => { mode = 'value'; paintMode(); } }, '填数值'),
        h('button', { type: 'button', 'aria-pressed': String(mode === 'choice'), onclick: () => { mode = 'choice'; paintMode(); } }, `只记${options.join(' / ')}`));
      valuePane.hidden = mode !== 'value';
      choicePane.hidden = mode !== 'choice';
    };
    paintMode();
    const time = h('input', { id: 'sheet-time', class: 'field', type: 'time', value: entry ? (entry.time || '') : nowTime() });
    const note = h('textarea', { id: 'sheet-note', class: 'field', rows: 2, maxlength: 4000, placeholder: item.role === 'lh' ? '比如：T 线比 C 线深' : '', value: entry?.text || '' });
    const payload = () => ({
      value: mode === 'value' && valueInput.value !== '' ? Number(valueInput.value) : null,
      choice: mode === 'choice' ? choice : null,
      time: time.value || null,
      text: note.value,
    });
    const others = (list || []).filter((e) => !entry || e.id !== entry.id);
    body.append(
      numeric && options.length ? seg : null, valuePane, choicePane,
      group('测试时间', time, 'sheet-time', entry ? null : '默认是现在'),
      group('备注', note, 'sheet-note'),
      others.length ? group('这天的其他记录', entryList(others.map((e) => h('button', {
        type: 'button', class: 'entry-line', onclick: () => resultSheet(item, date, e, list),
      }, resultLine(item, e))))) : null,
      err,
      sheetButtons({
        onDelete: entry ? () => save(`已删除 · ${item.name}`, () => deleteEntry(entry.id), () => addEntry(entryBody(entry))) : null,
        onDone: () => save(`已记录 · ${item.name}`,
          () => (entry ? patchEntry(entry.id, payload()) : addEntry({ date, item_id: item.id, ...payload() })),
          (made) => (entry ? restoreEntry(entry) : deleteEntry(made.id))),
      }));
  });
}

// -- medication

function medRow(items, date, entries) {
  const chips = h('div', { class: 'chips' }, items.map((item) => {
    const doses = entries[item.id] || [];
    const total = doses.reduce((a, e) => a + (e.value || 0), 0);
    const chip = h('button', { type: 'button', class: 'chip', 'aria-pressed': String(doses.length > 0) },
      item.name, doses.length ? h('small', {}, `${num(total)}${item.unit || ''}${doses.length > 1 ? ` · ${doses.length} 次` : ''}`) : null);
    return pressable(chip, {
      tap: () => {
        if (!doses.length) return act(`已记录 · ${item.name}`, () => addEntry({ date, item_id: item.id, time: nowTime() }), (made) => deleteEntry(made.id));
        if (doses.length === 1) return act(`已取消 · ${item.name}`, () => deleteEntry(doses[0].id), () => addEntry(entryBody(doses[0])));
        return amountSheet(item, date, null, doses);
      },
      long: () => amountSheet(item, date, null, doses),
    });
  }));
  const lines = items.flatMap((item) => (entries[item.id] || []).map((e) => h('button', {
    type: 'button', class: 'entry-line', onclick: () => amountSheet(item, date, e, entries[item.id]),
  }, h('span', {}, `${e.time || '—'} · ${item.name} ${num(e.value)}${item.unit || ''}`), e.text ? h('span', { class: 'meta' }, e.text) : null)));
  return h('div', { class: 'block' }, chips, entryList(lines), h('span', { class: 'hint' }, '点一下记默认量和当前时间，长按改量和时间'));
}

// medication doses and water: amount + time + note, several per day
function amountSheet(item, date, entry, list) {
  const water = item.role === 'water';
  const fallback = item.default_value || (water ? settings().cup_ml : 1);
  const unit = item.unit || '';
  openSheet(item.name, entry ? `${md(date)} · 改这一条` : `${md(date)} · 再记一次`, ({ body, err, save }) => {
    const amount = h('input', { id: 'sheet-amount', class: 'big', type: 'number', inputmode: 'decimal', step: water ? '50' : '0.5', min: '0', value: String(entry ? entry.value : fallback) });
    const quick = water ? h('div', { class: 'chips' }, [250, 330, 500].map((ml) => h('button', {
      type: 'button', class: 'chip on-bg', onclick: () => { amount.value = String(ml); },
    }, `${ml} ml`))) : null;
    const time = h('input', { id: 'sheet-time', class: 'field', type: 'time', value: entry ? (entry.time || '') : (water ? '' : nowTime()) });
    const note = h('textarea', { id: 'sheet-note', class: 'field', rows: 2, maxlength: 4000, value: entry?.text || '' });
    const others = (list || []).filter((e) => !entry || e.id !== entry.id);
    body.append(
      group(`量${unit ? `（${unit}）` : ''}`, h('div', { class: 'block' }, water ? amount : stepper(amount, 0.5, 0.5, 100000), quick), 'sheet-amount'),
      group(water ? '时间（可以不填）' : '时间', time, 'sheet-time'),
      water ? null : group('备注', note, 'sheet-note'),
      others.length ? group('这天的其他记录', entryList(others.map((e) => h('button', {
        type: 'button', class: 'entry-line', onclick: () => amountSheet(item, date, e, list),
      }, h('span', {}, `${e.time || '—'} · ${num(e.value)} ${unit}`), e.text ? h('span', { class: 'meta' }, e.text) : null)))) : null,
      entry ? h('button', { type: 'button', class: 'link-btn', style: 'align-self:flex-start', onclick: () => amountSheet(item, date, null, list) }, '＋ 再记一次') : null,
      err,
      sheetButtons({
        onDelete: entry ? () => save(`已删除 · ${item.name}`, () => deleteEntry(entry.id), () => addEntry(entryBody(entry))) : null,
        onDone: () => {
          const payload = { value: Number(amount.value), time: time.value || null, text: water ? entry?.text : note.value };
          return save(`已记录 · ${item.name} ${num(payload.value)}${unit}`,
            () => (entry ? patchEntry(entry.id, payload) : addEntry({ date, item_id: item.id, ...payload })),
            (made) => (entry ? restoreEntry(entry) : deleteEntry(made.id)));
        },
      }));
  });
}

// -- measurements

function numberRow(item, date, entries) {
  let entry = (entries[item.id] || [])[0];
  const id = `n-${item.id}`;
  const step = item.role === 'temperature' ? '0.01' : item.role === 'weight' ? '0.1' : 'any';
  const input = h('input', {
    id, class: 'field num', type: 'number', inputmode: 'decimal', step,
    placeholder: item.role === 'temperature' ? '36.50' : item.role === 'weight' ? '0.0' : '—',
    value: entry ? num(entry.value) : '',
  });
  input.addEventListener('change', () => track((async () => {
    const raw = input.value.trim();
    if (input.validity.badInput) { toast('数字格式不对，用小数点，比如 36.5'); return; }
    try {
      const old = entry;
      if (!raw) {
        if (!old) return;
        await deleteEntry(old.id);
        entry = null;
        toast(`已清除 · ${item.name}`, () => addEntry(entryBody(old)));
        return;
      }
      entry = await addEntry({ date, item_id: item.id, value: Number(raw), time: old?.time, text: old?.text });
      const made = entry;
      toast(`已记录 · ${item.name} ${raw}${item.unit || ''}`, () => (old ? restoreEntry(old) : deleteEntry(made.id)));
    } catch (e) {
      toast(e.message);
    }
  })()));
  return h('div', { class: 'measure' },
    h('label', { for: id }, item.name),
    h('div', { class: 'row-actions', style: 'gap:6px' }, input, item.unit ? h('span', { class: 'unit' }, item.unit) : null));
}

function countRow(item, date, entries) {
  const list = entries[item.id] || [];
  const water = item.role === 'water';
  const amount = item.default_value || (water ? settings().cup_ml : 1);
  const total = list.reduce((a, e) => a + (e.value || 0), 0);
  const unit = item.unit || '';
  const button = h('button', { type: 'button', class: 'chip outline' }, water ? '+1 杯' : `+${num(amount)}${unit}`);
  pressable(button, {
    tap: () => act(`${item.name} +${num(amount)}${unit}`, () => addEntry({ date, item_id: item.id }), (made) => deleteEntry(made.id)),
    long: () => amountSheet(item, date, null, list),
  });
  const summary = h('button', {
    type: 'button', class: 'link-btn', style: 'min-height:0;padding:0;font-size:12px;color:var(--muted);text-align:left',
    onclick: () => (list.length ? amountSheet(item, date, list[list.length - 1], list) : amountSheet(item, date, null, list)),
  }, water ? `${list.length} 杯 · ${num(total)} ml` : `${list.length} 次 · ${num(total)} ${unit}`);
  return h('div', { class: 'measure' }, h('div', { class: 'block', style: 'gap:2px' }, h('span', {}, item.name), summary), button);
}

// -- free text

function noteBlock(item, date, entries, cat) {
  let entry = (entries[item.id] || [])[0];
  const id = `t-${item.id}`;
  const status = h('span', { class: 'hint', 'aria-live': 'polite' });
  const area = h('textarea', { id, class: 'note', rows: 4, maxlength: 4000, placeholder: '想写什么都可以', value: entry?.text || '' });
  let saved = area.value.trim();
  let timer = null;
  const save = () => {
    clearTimeout(timer);
    const text = area.value.trim();
    if (text === saved) return;
    saved = text;
    track((async () => {
      try {
        if (!text) {
          if (entry) await deleteEntry(entry.id);
          entry = null;
        } else {
          entry = await addEntry({ date, item_id: item.id, text });
        }
        status.textContent = '已保存';
      } catch (e) {
        saved = null;
        status.textContent = e.message;
      }
    })());
  };
  area.addEventListener('input', () => { status.textContent = ''; clearTimeout(timer); timer = setTimeout(save, 800); });
  area.addEventListener('blur', save);
  flushers.add(save);
  return h('div', { class: 'block' },
    h('div', { class: 'block-head' }, item.name !== cat.name ? h('label', { for: id, class: 'name' }, item.name) : h('label', { for: id, class: 'hint' }, '自动保存'), status),
    area);
}

// ---------------------------------------------------------------- cycles page

async function viewCycles() {
  const t = today();
  const [data, config] = await Promise.all([api('api/cycles', { query: { today: t, limit: 12 } }), api('api/config')]);
  state.config = config;
  const st = data.stats;
  const rows = [...data.cycles].reverse(); // oldest on top, this cycle at the bottom
  const pins = pinned();

  const header = h('header', { class: 'page-top' }, h('h1', {}, '周期'),
    h('p', { class: 'sub' }, rows.length ? `${rows.length} 个周期 · 都从月经第一天对齐` : '还没有周期记录'));

  const pinChips = h('div', { class: 'chips' },
    pins.map((item, k) => pressable(h('button', { type: 'button', class: 'chip', 'aria-pressed': 'true' }, `${PIN_MARKS[k]} ${item.name}`), {
      tap: () => act(`不再显示 · ${item.name}`, () => api(`api/items/${item.id}`, { method: 'PATCH', body: { on_strip: false } }),
        () => api(`api/items/${item.id}`, { method: 'PATCH', body: { on_strip: true } })),
    })),
    h('button', { type: 'button', class: 'chip dashed', onclick: pinSheet }, '＋ 选择'));
  const pinSection = h('section', { class: 'section', style: 'margin:0 16px 16px', 'aria-label': '显示的记号' },
    h('div', { class: 'block-head' }, h('span', { class: 'name', style: 'color:var(--muted)' }, '显示记号'), h('span', { class: 'hint' }, `最多 ${state.config.max_strip_items} 个`)),
    pinChips);

  const children = [header, pinSection];
  if (rows.length) {
    const cols = Math.min(60, Math.max(...rows.map((r) => r.length)));
    const tmpl = `grid-template-columns:repeat(${cols}, minmax(0, 1fr))`;
    const months = rows.map((r) => r.start.slice(0, 7));
    const chartRows = rows.map((r, idx) => {
      const unique = months.filter((m) => m === months[idx]).length === 1;
      const label = r.current ? '本周期' : unique ? `${Number(r.start.slice(5, 7))}月` : `${Number(r.start.slice(5, 7))}/${Number(r.start.slice(8))}`;
      const cells = [];
      const dots = [];
      let hasDots = false;
      for (let k = 0; k < cols; k++) {
        const d = r.days[k];
        if (!d) { cells.push(h('div', { style: 'background:transparent' })); dots.push(h('div')); continue; }
        let style = `background:${dayBackground(d)};`;
        if (d.ovulation) style += 'box-shadow:inset 0 0 0 1.5px var(--ring);';
        if (d.date === t) style += 'outline:1.5px solid var(--ink);outline-offset:1px;';
        if (d.future) style += 'opacity:0.45;';
        cells.push(h('div', { style, title: `${md(d.date)} · 第 ${k + 1} 天` }));
        const marks = marksFor(d);
        if (marks.length) hasDots = true;
        dots.push(h('div', {}, marks));
      }
      return h('div', { class: 'row' }, h('span', {}, label), h('div', { class: 'block', style: 'gap:2px' },
        h('div', { class: 'cells', style: tmpl }, cells),
        hasDots ? h('div', { class: 'dots', style: tmpl }, dots) : null));
    });
    const axis = h('div', { class: 'row' }, h('span'), h('div', { class: 'labels', style: tmpl },
      [1, 10, 20, 30, 40, 50].filter((n) => n <= cols).map((n) => h('span', { style: `grid-row:1;grid-column:${n}` }, String(n)))));
    children.push(h('section', { class: 'card', 'aria-label': '周期对比图' }, h('div', { class: 'chart' }, chartRows, axis), legend()));
  }

  const tile = (label, value, unit = '天') => h('div', { class: 'tile' }, h('span', { class: 'small' }, label),
    h('span', { class: 'v' }, value == null ? '—' : num(value), value == null ? null : h('small', {}, ` ${unit}`)));
  children.push(h('section', { class: 'tiles', 'aria-label': '统计' },
    tile('平均周期', st?.avg_cycle), tile('平均经期', st?.avg_period), tile('最短周期', st?.min_cycle), tile('最长周期', st?.max_cycle)));

  const s = settings();
  const basis = st?.from_history
    ? `预测用最近 ${Math.min(st.complete_cycles, s.prediction_cycles)} 个完整周期的平均：周期 ${st.cycle_length} 天，经期 ${st.period_length} 天。`
    : `完整周期还不到 2 个，先用设置里的一般值：周期 ${s.typical_cycle_length} 天，经期 ${s.typical_period_length} 天。`;
  const next = data.next_start ? `下次月经预计 ${data.next_range ? rangeText(...data.next_range) : md(data.next_start)}。` : '';
  children.push(h('p', { class: 'sub', style: 'margin:12px 16px 0' }, basis, next));

  if (data.cycles.length) {
    children.push(h('section', { class: 'section', style: 'margin:16px 16px 0', 'aria-label': '每个周期' },
      h('h2', {}, '每个周期'),
      h('div', { class: 'list' }, data.cycles.map((c) => {
        const todayRow = c.days.find((d) => d.date === t);
        const info = c.current
          ? `进行中 · 第 ${todayRow ? todayRow.cycle_day : '?'} 天 · 经期 ${c.period_days} 天`
          : `${c.length} 天 · 经期 ${c.period_days} 天${c.measured ? ' · LH 定位' : ''}`;
        return h('button', { type: 'button', class: 'list-row', onclick: () => { state.selected = c.start; state.month = c.start.slice(0, 7); location.hash = '#/'; } },
          h('span', {}, md(c.start)), h('span', { class: 'meta' }, info));
      }))));
  }
  return h('div', {}, children);
}

function pinSheet() {
  openSheet('显示记号', `在周期条和对比图上用 ${PIN_MARKS.join(' ')} 标出来，最多 ${state.config.max_strip_items} 个`, ({ body, err }) => {
    const paint = () => {
      const blocks = state.config.categories.filter((c) => !c.hidden).map((c) => {
        const items = c.items.filter((i) => !i.hidden && !['switch', 'text'].includes(i.kind));
        if (!items.length) return null;
        return h('div', { class: 'block' }, h('span', { class: 'lbl hint' }, c.name), h('div', { class: 'chips' }, items.map((i) => h('button', {
          type: 'button', class: 'chip on-bg', 'aria-pressed': String(i.on_strip),
          onclick: async () => {
            err.textContent = '';
            try {
              await api(`api/items/${i.id}`, { method: 'PATCH', body: { on_strip: !i.on_strip } });
              state.config = await api('api/config');
              paint();
              render();
            } catch (e) { err.textContent = e.message; }
          },
        }, i.name))));
      });
      body.querySelector('.pin-list')?.remove();
      body.insertBefore(h('div', { class: 'pin-list block', style: 'gap:14px' }, blocks), err);
    };
    body.append(err, h('div', { class: 'sheet-actions' }, h('span'), h('button', { type: 'button', class: 'primary', onclick: () => $sheet.close() }, '好')));
    paint();
  });
}

// ---------------------------------------------------------------- settings page

const SETTING_FIELDS = [
  ['typical_period_length', '一般经期几天', '还没有 2 个完整周期时用来预测；打开月经第一天时也按预测天数自动补满', 1],
  ['typical_cycle_length', '一般周期几天', '从这次月经第一天到下次月经第一天', 1],
  ['new_cycle_gap_days', '隔几天算新周期', '离上一个月经日超过这么多天，再打开开关才算新周期', 1],
  ['prediction_cycles', '预测用几个周期', '有 2 个以上完整周期后，用最近这么多个的平均', 1],
  ['ovulation_offset_days', '没测 LH 时，排卵日在下次月经前几天', 'Apple 的推算方式：排卵期 6 天，排卵日是最后一天', 1],
  ['lh_positive_threshold', 'LH 阳性线（mIU/mL）', '读数大于或等于它算阳性', 0.5],
  ['cup_ml', '一杯水多少 ml', '点「+1 杯」时记的量', 10],
];

async function viewSettings() {
  state.config = await api('api/config');
  const s = settings();
  const inputs = {};
  const form = h('div', { class: 'form-grid' }, SETTING_FIELDS.map(([key, label, help, step]) => {
    const id = `set-${key}`;
    inputs[key] = h('input', { id, class: 'field num', type: 'number', inputmode: 'decimal', step: String(step), value: num(s[key]) });
    return h('div', { class: 'form-row' }, h('label', { for: id }, label, h('span', { class: 'help' }, help)), inputs[key]);
  }));
  const saveSettings = () => act('设置已保存', async () => {
    const body = {};
    for (const [key] of SETTING_FIELDS) body[key] = Number(inputs[key].value);
    state.config.settings = await api('api/settings', { method: 'PUT', body });
  });

  const children = [
    h('header', { class: 'page-top' }, h('h1', {}, '设置'), h('p', { class: 'sub' }, '数据只存在你自己的服务器上')),
    h('section', { class: 'card', 'aria-label': '周期设置' }, h('h2', { class: 'label-mono' }, '周期'), form,
      h('button', { type: 'button', class: 'primary', onclick: saveSettings }, '保存')),
    h('section', { class: 'card gap-top', 'aria-label': '分类和项目' }, h('h2', { class: 'label-mono' }, '分类和项目'),
      h('p', { class: 'hint', style: 'margin:0' }, '点名字可以改名或删除。隐藏不会丢记录，随时能再显示；删除会连记录一起删掉。'),
      categoryEditor(),
      h('button', { type: 'button', class: 'chip dashed', onclick: () => nameSheet('新分类', '', (name) => api('api/categories', { method: 'POST', body: { name } })) }, '＋ 添加分类')),
    h('section', { class: 'card gap-top', 'aria-label': '数据' }, h('h2', { class: 'label-mono' }, '数据'),
      h('div', { class: 'chips' },
        h('a', { class: 'chip', href: 'api/export.json', download: '' }, '导出 JSON（完整备份）'),
        h('a', { class: 'chip', href: 'api/export.csv', download: '' }, '导出 CSV（表格）')),
      importControl()),
  ];
  return h('div', {}, children);
}

function categoryEditor() {
  const cats = state.config.categories;
  const moveCat = (idx, d) => {
    const ids = cats.map((c) => c.id);
    [ids[idx], ids[idx + d]] = [ids[idx + d], ids[idx]];
    act('已排序', () => api('api/categories/order', { method: 'PUT', body: { ids } }));
  };
  return h('div', { class: 'list' }, cats.map((c, idx) => {
    const moveItem = (k, d) => {
      const ids = c.items.map((i) => i.id);
      [ids[k], ids[k + d]] = [ids[k + d], ids[k]];
      act('已排序', () => api(`api/categories/${c.id}/order`, { method: 'PUT', body: { ids } }));
    };
    const head = h('div', { class: `cat-head${c.hidden ? ' is-hidden' : ''}` },
      h('button', { type: 'button', class: 'grow', onclick: () => nameSheet('分类名', c.name, (name) => api(`api/categories/${c.id}`, { method: 'PATCH', body: { name } }), c) },
        c.name, c.builtin ? h('span', { class: 'kind' }, '内置') : null),
      h('button', { type: 'button', class: 'mini', 'aria-label': `${c.name} 上移`, disabled: idx === 0, onclick: () => moveCat(idx, -1) }, icon('up', 16)),
      h('button', { type: 'button', class: 'mini', 'aria-label': `${c.name} 下移`, disabled: idx === cats.length - 1, onclick: () => moveCat(idx, 1) }, icon('down', 16)),
      c.builtin ? null : h('button', {
        type: 'button', class: 'mini',
        onclick: () => act(c.hidden ? `已显示 · ${c.name}` : `已隐藏 · ${c.name}`, () => api(`api/categories/${c.id}`, { method: 'PATCH', body: { hidden: !c.hidden } }),
          () => api(`api/categories/${c.id}`, { method: 'PATCH', body: { hidden: c.hidden } })),
      }, c.hidden ? '显示' : '隐藏'));
    const items = c.items.map((it, k) => h('div', { class: `item-row${it.hidden ? ' is-hidden' : ''}` },
      h('button', { type: 'button', class: 'grow', onclick: () => itemSheet(c, it) }, it.name, h('span', { class: 'kind' }, KIND_NAMES[it.kind] || '开关')),
      h('button', { type: 'button', class: 'mini', 'aria-label': `${it.name} 上移`, disabled: k === 0, onclick: () => moveItem(k, -1) }, icon('up', 16)),
      h('button', { type: 'button', class: 'mini', 'aria-label': `${it.name} 下移`, disabled: k === c.items.length - 1, onclick: () => moveItem(k, 1) }, icon('down', 16)),
      ['period', 'flow'].includes(it.role) ? null : h('button', {
        type: 'button', class: 'mini',
        onclick: () => act(it.hidden ? `已显示 · ${it.name}` : `已隐藏 · ${it.name}`, () => api(`api/items/${it.id}`, { method: 'PATCH', body: { hidden: !it.hidden } }),
          () => api(`api/items/${it.id}`, { method: 'PATCH', body: { hidden: it.hidden } })),
      }, it.hidden ? '显示' : '隐藏')));
    const add = h('div', { class: 'item-row' }, h('button', { type: 'button', class: 'link-btn', onclick: () => itemSheet(c, null) }, '＋ 添加项目'));
    return h('div', { class: 'cat' }, head, items, add);
  }));
}

function nameSheet(title, current, run, cat) {
  openSheet(title, null, ({ body, err, save }) => {
    const input = h('input', { id: 'sheet-name', class: 'field wide', maxlength: 40, value: current });
    const canDelete = cat && !cat.builtin && !cat.items.length;
    const note = cat && !cat.builtin && cat.items.length
      ? h('span', { class: 'hint' }, '分类里还有项目，把它们删掉或移到别的分类之后，才能删这个分类。') : null;
    body.append(group('名字', input, 'sheet-name'), note, err, sheetButtons({
      deleteText: '删除这个分类',
      onDelete: canDelete ? () => save(`已删除 · ${cat.name}`, () => api(`api/categories/${cat.id}`, { method: 'DELETE' })) : null,
      onDone: () => save('已保存', () => run(input.value)),
    }));
  });
}

// deleting an item: straight away when it has no records, otherwise after a clear warning
function deleteItem(item, save) {
  const run = (withEntries) => api(`api/items/${item.id}`, { method: 'DELETE', query: { with_entries: withEntries ? 'true' : null } });
  if (!item.entries) return save(`已删除 · ${item.name}`, () => run(false));
  openSheet(`删除「${item.name}」？`, null, ({ body, err, save: confirm }) => {
    body.append(
      h('p', { class: 'empty', style: 'color:var(--ink)' }, `这个项目有 ${item.entries} 条记录，会一起删掉，删了找不回来。`),
      h('p', { class: 'hint', style: 'margin:0' }, '只是不想看到它的话，用「隐藏」就好，记录都会留着。要删的话，可以先在设置最下面「导出 JSON」备份一份。'),
      err,
      h('div', { class: 'sheet-actions' },
        h('button', { type: 'button', class: 'link-btn', onclick: () => $sheet.close() }, '取消'),
        h('button', { type: 'button', class: 'danger', style: 'border:1px solid var(--danger);border-radius:8px;padding:0 20px',
          onclick: () => confirm(`已删除 · ${item.name}（${item.entries} 条记录）`, () => run(true)) }, `连同 ${item.entries} 条记录删除`)));
  });
}

function itemSheet(cat, item) {
  openSheet(item ? `改 · ${item.name}` : `${cat.name} · 添加项目`, null, ({ body, err, save }) => {
    const name = h('input', { id: 'sheet-name', class: 'field wide', maxlength: 40, value: item?.name || '' });
    const kind = h('select', { id: 'sheet-kind', class: 'field wide', disabled: !!item },
      Object.entries(KIND_NAMES).map(([k, label]) => h('option', { value: k }, `${label}（${k}）`)));
    kind.value = item?.kind || (cat.id === 'meds' ? 'medication' : 'toggle');
    const unit = h('input', { id: 'sheet-unit', class: 'field wide', maxlength: 16, placeholder: '比如：片、mg、ml', value: item?.unit || '' });
    const amount = h('input', { id: 'sheet-default', class: 'field wide', type: 'number', inputmode: 'decimal', step: 'any', min: '0', value: item?.default_value != null ? num(item.default_value) : '' });
    const options = h('input', { id: 'sheet-options', class: 'field wide', placeholder: '用逗号隔开，比如：少,中,多', value: (item?.options || []).join(',') });
    const unitGroup = group('单位', unit, 'sheet-unit');
    const amountGroup = group('默认量', amount, 'sheet-default', '点一下时记的量；不填就是 1');
    const optionsGroup = group('选项', options, 'sheet-options', '结果类至少要一个，比如：阴性,阳性');
    const paint = () => {
      const k = kind.value;
      unitGroup.hidden = !['count', 'number', 'medication', 'result'].includes(k);
      amountGroup.hidden = !['count', 'medication'].includes(k);
      optionsGroup.hidden = !['level', 'result'].includes(k) || !!item?.role;
    };
    kind.addEventListener('change', paint);
    paint();
    body.append(
      group('名字', name, 'sheet-name'),
      group('类型', kind, 'sheet-kind', item ? '类型建好后不能改' : '勾选=点一下打勾；药物=量+时间，一天可以多次；等级=1–4 级；结果=阴/阳这类'),
      unitGroup, amountGroup, optionsGroup,
      item && !item.deletable ? h('span', { class: 'hint' }, '这个是算周期用的，可以改名、排序，不能删除。') : null,
      item && item.deletable && item.entries ? h('span', { class: 'hint' }, `已经有 ${item.entries} 条记录`) : null,
      err,
      sheetButtons({
        deleteText: '删除这个项目',
        onDelete: item && item.deletable ? () => deleteItem(item, save) : null,
        onDone: () => {
          const payload = { name: name.value };
          if (!unitGroup.hidden) payload.unit = unit.value || null;
          if (!amountGroup.hidden) payload.default_value = amount.value === '' ? null : Number(amount.value);
          if (!optionsGroup.hidden) payload.options = options.value.split(/[,，、]/).map((o) => o.trim()).filter(Boolean);
          if (payload.options && !payload.options.length) delete payload.options;
          return save(item ? '已保存' : `已添加 · ${name.value}`, () => (item
            ? api(`api/items/${item.id}`, { method: 'PATCH', body: payload })
            : api('api/items', { method: 'POST', body: { ...payload, category_id: cat.id, kind: kind.value } })));
        },
      }));
  });
}

function importControl() {
  const input = h('input', { id: 'import-file', type: 'file', accept: 'application/json,.json', class: 'field wide', style: 'padding-top:10px' });
  const restore = h('input', { id: 'import-restore', type: 'checkbox', style: 'width:20px;height:20px;accent-color:var(--rose)' });
  input.addEventListener('change', async () => {
    const file = input.files?.[0];
    if (!file) return;
    try {
      const data = JSON.parse(await file.text());
      const r = await api('api/import', { method: 'POST', body: data, query: { restore: restore.checked ? 'true' : null } });
      toast(`导入了 ${r.entries} 条记录（重复跳过 ${r.skipped} 条）`);
      state.config = null;
      await render();
    } catch (e) {
      toast(`导入失败：${e.message}`);
    }
    input.value = '';
  });
  return h('div', { class: 'block' },
    group('导入（合并进来，重复的跳过）', input, 'import-file'),
    h('label', { for: 'import-restore', class: 'hint', style: 'display:flex;align-items:center;gap:8px;min-height:44px' },
      restore, '从备份还原：连设置、改名、排序、隐藏和记号一起恢复'));
}

// ---------------------------------------------------------------- first run

function onboardSheet() {
  openSheet('先设三个数', '之后都能在设置里改', ({ body, err, save }) => {
    const s = settings();
    const fields = [['typical_period_length', '一般经期几天'], ['typical_cycle_length', '一般周期几天'], ['cup_ml', '一杯水多少 ml']];
    const inputs = {};
    body.append(h('div', { class: 'form-grid' }, fields.map(([key, label]) => {
      inputs[key] = h('input', { id: `ob-${key}`, class: 'field num', type: 'number', inputmode: 'numeric', value: num(s[key]) });
      return h('div', { class: 'form-row' }, h('label', { for: `ob-${key}` }, label), inputs[key]);
    })), err, sheetButtons({
      doneText: '好',
      onDone: () => save('设好了', async () => {
        const payload = { onboarded: 1 };
        for (const [key] of fields) payload[key] = Number(inputs[key].value);
        state.config.settings = await api('api/settings', { method: 'PUT', body: payload });
      }),
    }));
  });
}

// ---------------------------------------------------------------- router

let renderSeq = 0;
let lastPage = null;
async function render() {
  const seq = ++renderSeq;
  for (const flush of flushers) flush();
  await pending;
  flushers.clear();
  const [, name = '', arg] = location.hash.replace(/^#/, '').split('/');
  const page = name === 'day' ? `day|${isDate(arg) ? arg : today()}`
    : ['cycles', 'settings'].includes(name) ? name : `calendar|${state.selected || today()}`;
  let node;
  try {
    if (!state.config) state.config = await api('api/config');
    if (name === 'day') node = await viewDay(isDate(arg) ? arg : today());
    else if (name === 'cycles') node = await viewCycles();
    else if (name === 'settings') node = await viewSettings();
    else node = await viewCalendar();
  } catch (e) {
    node = h('p', { class: 'empty center', style: 'padding:24px 16px' }, `出错了：${e.message}`);
  }
  if (seq !== renderSeq) return;
  const keep = page === lastPage ? { y: window.scrollY, side: $view.querySelector('.side')?.scrollTop || 0 } : null;
  $view.replaceChildren(node);
  if (keep) {
    const side = $view.querySelector('.side');
    if (side) side.scrollTop = keep.side;
    window.scrollTo(0, keep.y);
  }
  lastPage = page;
  const tab = name || 'calendar';
  document.querySelectorAll('.tabs a').forEach((a) => {
    if (a.dataset.tab === tab) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  if (!state.onboardShown && state.config?.settings.onboarded === 0) {
    state.onboardShown = true;
    onboardSheet();
  }
}

window.addEventListener('hashchange', () => { window.scrollTo(0, 0); render(); });
wide.addEventListener('change', () => render());
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') for (const flush of flushers) flush();
  else render(); // the date may have changed while the page was in the background
});
render();
