# Phase 3: QR attendance and anti-proxy checks

## How a check-in works
1. A teacher starts a session for one of **their** subject+class assignments (current semester only).
   The server stores the geofence as a **snapshot** with the session.
2. The teacher page requests QR codes (`POST /api/teacher/attendance-session/<id>/qr`). Each code is
   `NXR1.<256-bit random token>`; only its SHA-256 is stored. A new code is fetched automatically a
   few seconds before the current one expires. Old codes are not cancelled early; they simply run out
   (short overlap, so a student mid-scan is not rejected). Closing the session expires all of them.
3. The student scans, the browser adds its location, and `POST /api/attendance/mark` sends
   `{qr, latitude, longitude, accuracy}`. The student identity comes from the server-side login,
   never from the request.
4. The server checks, in one transaction: rate limit, QR format, token to session lookup, session live,
   token not expired, enrolled, not already marked, device policy, geofence, then inserts. The
   `UNIQUE(session_id, student_id)` key is the final duplicate guard. Rejections are written to
   `attendance_security_events`. Success is shown only after the server answers 201.

QR validity (`qr_validity_seconds`, default 120, 15 to 600) and session length (default 10 minutes,
1 to 240) are separate. Expiry of one does not end the other.

## Policies (explicit decisions)
| Topic | Policy |
|---|---|
| QR reuse | Multi-use inside its window (one screen, many students). Replay fails by expiry, closure, or the one-record-per-student rule. |
| Geofence | Strict: distance <= radius, and reported accuracy <= the teacher's limit. Accuracy is not subtracted from distance. |
| Location stored | Only distance and accuracy. Raw coordinates are never saved. |
| Geofence off | Location is not required or stored. |
| Device cookie | `nexora_device`: random, HttpOnly, SameSite=Lax, Secure over HTTPS, 365 days. Only its SHA-256 is stored. |
| Missing/invalid cookie | Student login refused (`device_required`) after the password is verified; reloading the login page issues a cookie. Teachers/admins are not device-bound. |
| Cooldown | 180 s (configurable). Starts on student logout, or when another student tries to log in while one is still active on the device. |
| During cooldown | Same student: allowed. Different student: HTTP 429 plus seconds remaining. After cooldown: allowed. |
| Stale "active" student | Ignored after the session lifetime (2 h) without activity, so a forgotten login cannot lock a device forever. |
| Marking | Needs a registered device whose last student is you. One device can mark only ONE student per session. |
| Rate limit | 10 rejected scans per student per minute, then HTTP 429. |
| Attendance % | attended / conducted, per subject and overall. |
| "Conducted" | A finished session (closed or past its end) with at least one student marked. Finished sessions with nobody marked count as not held. |
| Excused absence | Not supported by the Phase 1 schema; every missed conducted session counts as absent. |
| Session eligibility | Assignment must belong to the teacher and to the **current** semester. One active session per assignment (database-enforced). |
| Retention | `scripts\purge_old_events.py` deletes old security/device events and can blank stored distances. It is manual (Task Scheduler). |
| Time | UTC everywhere. Attendance/QR/cooldown use the application clock (`app/clock.py`, replaceable in tests). |

## Security limitations (please read)
* **Browser location can be spoofed** (developer tools, mock-location apps, rooted/jailbroken phones).
  Geofencing discourages casual proxying; it does not prove physical presence.
* **The device cookie can be deleted, copied or bypassed** (clearing site data, private window, another
  browser). The cooldown and one-device-one-student rules are deterrents, not hardware identity, and
  fingerprinting is deliberately not used.
* A student can photograph the QR and send it to a friend. Short QR validity, the geofence and the
  device rules make this harder, not impossible.
* A shared legitimate device (for example two friends on one phone) is blocked by design.
* Possible future hardening: managed-device attestation, campus Wi-Fi/network checks, Bluetooth beacons,
  supervised or manual check-in for flagged cases.
* The QR scanner library (html5-qrcode) and Bootstrap load from a CDN without integrity hashes.
  Vendor them locally for a real deployment.

## Windows 11 demonstration (Command Prompt)
```
cd path\to\nexora
venv\Scripts\activate
python -m pip install -r requirements.txt
net start MySQL80
python scripts\migrate.py
python scripts\seed_demo.py
python run.py
```
(`net start` needs an Administrator Command Prompt; the service may be named differently. If the
database is not initialised yet, run `python scripts\init_db.py` first. It also applies migrations.
`seed_demo.py` says so if the demo data already exists.)

Teacher (computer): open http://localhost:5000, sign in as `asha.verma@nexora.demo` /
`Nexora@2026`, click **Attendance Sessions**. Choose AD301, classroom Room A-101, press
**Use my current location** (the seeded coordinates are placeholders), set radius and QR validity
(use 15 s to demo expiry), **Start session**. The QR, countdown and live list appear.

Phone (needs HTTPS for camera and location): stop the server, then
```
set FLASK_HTTPS=1
set FLASK_HOST=0.0.0.0
python run.py
ipconfig
```
Use the IPv4 address shown. On the phone (same Wi-Fi) open `https://<that-address>:5000`, accept the
self-signed certificate warning, sign in as `aarav.sharma@nexora.demo`, open **Scan QR**, allow camera
and location, scan. Allow Python through the Windows firewall for private networks if asked.
(Debug mode is switched off automatically when the server listens on the network.)

Checks to try
* **Success:** the student sees "Attendance recorded"; the teacher list updates within about 3 seconds;
  the student's **My Attendance** page shows the session after the teacher closes it.
* **Expired QR:** set QR validity to 15 s, photograph the QR, wait 20 s, scan the photo: rejected.
* **Outside geofence:** start a session with coordinates far from the room (or radius 5 m), scan: the
  message says how far away you are.
* **Cooldown:** on the phone, sign out of aarav, immediately sign in as `diya.patel@nexora.demo`:
  refused with the seconds left. Sign back in as aarav: allowed. After 180 s diya can sign in.
* **Reports:** teacher page **Export CSV**; student **My Attendance**.

Tests
```
python scripts\init_db.py --test
python -m pytest tests -v
```
