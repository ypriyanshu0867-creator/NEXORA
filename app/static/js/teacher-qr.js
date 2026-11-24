
/* Teacher attendance page: one-time QR, session management and live records. */
(() => {
  'use strict';

  const app = document.getElementById('attendanceApp');
  if (!app) return;

  const csrfMeta = document.querySelector('meta[name="csrf-token"]');
  const csrf = csrfMeta ? csrfMeta.content : '';

  const $ = (id) => document.getElementById(id);

  const fmtTime = (value) =>
    new Date(value).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });

  let session = null;
  let qrExpiresAt = 0;
  let qrBusy = false;
  let ticker = null;
  let poller = null;
  let qrLoaded = false;

  async function api(method, path, body) {
    const headers = {
      Accept: 'application/json',
      'X-CSRFToken': csrf
    };

    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }

    try {
      const res = await fetch(path, {
        method,
        headers,
        credentials: 'same-origin',
        body: body === undefined ? undefined : JSON.stringify(body)
      });

      let json = null;

      try {
        json = await res.json();
      } catch (_) {
        // Response was not JSON.
      }

      if (res.ok && json && json.ok) {
        return { ok: true, data: json.data };
      }

      return {
        ok: false,
        status: res.status,
        error: (json && json.error) || {
          message: 'Unexpected server response.'
        }
      };
    } catch (_) {
      return {
        ok: false,
        status: 0,
        error: { message: 'Network error. Check your connection.' }
      };
    }
  }

  // ---- start form ------------------------------------------------------

  const form = $('startForm');

  const num = (id) => {
    const value = $(id).value.trim();
    return value === '' ? undefined : Number(value);
  };

  if (form) {
    $('classroom_id').addEventListener('change', (event) => {
      const option = event.target.selectedOptions[0];

      if (option && option.dataset.lat) {
        $('latitude').value = option.dataset.lat;
        $('longitude').value = option.dataset.lng;
        $('radius_m').value = option.dataset.radius;
      }
    });

    $('useLocation').addEventListener('click', () => {
      if (!navigator.geolocation) {
        $('locationHint').textContent =
          'This browser has no location support.';
        return;
      }

      $('locationHint').textContent = 'Getting your location...';

      navigator.geolocation.getCurrentPosition(
        (position) => {
          $('latitude').value =
            position.coords.latitude.toFixed(6);

          $('longitude').value =
            position.coords.longitude.toFixed(6);

          $('locationHint').textContent =
            `Location set (accuracy about ${Math.round(
              position.coords.accuracy
            )} m).`;
        },
        () => {
          $('locationHint').textContent =
            'Could not get your location. Allow location access or type the coordinates.';
        },
        {
          enableHighAccuracy: true,
          timeout: 15000,
          maximumAge: 0
        }
      );
    });

    form.addEventListener('submit', async (event) => {
      event.preventDefault();

      form.querySelectorAll('.is-invalid').forEach((element) => {
        element.classList.remove('is-invalid');
      });

      $('startError').classList.add('d-none');

      const button = $('startBtn');
      button.disabled = true;
      button.querySelector('.spinner-border').classList.remove('d-none');

      const geofence = $('geofence_enabled').checked;

      const body = {
        assignment_id: num('assignment_id'),
        qr_validity_seconds: num('qr_validity_seconds'),
        duration_minutes: num('duration_minutes'),
        geofence_enabled: geofence
      };

      if ($('classroom_id').value) {
        body.classroom_id = Number($('classroom_id').value);
      }

      if (geofence) {
        Object.assign(body, {
          radius_m: num('radius_m'),
          min_accuracy_m: num('min_accuracy_m')
        });

        if (num('latitude') !== undefined) {
          body.latitude = num('latitude');
        }

        if (num('longitude') !== undefined) {
          body.longitude = num('longitude');
        }
      }

      const result = await api(
        'POST',
        '/api/teacher/attendance-session',
        body
      );

      button.disabled = false;
      button.querySelector('.spinner-border').classList.add('d-none');

      if (result.ok) {
        window.NexoraUI.showToast('Session started.', 'success');
        openSession(result.data);
        return;
      }

      const fields = result.error.fields || {};

      Object.keys(fields).forEach((key) => {
        const input = $(key);
        const message = form.querySelector(
          `[data-error-for="${key}"]`
        );

        if (input) input.classList.add('is-invalid');
        if (message) message.textContent = fields[key];
      });

      $('startError').textContent = result.error.message;
      $('startError').classList.remove('d-none');
    });
  }

  // ---- live session ----------------------------------------------------

  function openSession(value) {
    session = value;
    qrLoaded = false;
    qrExpiresAt = 0;

    $('startPanel').classList.add('d-none');
    $('livePanel').classList.remove('d-none');

    $('liveTitle').textContent =
      `${value.subject_code} · ${value.subject_name} (${value.class_name})`;

    $('exportBtn').href =
      `/api/teacher/attendance?session_id=${value.id}&format=csv`;

    $('newSessionBtn').classList.add('d-none');

    if (value.state === 'active') {
      $('closeBtn').classList.remove('d-none');
      $('qrImage').classList.add('d-none');
      $('qrSpinner').classList.remove('d-none');
      $('manualCode').textContent = '';
      $('qrCountdown').textContent = '--';
    }

    renderState(value);

    clearInterval(ticker);
    clearInterval(poller);

    if (value.state === 'active') {
      // This endpoint is idempotent: it returns the same encrypted QR.
      fetchQr();

      ticker = setInterval(tick, 1000);
      poller = setInterval(refresh, 3000);
    }

    refresh();
  }

  function renderState(value) {
    const badge = $('liveState');

    badge.textContent =
      value.state === 'active'
        ? 'Active'
        : value.state === 'closed'
          ? 'Closed'
          : 'Time is up';

    badge.className =
      'badge ' +
      (value.state === 'active'
        ? 'text-bg-success'
        : 'text-bg-secondary');

    $('sessionMeta').textContent = value.geofence_enabled
      ? `Geofence: ${value.geofence.radius_m} m radius, GPS accuracy ${value.geofence.max_accuracy_m} m or better. Ends ${fmtTime(value.ends_at)}.`
      : `Geofence off. Ends ${fmtTime(value.ends_at)}.`;

    if (value.state !== 'active') {
      endLive();
    }
  }

  function endLive() {
    clearInterval(ticker);
    clearInterval(poller);

    $('qrImage').classList.add('d-none');
    $('qrSpinner').classList.add('d-none');
    $('qrCountdown').textContent = '--';
    $('manualCode').textContent = '';

    $('closeBtn').classList.add('d-none');
    $('newSessionBtn').classList.remove('d-none');
  }

  // ---- one-time QR -----------------------------------------------------

  async function fetchQr() {
    if (qrBusy || !session || session.state !== 'active') {
      return;
    }

    qrBusy = true;

    const result = await api(
      'POST',
      `/api/teacher/attendance-session/${session.id}/qr`
    );

    qrBusy = false;

    if (!result.ok) {
      $('qrSpinner').classList.add('d-none');
      $('qrImage').classList.add('d-none');

      if (result.status === 409) {
        $('qrCountdown').textContent = 'Unavailable';
        $('manualCode').textContent = result.error.message;
        return;
      }

      window.NexoraUI.showToast(
        result.error.message,
        'warning'
      );

      return;
    }

    const qr = result.data;

    qrLoaded = true;

    qrExpiresAt =
      Date.now() + Math.max(0, qr.seconds_remaining) * 1000;

    $('qrImage').src = qr.image;
    $('qrSpinner').classList.add('d-none');

    $('manualCode').textContent = qr.payload;

    if (qr.seconds_remaining > 0) {
      $('qrImage').classList.remove('d-none');
    } else {
      expireQr();
    }

    tick();
  }

  function expireQr() {
    $('qrImage').classList.add('d-none');
    $('qrSpinner').classList.add('d-none');
    $('qrCountdown').textContent = 'Expired';
    $('manualCode').textContent = 'This QR has expired. Start a new attendance session for a new QR.';
  }

  function tick() {
    if (!session || session.state !== 'active') {
      return;
    }

    if (!qrLoaded) {
      return;
    }

    const remaining = Math.max(
      0,
      Math.ceil((qrExpiresAt - Date.now()) / 1000)
    );

    if (remaining <= 0) {
      expireQr();
      clearInterval(ticker);
      return;
    }

    $('qrCountdown').textContent = remaining;
  }

  // ---- live attendance records ---------------------------------------

  async function refresh() {
    if (!session) {
      return;
    }

    const result = await api(
      'GET',
      `/api/teacher/attendance-session/${session.id}`
    );

    if (!result.ok) {
      return;
    }

    session = result.data;

    renderState(session);

    $('presentCount').textContent = session.present_count;
    $('enrolledCount').textContent = session.enrolled_count;

    const body = $('recordsBody');
    body.replaceChildren();

    session.records.forEach((record) => {
      const row = document.createElement('tr');

      [
        record.student,
        record.roll_number || '',
        fmtTime(record.marked_at)
      ].forEach((value) => {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.append(cell);
      });

      const distance = document.createElement('td');
      distance.className = 'text-end';

      distance.textContent =
        record.distance_m === null
          ? '--'
          : `${Math.round(record.distance_m)} m`;

      row.append(distance);
      body.append(row);
    });

    $('recordsEmpty').classList.toggle(
      'd-none',
      session.records.length > 0
    );
  }

  // ---- close session ---------------------------------------------------

  $('confirmClose').addEventListener('click', async () => {
    if (!session) return;

    const result = await api(
      'POST',
      `/api/teacher/attendance-session/${session.id}/close`
    );

    bootstrap.Modal.getInstance($('closeModal')).hide();

    if (result.ok) {
      window.NexoraUI.showToast(
        'Session closed.',
        'success'
      );

      session = result.data;
      renderState(session);
      refresh();
    } else {
      window.NexoraUI.showToast(
        result.error.message,
        'danger'
      );
    }
  });

  // ---- start another session -----------------------------------------

  $('newSessionBtn').addEventListener('click', () => {
    clearInterval(ticker);
    clearInterval(poller);

    session = null;
    qrLoaded = false;
    qrExpiresAt = 0;

    $('livePanel').classList.add('d-none');
    $('startPanel').classList.remove('d-none');
  });

  // ---- resume existing active session after reload -------------------

  (async () => {
    const result = await api(
      'GET',
      '/api/teacher/attendance-session/active'
    );

    if (result.ok && result.data.sessions.length) {
      openSession(result.data.sessions[0]);
    }
  })();
})();
