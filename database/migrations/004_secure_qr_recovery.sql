ALTER TABLE attendance_qr_tokens
ADD COLUMN encrypted_token VARCHAR(255) NULL AFTER token_hash;