
-- Rollback for migration 004.
-- Run MANUALLY, then:
-- DELETE FROM schema_migrations WHERE version = '004_secure_qr_recovery';

ALTER TABLE attendance_qr_tokens
    DROP COLUMN encrypted_token;

