/* SGPA / CGPA calculator. Every number shown is computed by the server from the
   institution's configured grading scheme; this script only collects choices
   and displays the response. */
(() => {
  'use strict';
  const app = document.getElementById('calcApp');
  if (!app) return;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  let data = null;

  async function api(method, path, body) {
    const headers = { Accept: 'application/json', 'X-CSRFToken': csrf };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    try {
      const res = await fetch(path, { method, headers, credentials: 'same-origin', body: body === undefined ? undefined : JSON.stringify(body) });
      let json = null;
      try { json = await res.json(); } catch (_) { /* not JSON */ }
      if (res.ok && json && json.ok) return { ok: true, data: json.data };
      return { ok: false, error: (json && json.error) || { message: 'Unexpected server response.' } };
    } catch (_) { return { ok: false, error: { message: 'Network error. Check your connection.' } }; }
  }

  function gradeSelect(valueKey, blankText) {
    const s = el('select', 'form-select form-select-sm');
    s.append(new Option(blankText, ''));
    data.scheme.grades.forEach((g) => s.append(new Option(`${g.label} (${g.grade_point})`, valueKey === 'label' ? g.label : g.grade_point)));
    return s;
  }

  // ---- semester mode ----------------------------------------------------
  function renderSubjects() {
    const n = Number($('semSelect').value);
    const box = $('subjectRows'); box.replaceChildren();
    const subs = data.subjects.filter((s) => s.semester_number === n);
    if (!subs.length) { box.append(el('p', 'text-muted small mb-0', 'No subjects are defined for this semester in your department.')); return; }
    subs.forEach((s) => {
      const row = el('div', 'nx-calc-row'); row.dataset.subject = s.id;
      row.append(el('span', 'nx-calc-subject', `${s.code} · ${s.name}`), el('span', 'text-muted small', `${s.credits} cr`), gradeSelect('label', 'Not taken'));
      box.append(row);
    });
  }
  function collectGrades() {
    const grades = {};
    $('subjectRows').querySelectorAll('.nx-calc-row').forEach((row) => {
      const v = row.querySelector('select').value; if (v) grades[row.dataset.subject] = v;
    });
    return { semester_number: Number($('semSelect').value), grades };
  }

  // ---- manual mode ------------------------------------------------------
  function addManualRow() {
    const row = el('div', 'nx-calc-row manual');
    const name = el('input', 'form-control form-control-sm'); name.placeholder = 'Subject'; name.maxLength = 40; name.setAttribute('aria-label', 'Subject name');
    const credits = el('input', 'form-control form-control-sm'); credits.type = 'number'; credits.min = '0.5'; credits.max = '30'; credits.step = '0.5'; credits.placeholder = 'Credits'; credits.setAttribute('aria-label', 'Credits');
    const grade = gradeSelect('point', 'Grade');
    const remove = el('button', 'btn btn-sm btn-outline-danger'); remove.type = 'button'; remove.setAttribute('aria-label', 'Remove subject'); remove.append(el('i', 'bi bi-x-lg'));
    remove.addEventListener('click', () => row.remove());
    row.append(name, credits, grade, remove); $('manualRows').append(row);
  }
  function collectManual() {
    return [...$('manualRows').querySelectorAll('.nx-calc-row')].map((row) => {
      const [name, credits] = row.querySelectorAll('input'); const grade = row.querySelector('select');
      return { name: name.value.trim(), credits: credits.value, grade_point: grade.value };
    });
  }

  // ---- result display ---------------------------------------------------
  function showMessage(kind, text) {
    const box = $('calcResult'); box.replaceChildren();
    box.append(el('div', `alert alert-${kind} mb-0`, text));
  }
  function showError(err) {
    const box = $('calcResult'); box.replaceChildren();
    const a = el('div', 'alert alert-danger mb-0'); a.setAttribute('role', 'alert');
    a.append(el('div', 'fw-semibold', err.message || 'Could not calculate.'));
    if (err.fields) { const ul = el('ul', 'mb-0 mt-1 small'); Object.entries(err.fields).forEach(([k, v]) => ul.append(el('li', '', `${k}: ${v}`))); a.append(ul); }
    box.append(a);
  }
  function showResult(r) {
    const box = $('calcResult'); box.replaceChildren();
    const kind = r.formula.startsWith('CGPA') ? 'CGPA' : 'SGPA';
    const head = el('div', 'd-flex justify-content-between align-items-start flex-wrap gap-2 mb-2');
    head.append(el('div', 'nx-calc-total', `${kind} ${r.gpa}`), el('span', `badge ${r.is_estimate ? 'text-bg-warning' : 'text-bg-success'}`, r.label));
    box.append(head);
    const table = el('table', 'table table-sm align-middle');
    const thead = el('thead'); const hr = el('tr');
    ['Subject', 'Credits', 'Grade', 'Grade points', 'Credit × points'].forEach((h) => { const th = el('th', '', h); th.scope = 'col'; hr.append(th); });
    thead.append(hr); table.append(thead);
    const tbody = el('tbody');
    r.rows.forEach((row) => {
      const tr = el('tr');
      [row.name ? `${row.code} · ${row.name}` : row.code, row.credits, row.grade || '—', row.grade_point, row.weighted].forEach((v) => tr.append(el('td', '', String(v))));
      tbody.append(tr);
    });
    table.append(tbody); const wrap = el('div', 'table-responsive'); wrap.append(table); box.append(wrap);
    box.append(el('div', 'small', `Total applicable credits: ${r.total_credits}`));
    box.append(el('div', 'small', `Weighted grade-point total: ${r.total_weighted}`));
    box.append(el('div', 'small text-muted mt-1', `${r.formula}. Scheme: ${r.scheme.name}. Rounded to ${r.decimals} decimal places.`));
    if (r.not_included && r.not_included.length) box.append(el('div', 'small mt-2', 'Not included: ' + r.not_included.map((n) => `${n.code} (${n.reason})`).join(', ')));
    if (r.superseded && r.superseded.length) box.append(el('div', 'small mt-2', `Earlier attempts not counted (${r.policy} attempt policy): ` + r.superseded.map((s) => s.code).join(', ')));
    if (r.semesters && r.semesters.length) box.append(el('div', 'small mt-2', 'Official semester SGPAs (shown for information, not averaged): ' + r.semesters.map((s) => `${s.semester_name} ${s.gpa}`).join(', ')));
  }

  async function run(button, path, body) {
    const buttons = app.querySelectorAll('button'); buttons.forEach((b) => { b.disabled = true; });
    showMessage('secondary', 'Calculating…');
    const res = await api('POST', path, body);
    buttons.forEach((b) => { b.disabled = false; });
    if (res.ok) showResult(res.data); else showError(res.error);
  }

  $('calcSgpa').addEventListener('click', (e) => run(e.currentTarget, '/api/student/sgpa/calculate', collectGrades()));
  $('calcCgpaHypo').addEventListener('click', (e) => run(e.currentTarget, '/api/student/cgpa/calculate', { hypothetical: collectGrades() }));
  $('calcCgpa').addEventListener('click', (e) => run(e.currentTarget, '/api/student/cgpa/calculate', {}));
  $('manualSgpa').addEventListener('click', (e) => run(e.currentTarget, '/api/student/sgpa/calculate', { items: collectManual() }));
  $('manualCgpa').addEventListener('click', (e) => run(e.currentTarget, '/api/student/cgpa/calculate', { items: collectManual() }));
  $('addRow').addEventListener('click', addManualRow);
  $('semSelect').addEventListener('change', renderSubjects);

  (async () => {
    const res = await api('GET', '/api/student/calculator-data');
    if (!res.ok || !res.data.scheme) { showMessage('warning', 'The grading scheme could not be loaded.'); return; }
    data = res.data;
    renderSubjects(); addManualRow();
  })();
})();
