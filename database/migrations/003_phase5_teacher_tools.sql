-- Migration 003 (Phase 5, teacher side). Existing rows are preserved.

-- 1. Attendance corrections. The original value is kept here; the attendance record itself
--    is added or removed so every existing report keeps working. This table is append-only.
ALTER TABLE attendance_records ADD COLUMN source ENUM('qr','correction') NOT NULL DEFAULT 'qr';

CREATE TABLE attendance_corrections (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id         INT UNSIGNED NOT NULL,
    student_id         INT UNSIGNED NOT NULL,
    previous_present   TINYINT(1)   NOT NULL,
    new_present        TINYINT(1)   NOT NULL,
    old_marked_at      DATETIME     NULL,
    old_distance_m     DECIMAL(8,2) NULL,
    old_gps_accuracy_m DECIMAL(8,2) NULL,
    old_device_id      INT UNSIGNED NULL,
    old_source         ENUM('qr','correction') NULL,
    reason             VARCHAR(255) NOT NULL,
    corrected_by       INT UNSIGNED NOT NULL,
    corrected_at       DATETIME     NOT NULL,
    PRIMARY KEY (id),
    KEY idx_corrections_session (session_id, corrected_at),
    KEY idx_corrections_student (student_id, corrected_at),
    CONSTRAINT fk_corr_session FOREIGN KEY (session_id) REFERENCES attendance_sessions (id) ON DELETE RESTRICT,
    CONSTRAINT fk_corr_student FOREIGN KEY (student_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_corr_teacher FOREIGN KEY (corrected_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_corr_change CHECK (previous_present <> new_present)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 2. (Append-only triggers are NOT part of this migration: on a default MySQL 8 server a non-SUPER
--    account cannot create triggers. Install them once with: python scripts\protect_audit_tables.py)

-- 3. Syllabus units can be published or hidden.
ALTER TABLE syllabus ADD COLUMN is_published TINYINT(1) NOT NULL DEFAULT 1;

-- 4. Learning materials get a description.
ALTER TABLE learning_materials ADD COLUMN description VARCHAR(255) NULL;

-- 5. Notices: a class target (class-specific announcements) and a learning-material category.
ALTER TABLE notices
    MODIFY COLUMN category ENUM('exam','holiday','event','timetable','assignment','general',
                                'result','fee','library','material') NOT NULL DEFAULT 'general',
    ADD COLUMN class_id INT UNSIGNED NULL,
    ADD KEY idx_notices_class (class_id),
    ADD CONSTRAINT fk_notices_class FOREIGN KEY (class_id) REFERENCES classes (id) ON DELETE SET NULL;
