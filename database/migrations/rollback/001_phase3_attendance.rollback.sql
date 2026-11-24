-- Rollback for migration 001. Run MANUALLY, only if you must undo it:
--   mysql -u nexora_app -p nexora < database\migrations\rollback\001_phase3_attendance.rollback.sql
-- Then delete the row:  DELETE FROM schema_migrations WHERE version = '001_phase3_attendance';
-- WARNING: first delete (or export) security events that use the three new types, or MySQL
-- will refuse to shrink the ENUM:
--   DELETE FROM attendance_security_events
--   WHERE event_type IN ('location_invalid','device_unregistered','device_reused');

ALTER TABLE attendance_records DROP KEY idx_attendance_session_device;

ALTER TABLE attendance_security_events
    MODIFY COLUMN event_type ENUM('qr_invalid','qr_expired','session_closed','not_enrolled',
        'duplicate','location_denied','location_missing','location_inaccurate','outside_radius',
        'device_cooldown','rate_limited') NOT NULL;

ALTER TABLE attendance_sessions
    DROP KEY uq_one_active_session_per_assignment,
    DROP COLUMN active_assignment_id;
