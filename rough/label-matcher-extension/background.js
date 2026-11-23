/* Label & Dropdown Matcher - service worker
 * - opens the side panel (shortcut / right-click menu on the toolbar icon)
 * - runs the page scanner in whichever tab is ACTIVE in the caller's window,
 *   so it always works on the tab the user is currently looking at
 */

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create(
    { id: 'lm-open-sidebar', title: 'Open Label Matcher in sidebar', contexts: ['action'] },
    () => void chrome.runtime.lastError
  );
});
chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === 'lm-open-sidebar' && tab) chrome.sidePanel.open({ windowId: tab.windowId });
});
chrome.commands.onCommand.addListener((cmd, tab) => {
  if (cmd === 'open-sidebar' && tab) chrome.sidePanel.open({ windowId: tab.windowId });
});

/* ------------------------------------------------------------------ */
/* Messaging                                                           */
/* ------------------------------------------------------------------ */

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (!msg || msg.target !== 'lm-bg') return;
  handle(msg).then(sendResponse, (e) => sendResponse({ error: String((e && e.message) || e) }));
  return true; // async response
});

const blockedUrl = (u) =>
  !u ||
  /^(chrome|edge|about|chrome-extension|devtools|view-source|chrome-untrusted):/i.test(u) ||
  /^https:\/\/(chrome\.google\.com\/webstore|chromewebstore\.google\.com)/i.test(u);

async function activeTab(windowId) {
  const q = windowId ? { active: true, windowId } : { active: true, lastFocusedWindow: true };
  const [tab] = await chrome.tabs.query(q);
  return tab;
}

async function inject(tab, opts) {
  if (blockedUrl(tab.url)) throw new Error("Chrome doesn't allow extensions to read this page (" + (tab.url || 'no URL') + '). Switch to a normal web page.');
  try {
    const res = await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, func: scanner, args: [opts] });
    return res.map((r) => r.result).filter(Boolean);
  } catch (e) {
    const hint = /^file:/i.test(tab.url || '') ? ' For local files enable "Allow access to file URLs" in chrome://extensions.' : '';
    throw new Error('Could not scan this tab: ' + e.message + hint);
  }
}

async function handle(msg) {
  const tab = await activeTab(msg.windowId);
  if (!tab) throw new Error('No active tab found.');
  const info = { id: tab.id, title: tab.title || tab.url || 'Untitled', url: tab.url || '' };

  if (msg.type === 'tab') return { tab: info };

  if (msg.type === 'scan') {
    const frames = await inject(tab, msg.opts);
    return { tab: info, ...merge(frames, msg.opts) };
  }
  if (msg.type === 'locate') {
    const frames = await inject(tab, { task: 'locate', key: msg.key, step: msg.step });
    const hit = frames.filter((f) => f.ok);
    return { ok: hit.length > 0, n: hit.reduce((a, f) => a + f.n, 0), at: hit[0] ? hit[0].at : 0 };
  }
  if (msg.type === 'clear') {
    await inject(tab, { task: 'clear' });
    return { ok: true };
  }
  throw new Error('Unknown request');
}

/* ------------------------------------------------------------------ */
/* Merge results coming from several frames of the same tab            */
/* ------------------------------------------------------------------ */

