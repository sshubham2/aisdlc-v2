
(function () {
  // ===== Theme toggle =====
  const THEME_KEY = 'diagnose:theme';
  function applyTheme(t) {
    if (t === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.removeAttribute('data-theme');
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = t === 'dark' ? '☀' : '☾';
  }
  let theme = 'light';
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === 'dark' || stored === 'light') theme = stored;
    else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) theme = 'dark';
  } catch (e) {}
  applyTheme(theme);
  const themeBtn = document.getElementById('theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      theme = theme === 'dark' ? 'light' : 'dark';
      applyTheme(theme);
      try { localStorage.setItem(THEME_KEY, theme); } catch (e) {}
    });
  }

  // ===== Sidebar toggle (mobile) =====
  const sidebarBtn = document.getElementById('sidebar-toggle');
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebar-overlay');
  function closeSidebar() { if (sidebar) sidebar.classList.remove('open'); }
  if (sidebarBtn && sidebar) {
    sidebarBtn.addEventListener('click', () => sidebar.classList.toggle('open'));
  }
  if (overlay) overlay.addEventListener('click', closeSidebar);
  document.querySelectorAll('aside.sidebar a').forEach(a => a.addEventListener('click', () => {
    if (window.innerWidth <= 1100) closeSidebar();
  }));

  // ===== Embedded data =====
  const dataEl = document.getElementById('diagnose-data');
  if (!dataEl) return;
  let raw = dataEl.textContent.trim();
  raw = raw.replace(/<\\\//g, '</');
  let data;
  try { data = JSON.parse(raw); } catch (e) { console.error('Bad embedded JSON', e); return; }
  data.annotations = data.annotations || {};

  const localKey = 'diagnose:' + (data.generated || 'unknown');

  function setStatus(text, cls) {
    const s = document.getElementById('save-status');
    if (s) { s.textContent = text; s.className = cls || ''; }
  }

  function cssEscape(s) {
    return String(s).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function applyAnnotation(id, anno) {
    const el = document.querySelector('[data-finding-id="' + cssEscape(id) + '"]');
    if (!el) return;
    const sel = el.querySelector('select.confirmed');
    const txt = el.querySelector('textarea.notes');
    if (sel) sel.value = anno.confirmed || '';
    if (txt) txt.value = anno.notes || '';
  }

  Object.entries(data.annotations).forEach(([id, anno]) => applyAnnotation(id, anno));

  try {
    const stored = localStorage.getItem(localKey);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (parsed && parsed.annotations && Object.keys(parsed.annotations).length) {
        Object.entries(parsed.annotations).forEach(([id, anno]) => applyAnnotation(id, anno));
        setStatus('Restored unsaved draft from this browser. Click Save annotated HTML to commit.', 'dirty');
      }
    }
  } catch (e) {}

  function collect() {
    const out = {};
    document.querySelectorAll('[data-finding-id]').forEach(el => {
      const id = el.dataset.findingId;
      const sel = el.querySelector('select.confirmed');
      const txt = el.querySelector('textarea.notes');
      const conf = sel ? sel.value : '';
      const notes = txt ? txt.value : '';
      if (conf || notes) out[id] = { confirmed: conf, notes: notes };
    });
    return out;
  }

  // ===== Live progress counter =====
  function updateProgress() {
    const totals = { all: 0, critical: 0, high: 0, medium: 0, low: 0 };
    const reviewed = { all: 0, critical: 0, high: 0, medium: 0, low: 0 };
    document.querySelectorAll('[data-finding-id]').forEach(el => {
      const sev = (el.dataset.severity || 'low').toLowerCase();
      if (totals[sev] === undefined) return;
      totals[sev]++; totals.all++;
      const sel = el.querySelector('select.confirmed');
      const isReviewed = sel && sel.value && sel.value !== '';
      if (isReviewed) {
        reviewed[sev]++; reviewed.all++;
        el.classList.add('is-reviewed');
      } else {
        el.classList.remove('is-reviewed');
      }
    });
    const overallNum = document.getElementById('progress-overall-num');
    const overallTotal = document.getElementById('progress-overall-total');
    const overallFill = document.getElementById('progress-overall-fill');
    if (overallNum) overallNum.textContent = reviewed.all;
    if (overallTotal) overallTotal.textContent = totals.all;
    if (overallFill) {
      const pct = totals.all > 0 ? (reviewed.all / totals.all) * 100 : 0;
      overallFill.style.width = pct.toFixed(1) + '%';
    }
    ['critical', 'high', 'medium', 'low'].forEach(sev => {
      const fill = document.getElementById('sev-fill-' + sev);
      const count = document.getElementById('sev-count-' + sev);
      if (count) count.textContent = reviewed[sev] + ' / ' + totals[sev];
      if (fill) {
        const pct = totals[sev] > 0 ? (reviewed[sev] / totals[sev]) * 100 : 0;
        fill.style.width = pct.toFixed(1) + '%';
      }
    });
  }
  updateProgress();

  let dirty = false;
  let saveTimer = null;
  function markDirty() {
    dirty = true;
    setStatus('Unsaved changes', 'dirty');
    updateProgress();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      try {
        localStorage.setItem(localKey, JSON.stringify({ annotations: collect() }));
      } catch (e) {}
    }, 500);
  }

  document.querySelectorAll('select.confirmed, textarea.notes').forEach(el => {
    el.addEventListener('change', markDirty);
    el.addEventListener('input', markDirty);
  });

  const saveBtn = document.getElementById('save-btn');
  if (saveBtn) {
    saveBtn.addEventListener('click', () => {
      data.annotations = collect();
      const json = JSON.stringify(data, null, 2).replace(/<\//g, '<\\/');
      dataEl.textContent = json;

      const html = '<!DOCTYPE html>\n' + document.documentElement.outerHTML;
      const blob = new Blob([html], { type: 'text/html' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'diagnosis.html';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      try { localStorage.removeItem(localKey); } catch (e) {}
      dirty = false;
      setStatus('Saved (downloaded). Replace the original with the downloaded copy and send back.', 'saved');
    });
  }

  window.addEventListener('beforeunload', e => {
    if (dirty) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
})();
