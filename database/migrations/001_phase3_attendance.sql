-- Migration 001 (Phase 3): attendance hardening. Safe on a database that already has data.

-- 1. At most ONE active session per teaching assignment (subject + class + semester + teacher).
--    The generated column is the assignment id while the session is active and NULL once closed.
--    MySQL allows many NULLs in a unique key, so closed sessions never collide.
ALTER TABLE attendance_sessions
    ADD COLUMN active_assignment_id INT UNSIGNED
        GENERATED ALWAYS AS (CASE WHEN status = 'active' THEN assignment_id ELSE NULL END) STORED,
    ADD UNIQUE KEY uq_one_active_session_per_assignment (active_assignment_id);

-- 2. Three more rejection reasons the attendance service records.
ALTER TABLE attendance_security_events
    MODIFY COLUMN event_type ENUM('qr_invalid','qr_expired','session_closed','not_enrolled',
        'duplicate','location_denied','location_missing','location_inaccurate','outside_radius',
        'device_cooldown','rate_limited','location_invalid','device_unregistered','device_reused')
        NOT NULL;

-- 3. Speeds up the "one device, one student per session" check.
ALTER TABLE attendance_records
    ADD KEY idx_attendance_session_device (session_id, device_id);