function merge(fr, o) {
  const sum = (arr, k) => arr.reduce((a, x) => a + (x[k] || 0), 0);
  const uniq = (a) => [...new Set(a)];
  const out = { frames: fr.length };

  if (fr.some((f) => f.labels)) {
    out.labels = o.labels.map((q, i) => {
      const rs = fr.map((f) => f.labels && f.labels[i]).filter(Boolean);
      return { q, n: sum(rs, 'n'), hits: uniq(rs.flatMap((r) => r.hits || [])), near: uniq(rs.flatMap((r) => r.near || [])).slice(0, 3) };
    });
  }
  if (fr.some((f) => f.groups)) {
    out.units = sum(fr, 'units');
    out.groups = o.groups.map((g, gi) => ({
      items: g.map((q, j) => {
        const rs = fr.map((f) => f.groups && f.groups[gi] && f.groups[gi].items[j]).filter(Boolean);
        return { q, n: sum(rs, 'n'), near: uniq(rs.flatMap((r) => r.near || [])).slice(0, 3) };
      }),
      allIn: fr.reduce((a, f) => a + ((f.groups && f.groups[gi] && f.groups[gi].allIn) || 0), 0),
    }));
  }
  if (fr.some((f) => f.pairs)) {
    const score = (r) => (r.ok ? 1000 : 0) + (r.unit ? 100 : 0) + (r.labelN ? 10 : 0) + r.items.filter((x) => x.n).length;
    out.pairs = o.labels.map((q, i) => {
      const rs = fr.map((f) => f.pairs && f.pairs[i]).filter(Boolean).sort((a, b) => score(b) - score(a));
      return rs[0];
    });
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* Page scanner - serialised and injected into the page. It must stay  */
/* fully self-contained (no references to anything outside itself).    */
/* ------------------------------------------------------------------ */

function scanner(o) {
  const W = window;
  const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|HEAD|META|LINK|TITLE)$/;
  // Collapse HTML whitespace only (a non-breaking space stays a distinct character).
  const clean = (s) => String(s == null ? '' : s).replace(/[ \t\n\r\f]+/g, ' ').replace(/^ | $/g, '');
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  /* ---- highlight / clear requests --------------------------------- */
  const unmark = () => {
    const m = W.__lmMark;
    if (m) { clearTimeout(m.t); m.el.style.outline = m.o; m.el.style.outlineOffset = m.f; W.__lmMark = null; }
  };
  if (o.task === 'clear') { unmark(); return { ok: true }; }
  if (o.task === 'locate') {
    const e = W.__lm && W.__lm[o.key];
    if (!e || !e.els.length) return { ok: false };
    const idx = o.step % e.els.length;
    let el = e.els[idx];
    while (el && el.getClientRects && !el.getClientRects().length && el.parentElement) el = el.parentElement;
    if (!el || !el.style) return { ok: false };
    unmark();
    W.__lmMark = { el, o: el.style.outline, f: el.style.outlineOffset, t: setTimeout(unmark, 5000) };
    el.style.setProperty('outline', '3px solid #f59e0b', 'important');
    el.style.setProperty('outline-offset', '2px', 'important');
    try { el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' }); } catch (_) {}
    return { ok: true, n: e.els.length, at: idx + 1 };
  }

  /* ---- matching ---------------------------------------------------- */
  const rxCache = new Map();
  // exact  : text must equal what the user typed, character for character.
  // normal : VS Code style find - occurrences (non-overlapping), case-insensitive
  //          unless "match case"; optional whole word; whitespace runs collapsed.
  const cnt = (mode, text, q) => {
    if (!text) return 0;
    if (mode === 'exact') return text === q ? 1 : 0;
    const nq = clean(q);
    if (!nq) return 0;
    let rx = rxCache.get(nq);
    if (!rx) {
      let p = esc(nq);
      if (o.ww) p = '(?<![\\p{L}\\p{N}_])' + p + '(?![\\p{L}\\p{N}_])';
      rx = new RegExp(p, 'gu' + (o.cs ? '' : 'i'));
      rxCache.set(nq, rx);
    }
    rx.lastIndex = 0;
    let c = 0;
    while (rx.exec(text)) c++;
    return c;
  };
  const near = (cands, q) => {
    const nq = clean(q).toLowerCase();
    if (!nq) return [];
    const eq = [], part = [];
    for (const c of cands) {
      const t = c.text.toLowerCase();
      if (t === nq) eq.push(c.text);
      else if (t.includes(nq) && t.length <= nq.length * 2 + 12) part.push(c.text);
    }
    return [...new Set(eq.concat(part))].slice(0, 3);
  };

  /* ---- walk the DOM (open shadow roots included) ------------------ */
  W.__lm = W.__lm || {};
  const pre = { labels: 'L', dropdowns: 'D', pairs: 'P' };
  for (const t of o.tasks) for (const k of Object.keys(W.__lm)) if (k[0] === pre[t]) delete W.__lm[k];
  const keep = (key, els) => { W.__lm[key] = { els }; };
  unmark();

  const all = [];
  (function walk(r) {
    r.querySelectorAll('*').forEach((el) => { all.push(el); if (el.shadowRoot) walk(el.shadowRoot); });
  })(document);
  const isVis = (el) => o.hidden || (el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden');
  const txt = (el) => clean(el.innerText != null ? el.innerText : el.textContent);

  /* ---- label candidates ------------------------------------------- */
  const cands = [];
  const addC = (el, text, src) => { if (text) cands.push({ el, text, src }); };
  const seen = new Set();
  const LSEL = 'label,legend,th,dt,caption,[role="label"]';
  const LANC = 'label,legend,th,dt,caption';
  for (const el of all) {
    if (SKIP.test(el.tagName)) continue;
    const al = el.getAttribute('aria-label'), ph = el.getAttribute('placeholder');
    if ((al || ph) && isVis(el)) {
      if (al) addC(el, clean(al), 'aria-label');
      if (ph) addC(el, clean(ph), 'placeholder');
    }
    if (o.lScope === 'page') {
      if (el.tagName === 'OPTION') continue;
      let t = '';
      for (const n of el.childNodes) if (n.nodeType === 3) t += n.nodeValue;
      t = clean(t);
      if (t && isVis(el)) addC(el, t, 'text');
    } else {
      const byTag = el.matches(LSEL);
      const byCls = !byTag && /label/i.test(el.getAttribute('class') || '') && !el.closest(LANC) && !el.querySelector('label,legend,[class*="label" i]');
      if ((byTag || byCls) && isVis(el)) {
        seen.add(el);
        const t = txt(el);
        if (t && t.length <= 300) addC(el, t, byTag ? el.tagName.toLowerCase() : 'class');
      }
    }
  }
  if (o.lScope !== 'page') {
    for (const el of all) {
      const ids = el.getAttribute('aria-labelledby');
      if (!ids) continue;
      const root = el.getRootNode();
      for (const id of ids.split(/\s+/)) {
        const t = id && root.getElementById ? root.getElementById(id) : null;
        if (t && t !== el && !seen.has(t)) {
          seen.add(t);
          const tx = txt(t);
          if (tx && tx.length <= 300 && isVis(t)) addC(t, tx, 'aria-labelledby');
        }
      }
    }
  }

  /* ---- dropdown units: select, datalist, role=listbox / menu ------- */
  const units = [];
  for (const el of all) {
    const tag = el.tagName, role = el.getAttribute('role');
    let opts = null;
    if (tag === 'SELECT') {
      if (!isVis(el)) continue;
      opts = [...el.options].map((p) => ({ el: p, t: clean(p.text), v: p.hasAttribute('value') ? p.getAttribute('value') : '' }));
    } else if (tag === 'DATALIST') {
      opts = [...el.options].map((p) => ({ el: p, t: clean(p.text), v: p.hasAttribute('value') ? p.getAttribute('value') : '' }));
    } else if (role === 'listbox' || role === 'menu') {
      if (!isVis(el)) continue;
      const sel = role === 'listbox' ? '[role="option"]' : '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"]';
      opts = [...el.querySelectorAll(sel)].map((p) => ({ el: p, t: txt(p), v: '' }));
    }
    if (opts) { opts = opts.filter((p) => p.t || p.v); units.push({ el, opts }); }
  }
  // An option can hold the text in two places: its visible text and its value attribute.
  // Both are counted (e.g. <option value="Evaluate">Evaluate</option> = 2 occurrences).
  // Exact mode: the visible text must match exactly, otherwise the option is a mismatch even if its
  // value attribute happens to match (the value then only adds a second occurrence).
  // Options with no visible text (datalist entries) are judged by their value.
  const oc = (p, mode, q) => {
    const tc = cnt(mode, p.t, q), vc = p.v ? cnt(mode, p.v, q) : 0;
    return mode === 'exact' && p.t && !tc ? 0 : tc + vc;
  };

  const out = { units: units.length };

  /* ---- 1. labels --------------------------------------------------- */
  if (o.tasks.includes('labels')) {
    out.labels = o.labels.map((q, i) => {
      let n = 0;
      const els = [], hits = [];
      for (const c of cands) {
        const k = cnt(o.lMode, c.text, q);
        if (k) { n += k; els.push(c.el); hits.push(c.src); }
      }
      keep('L' + i, els);
      const r = { q, n, hits: [...new Set(hits)].slice(0, 6) };
      if (!n && o.lMode === 'exact') r.near = near(cands, q);
      return r;
    });
  }

  /* ---- 2. dropdown value groups ------------------------------------ */
  if (o.tasks.includes('dropdowns')) {
    const flat = units.flatMap((u) => u.opts.map((p) => ({ text: p.t })));
    out.groups = o.groups.map((g, gi) => {
      const perUnit = units.map(() => 0);
      const items = g.map((q, j) => {
        let n = 0;
        const els = [];
        units.forEach((u, ui) => {
          let un = 0;
          for (const p of u.opts) { const k = oc(p, o.dMode, q); if (k) { un += k; els.push(p.el); } }
          if (un) perUnit[ui]++;
          n += un;
        });
        keep('D' + gi + '_' + j, els);
        const r = { q, n };
        if (!n && o.dMode === 'exact') r.near = near(flat, q);
        return r;
      });
      return { items, allIn: perUnit.filter((c) => c === g.length).length };
    });
  }

  /* ---- 3. label <-> dropdown pairing ------------------------------- */
  if (o.tasks.includes('pairs')) {
    const CTL = 'select,input,textarea,[role="combobox"],[role="listbox"],[role="button"][aria-haspopup]';
    const unitOf = new Map(units.map((u) => [u.el, u]));
    const resolve = (ctl, acc) => {
      if (unitOf.has(ctl)) acc.add(unitOf.get(ctl));
      if (ctl.list && unitOf.has(ctl.list)) acc.add(unitOf.get(ctl.list));
      for (const a of ['aria-controls', 'aria-owns']) {
        for (const id of (ctl.getAttribute(a) || '').split(/\s+/)) {
          const root = ctl.getRootNode();
          const t = id && root.getElementById ? root.getElementById(id) : null;
          if (t) units.forEach((u) => { if (u.el === t || t.contains(u.el) || u.el.contains(t)) acc.add(u); });
        }
      }
      ctl.querySelectorAll('select').forEach((s) => { if (unitOf.has(s)) acc.add(unitOf.get(s)); });
    };
    const unitsFor = (c) => {
      const acc = new Set();
      let ctrl = false;
      const tryCtl = (x) => { ctrl = true; resolve(x, acc); };
      const el = c.el;
      if (c.src === 'aria-label' || c.src === 'placeholder') tryCtl(el);
      else {
        if (el.control) tryCtl(el.control);
        el.querySelectorAll(CTL).forEach(tryCtl);
        if (el.id) all.forEach((x) => { if ((x.getAttribute('aria-labelledby') || '').split(/\s+/).includes(el.id)) tryCtl(x); });
        if (!acc.size) {
          let p = el;
          for (let k = 0; k < 4 && p.parentElement; k++) {
            p = p.parentElement;
            const cs = [...p.querySelectorAll(CTL)].filter((x) => !el.contains(x) && el.compareDocumentPosition(x) & 4);
            if (cs.length) { for (const x of cs) { tryCtl(x); if (acc.size) break; } break; }
          }
        }
      }
      return { us: [...acc], ctrl };
    };
    const evalUnit = (u, g) => {
      const els = g.map(() => []);
      const used = new Set();
      let found = 0;
      const items = g.map((q, j) => {
        let n = 0;
        u.opts.forEach((p, k) => { const c = oc(p, o.dMode, q); if (c) { n += c; els[j].push(p.el); used.add(k); } });
        if (n) found++;
        return { q, n };
      });
      return { u, items, els, found, extra: u.opts.filter((_, k) => !used.has(k)).map((p) => p.t || p.v).slice(0, 30) };
    };

    out.pairs = o.labels.map((q, i) => {
      const g = o.groups[i] || [];
      const lm = cands.filter((c) => cnt(o.lMode, c.text, q));
      let best = null, ctrlSeen = false;
      for (const c of lm) {
        const f = unitsFor(c);
        if (f.ctrl) ctrlSeen = true;
        for (const u of f.us) {
          const ev = evalUnit(u, g);
          if (!best || ev.found > best.found) best = { ...ev, lab: c.el };
        }
      }
      keep('P' + i, best ? [best.lab, best.u.el] : lm.map((c) => c.el));
      g.forEach((_, j) => keep('P' + i + '_' + j, best ? best.els[j] : []));
      const items = best ? best.items : g.map((x) => ({ q: x, n: 0 }));
      return {
        q,
        labelN: lm.reduce((a, c) => a + cnt(o.lMode, c.text, q), 0),
        unit: !!best,
        ctrl: ctrlSeen,
        opts: best ? best.u.opts.length : 0,
        items,
        extra: best ? best.extra : [],
        ok: !!(lm.length && best && items.length && items.every((x) => x.n > 0)),
      };
    });
  }
  return out;
}
