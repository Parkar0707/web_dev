/* Shared UI for popup.html and sidebar.html (the two pages differ only in how they are opened). */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // show leading/trailing spaces and non-breaking spaces so "extra space" mismatches are visible
  const show = (s) => esc(String(s).replace(/^ +| +$/g, (m) => '·'.repeat(m.length)).replace(/ /g, '⍽'));

  const DEF = { labels: '', groups: '', lMode: 'normal', dMode: 'normal', lScope: 'labels', cs: false, ww: false, hidden: false, pairOn: false, auto: true };

  const TPL = `
<div class="top">
  <div class="title">Label &amp; Dropdown Matcher</div>
  <button id="openSide" class="ghost pp" title="Open as a side panel that stays open while you switch tabs">⇥ Open sidebar</button>
</div>
<div id="tabInfo" class="tab">Looking for the current tab…</div>
<div id="status" class="status" hidden></div>

<section class="card">
  <div class="hd"><b>1 · Labels</b><span class="cntb" id="cntL">0</span><span class="grow"></span>
    <select id="lScope" title="Where labels are searched"><option value="labels">Label elements</option><option value="page">Entire page text</option></select>
    <div class="seg" data-seg="lMode"><button data-v="normal">Normal</button><button data-v="exact">Exact</button></div>
  </div>
  <textarea id="labels" spellcheck="false" placeholder="Separate labels with a blank line&#10;&#10;First name&#10;&#10;Last name&#10;&#10;Country"></textarea>
  <div class="hint warn" id="hintL" hidden></div>
  <div class="acts"><button id="chkL" class="btn pri">Check labels</button></div>
  <div id="resL" class="res"></div>
</section>

<section class="card">
  <div class="hd"><b>2 · Label dropdown values</b><span class="cntb" id="cntG">0</span><span class="grow"></span>
    <div class="seg" data-seg="dMode"><button data-v="normal">Normal</button><button data-v="exact">Exact</button></div>
  </div>
  <textarea id="groups" spellcheck="false" placeholder="One value per line, groups separated by a blank line&#10;&#10;India&#10;USA&#10;UK&#10;&#10;Yes&#10;No"></textarea>
  <div class="acts"><button id="chkD" class="btn pri">Check dropdown values</button></div>
  <div id="resD" class="res"></div>
</section>

<section class="card">
  <div class="hd"><b>3 · Label ↔ dropdown match</b><span class="pill">optional</span><span class="grow"></span>
    <label class="sw"><input type="checkbox" id="pairOn"> Enable</label></div>
  <div id="pairBody">
    <div id="pairNote" class="hint"></div>
    <div class="acts"><button id="chkP" class="btn pri">Match labels with their dropdowns</button></div>
  </div>
  <div id="resP" class="res"></div>
</section>

<section class="card opts">
  <label><input type="checkbox" data-chk="cs"> Match case <i>(normal)</i></label>
  <label><input type="checkbox" data-chk="ww"> Whole word <i>(normal)</i></label>
  <label><input type="checkbox" data-chk="hidden"> Include hidden elements</label>
  <label class="sb"><input type="checkbox" data-chk="auto"> Re-check automatically when the tab changes</label>
</section>
<div class="foot"><button id="chkAll" class="btn pri">Check all</button><button id="clr" class="btn">Clear results</button></div>
`;

  const LM = (window.LM = { mode: 'popup', windowId: null, state: { ...DEF }, lastTasks: [], steps: {} });

  /* ---------- parsing ---------- */
  function blocks(text) {
    const out = [];
    let cur = [];
    for (const line of String(text).replace(/\r/g, '').split('\n')) {
      if (line.trim() === '') { if (cur.length) out.push(cur); cur = []; } else cur.push(line);
    }
    if (cur.length) out.push(cur);
    return out;
  }
  const parseLabels = (t) => blocks(t).map((b) => b.join(' '));
  const parseGroups = (t) => blocks(t);

  /* ---------- messaging ---------- */
  const bg = (msg) =>
    new Promise((res) =>
      chrome.runtime.sendMessage({ target: 'lm-bg', windowId: LM.windowId, ...msg }, (r) => {
        const err = chrome.runtime.lastError;
        res(r || { error: err ? err.message : 'No response from the extension.' });
      })
    );

  /* ---------- state ---------- */
  let saveT;
  const flush = () => { clearTimeout(saveT); chrome.storage.local.set({ lmState: LM.state }); };
  const save = () => { clearTimeout(saveT); saveT = setTimeout(flush, 150); };

  function paint() {
    $$('.seg').forEach((seg) => $$('button', seg).forEach((b) => b.classList.toggle('on', LM.state[seg.dataset.seg] === b.dataset.v)));
  }
  function applyState() {
    const s = LM.state;
    $('#labels').value = s.labels;
    $('#groups').value = s.groups;
    $('#lScope').value = s.lScope;
    $('#pairOn').checked = !!s.pairOn;
    $$('[data-chk]').forEach((c) => (c.checked = !!s[c.dataset.chk]));
    paint();
    refresh();
  }
  function refresh() {
    const s = LM.state;
    const L = parseLabels(s.labels), G = parseGroups(s.groups);
    $('#cntL').textContent = L.length;
    $('#cntG').textContent = G.length;
    const multi = blocks(s.labels).findIndex((b) => b.length > 1);
    const h = $('#hintL');
    h.hidden = multi < 0;
    h.textContent = multi < 0 ? '' : `Block ${multi + 1} has several lines and is treated as ONE label. Put a blank line between labels.`;
    $('#pairBody').hidden = !s.pairOn;
    const same = L.length > 0 && L.length === G.length;
    $('#chkP').disabled = !same;
    const note = $('#pairNote');
    note.className = 'hint ' + (same ? 'ok' : 'warn');
    note.textContent = same
      ? `${L.length} labels ↔ ${G.length} dropdown groups: label 1 is matched with group 1, label 2 with group 2, …`
      : `Pairing needs the same number of labels and dropdown groups (now ${L.length} labels, ${G.length} groups).`;
    if (!same || !s.pairOn) $('#resP').innerHTML = '';
  }

  /* ---------- rendering ---------- */
  const badge = (n, ex) => (ex ? (n ? '✓' + (n > 1 ? ' ×' + n : '') : '✗') : '×' + n);
  const near = (a) => (a && a.length ? 'Similar on page: ' + a.map((t) => `<code>${show(t)}</code>`).join(' ') : '');
  function row(key, q, n, ex, sub) {
    const ok = n > 0;
    return (
      `<div class="row ${ok ? 'ok' : 'bad'}"><span class="cnt">${badge(n, ex)}</span><span class="txt">${show(q)}</span>` +
      (ok && key ? `<button class="loc" data-key="${key}" title="Highlight on the page (click again for the next match)">⌖</button>` : '') +
      '</div>' + (sub ? `<div class="sub">${sub}</div>` : '')
    );
  }
  function renderLabels(r) {
    const ex = LM.state.lMode === 'exact';
    const f = r.filter((x) => x.n > 0).length;
    return (
      `<div class="sum ${f === r.length ? 'ok' : 'bad'}">${f}/${r.length} labels found · ${ex ? 'exact' : 'normal'} match</div>` +
      r.map((x, i) => row('L' + i, x.q, x.n, ex, x.n ? esc((x.hits || []).join(', ')) : near(x.near))).join('')
    );
  }
  function renderGroups(res) {
    const ex = LM.state.dMode === 'exact';
    return (
      `<div class="sum">${res.units} dropdown${res.units === 1 ? '' : 's'} found on the page · ${ex ? 'exact' : 'normal'} match</div>` +
      `<div class="hint">Counts include both an option's visible text and its value attribute.</div>` +
      res.groups.map((g, gi) => {
        const f = g.items.filter((x) => x.n > 0).length;
        const all = f === g.items.length;
        const tail = g.allIn ? ` · all in ${g.allIn} dropdown${g.allIn > 1 ? 's' : ''}` : all ? ' · but not together in a single dropdown' : '';
        return (
          `<div class="grp"><div class="gh ${all ? 'ok' : 'bad'}">Group ${gi + 1} · ${f}/${g.items.length} found${tail}</div>` +
          g.items.map((x, j) => row(`D${gi}_${j}`, x.q, x.n, ex, near(x.near))).join('') + '</div>'
        );
      }).join('')
    );
  }
  function renderPairs(res) {
    const lx = LM.state.lMode === 'exact', dx = LM.state.dMode === 'exact';
    return res.pairs.map((p, i) => {
      const found = p.items.filter((x) => x.n > 0).length;
      let st;
      if (!p.labelN) st = 'Label not found on the page';
      else if (!p.unit) st = p.ctrl ? 'Label found, but its dropdown options are not on the page (open the dropdown, then re-check)' : 'Label found, but no dropdown is linked to it';
      else st = `${found}/${p.items.length} values found in its dropdown (${p.opts} option${p.opts === 1 ? '' : 's'})`;
      return (
        `<div class="grp"><div class="gh ${p.ok ? 'ok' : 'bad'}">Pair ${i + 1} · ${p.ok ? 'match' : 'mismatch'}</div>` +
        row('P' + i, p.q, p.labelN, lx, esc(st)) +
        p.items.map((x, j) => row(`P${i}_${j}`, x.q, x.n, dx, '')).join('') +
        (p.extra && p.extra.length
          ? `<details class="sub"><summary>${p.extra.length}${p.extra.length >= 30 ? '+' : ''} other option(s) in that dropdown</summary>${p.extra.map((t) => `<code>${show(t)}</code>`).join(' ')}</details>`
          : '') + '</div>'
      );
    }).join('');
  }

  function setStatus(msg, kind) {
    const el = $('#status');
    el.hidden = !msg;
    el.className = 'status ' + (kind || 'info');
    el.textContent = msg || '';
  }
  LM.clearResults = () => { ['#resL', '#resD', '#resP'].forEach((s) => ($(s).innerHTML = '')); LM.steps = {}; };

  /* ---------- running checks ---------- */
  LM.run = async (tasks) => {
    const s = LM.state, labels = parseLabels(s.labels), groups = parseGroups(s.groups);
    const t = [], msgs = [];
    for (const k of tasks) {
      if (k === 'labels') labels.length ? t.push(k) : msgs.push('Enter at least one label.');
      if (k === 'dropdowns') groups.length ? t.push(k) : msgs.push('Enter at least one dropdown group.');
      if (k === 'pairs') labels.length && labels.length === groups.length ? t.push(k) : msgs.push('Pairing needs equal numbers of labels and dropdown groups.');
    }
    if (!t.length) return setStatus(msgs.join(' '), 'warn');
    LM.lastTasks = tasks;
    setStatus('Checking…', 'info');
    const r = await bg({ type: 'scan', opts: { tasks: t, labels, groups, lMode: s.lMode, dMode: s.dMode, lScope: s.lScope, cs: s.cs, ww: s.ww, hidden: s.hidden } });
    if (r.error) { LM.clearResults(); return setStatus(r.error, 'bad'); }
    if (r.tab) showTab(r.tab);
    if (r.labels) $('#resL').innerHTML = renderLabels(r.labels);
    if (r.groups) $('#resD').innerHTML = renderGroups(r);
    if (r.pairs) $('#resP').innerHTML = renderPairs(r);
    setStatus(`Checked at ${new Date().toLocaleTimeString()}${msgs.length ? ' · ' + msgs.join(' ') : ''}`, 'info');
  };

  function showTab(t) {
    let host = '';
    try { host = new URL(t.url).host; } catch (_) {}
    $('#tabInfo').innerHTML = `<b>Current tab:</b> ${esc(t.title)} <span class="host">${esc(host)}</span>`;
  }
  LM.refreshTab = async () => {
    const r = await bg({ type: 'tab' });
    if (r.tab) showTab(r.tab);
    else $('#tabInfo').textContent = r.error || 'No tab found.';
  };
  // called by sidebar.js when the user switches / reloads tabs
  LM.tabChanged = (kind) => {
    clearTimeout(LM._t);
    LM._t = setTimeout(async () => {
      await LM.refreshTab();
      if (kind === 'title') return;
      LM.clearResults();
      if (LM.state.auto && LM.lastTasks.length) LM.run(LM.lastTasks);
      else setStatus('Tab changed. Press a check button to scan this tab.', 'info');
    }, 300);
  };

  /* ---------- wiring ---------- */
  function bind() {
    $$('textarea').forEach((t) => t.addEventListener('input', () => { LM.state[t.id] = t.value; refresh(); save(); }));
    $$('.seg').forEach((seg) => seg.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      LM.state[seg.dataset.seg] = b.dataset.v;
      paint(); save();
    }));
    $$('[data-chk]').forEach((c) => c.addEventListener('change', () => { LM.state[c.dataset.chk] = c.checked; save(); }));
    $('#pairOn').addEventListener('change', (e) => { LM.state.pairOn = e.target.checked; refresh(); save(); });
    $('#lScope').addEventListener('change', (e) => { LM.state.lScope = e.target.value; save(); });
    $('#chkL').onclick = () => LM.run(['labels']);
    $('#chkD').onclick = () => LM.run(['dropdowns']);
    $('#chkP').onclick = () => LM.run(['pairs']);
    $('#chkAll').onclick = () => {
      const same = parseLabels(LM.state.labels).length === parseGroups(LM.state.groups).length;
      LM.run(['labels', 'dropdowns'].concat(LM.state.pairOn && same ? ['pairs'] : []));
    };
    $('#clr').onclick = () => { LM.clearResults(); setStatus(''); bg({ type: 'clear' }); };
    document.addEventListener('click', async (e) => {
      const b = e.target.closest('.loc');
      if (!b) return;
      const key = b.dataset.key;
      LM.steps[key] = (LM.steps[key] === undefined ? -1 : LM.steps[key]) + 1;
      const r = await bg({ type: 'locate', key, step: LM.steps[key] });
      b.textContent = r.ok ? `⌖ ${r.at}/${r.n}` : '⌖ n/a';
      if (!r.ok) b.title = 'Not on the page any more - run the check again';
    });
    window.addEventListener('pagehide', flush);
    // keep popup and sidebar in sync when both have been used
    chrome.storage.onChanged.addListener((ch, area) => {
      if (area !== 'local' || !ch.lmState || !ch.lmState.newValue) return;
      if (JSON.stringify(ch.lmState.newValue) === JSON.stringify(LM.state)) return;
      if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') return;
      LM.state = { ...DEF, ...ch.lmState.newValue };
      applyState();
    });
  }

  LM.init = async ({ mode }) => {
    LM.mode = mode;
    document.body.classList.add(mode);
    $('#app').innerHTML = TPL;
    try { LM.windowId = (await chrome.windows.getCurrent()).id; } catch (_) {}
    const saved = (await chrome.storage.local.get('lmState')).lmState || {};
    LM.state = { ...DEF, ...saved };
    applyState();
    bind();
    await LM.refreshTab();
  };
})();
