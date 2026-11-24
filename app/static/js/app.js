/* NEXORA shared UI behaviour: toasts, password toggle, loading buttons, registration form. */
(() => {
  'use strict';

  // ---- Toasts -------------------------------------------------------
  const ICONS = {
    success: 'bi-check-circle-fill', danger: 'bi-x-octagon-fill',
    warning: 'bi-exclamation-triangle-fill', info: 'bi-info-circle-fill',
  };
  const TONES = { success: 'success', danger: 'danger', error: 'danger', warning: 'warning', info: 'primary' };

  function showToast(message, category = 'info') {
    const region = document.getElementById('toastRegion');
    if (!region || !window.bootstrap) return;
    const tone = TONES[category] || 'primary';
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-bg-${tone}`;
    toast.setAttribute('role', category === 'danger' || category === 'error' ? 'alert' : 'status');
    const row = document.createElement('div');
    row.className = 'd-flex';
    const body = document.createElement('div');
    body.className = 'toast-body d-flex align-items-start gap-2';
    const icon = document.createElement('i');
    icon.className = `bi ${ICONS[TONES[category] === 'primary' ? 'info' : TONES[category]] || ICONS.info}`;
    const text = document.createElement('span');
    text.textContent = message;           // textContent: never inject HTML
    body.append(icon, text);
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'btn-close btn-close-white me-2 m-auto';
    close.setAttribute('data-bs-dismiss', 'toast');
    close.setAttribute('aria-label', 'Close');
    row.append(body, close);
    toast.append(row);
    region.append(toast);
    toast.addEventListener('hidden.bs.toast', () => toast.remove());
    new bootstrap.Toast(toast, { delay: 5000 }).show();
  }
  window.NexoraUI = { showToast };

  document.querySelectorAll('[data-flash]').forEach((el) => {
    showToast(el.textContent.trim(), el.dataset.category);
  });

  // ---- Show/hide password -------------------------------------------
  document.querySelectorAll('[data-toggle-password]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const input = document.querySelector(btn.dataset.togglePassword);
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      btn.querySelector('i').className = show ? 'bi bi-eye-slash' : 'bi bi-eye';
    });
  });

  // ---- Loading state on submit ---------------------------------------
  function resetLoading(form) {
    const btn = form.querySelector('button[type="submit"]');
    if (!btn) return;
    btn.disabled = false;
    const spinner = btn.querySelector('.spinner-border');
    if (spinner) spinner.classList.add('d-none');
  }
  document.querySelectorAll('form[data-loading]').forEach((form) => {
    form.addEventListener('submit', () => {
      const btn = form.querySelector('button[type="submit"]');
      if (!btn) return;
      const spinner = btn.querySelector('.spinner-border');
      if (spinner) spinner.classList.remove('d-none');
      // Disable after the browser has read the form, so the submit still goes through.
      setTimeout(() => { btn.disabled = true; }, 0);
    });
  });
  window.addEventListener('pageshow', () => {          // back button / bfcache
    document.querySelectorAll('form[data-loading]').forEach(resetLoading);
  });

  // ---- Progress bars (widths set via CSSOM, which the CSP allows) ------
  document.querySelectorAll('[data-width]').forEach((el) => { el.style.width = `${el.dataset.width}%`; });

  // ---- Show server UTC times in the viewer's local time ---------------
  document.querySelectorAll('time.js-local-time').forEach((el) => {
    const d = new Date(el.getAttribute('datetime'));
    if (!isNaN(d)) el.textContent = d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  });

  // ---- Confirmation for important actions (data-confirm="message") ----
  document.querySelectorAll('[data-confirm]').forEach((el) => {
    el.addEventListener('click', (e) => { if (!window.confirm(el.dataset.confirm)) { e.preventDefault(); e.stopImmediatePropagation(); } });
  });

  // ---- Selects that submit their form on change ---------------------
  document.querySelectorAll('select[data-autosubmit]').forEach((sel) => {
    sel.addEventListener('change', () => sel.form && sel.form.submit());
  });

  // ---- Registration: label follows the chosen role ------------------
  const label = document.getElementById('identifierLabel');
  if (label) {
    document.querySelectorAll('[data-role-radio]').forEach((radio) => {
      radio.addEventListener('change', () => {
        label.textContent = radio.value === 'teacher' ? label.dataset.teacher : label.dataset.student;
      });
    });
  }
})();
