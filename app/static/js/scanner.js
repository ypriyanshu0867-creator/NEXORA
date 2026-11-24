/* Student QR scanner. The browser only forwards what it scanned and measured;
   the server decides whether attendance is recorded. Success is shown only
   after the server answers 201. */
(() => {
  'use strict';
  const app = document.getElementById('scanApp');
  if (!app) return;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);
  const PREFIX = 'NXR1.';
  let scanner = null, scanning = false, busy = false;

  const MESSAGES = {
    qr_invalid: 'That QR code is not a NEXORA attendance code.',
    qr_expired: 'That code has expired. Scan the code that is on the teacher\'s screen now.',
    session_closed: 'This attendance session has ended.',
    not_enrolled: 'You are not enrolled in this subject.',
    already_marked: 'Your attendance is already recorded for this session.',
    outside_radius: null, location_inaccurate: null, location_denied: null,
    location_missing: null, location_invalid: null, device_cooldown: null,
    device_unregistered: null, device_reused: null, rate_limited: null,
  };

  function show(kind, title, text) {
    const box = $('result'); box.replaceChildren();
    const alert = document.createElement('div');
    alert.className = `alert alert-${kind} mb-0`; alert.setAttribute('role', kind === 'danger' ? 'alert' : 'status');
    const h = document.createElement('div'); h.className = 'fw-semibold'; h.textContent = title;
    const p = document.createElement('div'); p.textContent = text;
    alert.append(h, p); box.append(alert);
  }

  function setBusy(on) {
    busy = on;
    $('startBtn').disabled = on;
    const submit = $('manualForm').querySelector('button'); submit.disabled = on;
  }

  const cameraSupported = () => !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.Html5Qrcode);

  async function stopScan() {
    if (scanner && scanning) {
      try { await scanner.stop(); } catch (_) { /* already stopped */ }
      try { scanner.clear(); } catch (_) { /* nothing to clear */ }
    }
    scanning = false;
    $('stopBtn').classList.add('d-none'); $('startBtn').classList.remove('d-none');
  }

  async function startScan() {
    if (busy || scanning) return;
    if (!cameraSupported()) { $('unsupported').classList.remove('d-none'); return; }
    scanner = scanner || new Html5Qrcode('reader');
    try {
      await scanner.start({ facingMode: 'environment' }, { fps: 10, qrbox: { width: 240, height: 240 } },
        onScanned, () => { /* per-frame "no code" notices are ignored */ });
      scanning = true;
      $('startBtn').classList.add('d-none'); $('stopBtn').classList.remove('d-none');
      show('info', 'Camera on', 'Point the camera at the QR code.');
    } catch (err) {
      const denied = err && /permission|denied|notallowed/i.test(String(err));
      show('danger', denied ? 'Camera permission needed' : 'Could not start the camera',
        denied ? 'Allow camera access for this site (lock icon in the address bar), then press Start scanning again.'
               : 'Check that no other app is using the camera, and that you opened this page over HTTPS.');
    }
  }

  function getLocation() {
    return new Promise((resolve) => {
      if (!navigator.geolocation) { resolve({ location_error: 'unavailable' }); return; }
      navigator.geolocation.getCurrentPosition(
        (p) => resolve({ latitude: p.coords.latitude, longitude: p.coords.longitude, accuracy: p.coords.accuracy }),
        (e) => resolve({ location_error: e.code === 1 ? 'denied' : (e.code === 3 ? 'timeout' : 'unavailable') }),
        { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
    });
  }

  async function submit(text) {
    if (busy) return;                                   // never two requests at once
    if (!text.startsWith(PREFIX)) { show('warning', 'Not an attendance code', MESSAGES.qr_invalid); return; }
    setBusy(true);
    await stopScan();
    show('info', 'Checking…', 'Getting your location and contacting the server.');
    const location = await getLocation();
    try {
      const res = await fetch('/api/attendance/mark', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({ qr: text, ...location }),
      });
      let json = null;
      try { json = await res.json(); } catch (_) { /* not JSON */ }
      if (res.status === 201 && json && json.ok) {
        show('success', 'Attendance recorded', `${json.data.subject}: you are marked present.`);
      } else if (res.status === 401) {
        show('warning', 'Signed out', 'Your session has ended. Sign in again, then scan.');
      } else {
        const err = (json && json.error) || {};
        const wait = json && json.retry_after ? ` (wait ${json.retry_after} s)` : '';
        show(err.code === 'already_marked' ? 'info' : 'danger', 'Not recorded',
          (MESSAGES[err.code] || err.message || 'The server rejected this scan.') + wait);
      }
    } catch (_) {
      show('danger', 'Network error', 'Could not reach the server. Check your connection and scan again.');
    }
    setBusy(false);
  }

  function onScanned(text) { submit(text); }

  $('startBtn').addEventListener('click', startScan);
  $('stopBtn').addEventListener('click', stopScan);
  $('manualForm').addEventListener('submit', (e) => { e.preventDefault(); const v = $('manualCode').value.trim(); if (v) submit(v); });
  if (!cameraSupported()) $('unsupported').classList.remove('d-none');

  // Release the camera whenever the page is hidden or left.
  document.addEventListener('visibilitychange', () => { if (document.hidden) stopScan(); });
  window.addEventListener('pagehide', stopScan);
})();
