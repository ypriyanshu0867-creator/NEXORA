-- Migration 002 (Phase 4): student portal fields. Existing rows are preserved.

-- 1. Which grade bands count as a pass. Existing bands with 0 grade points are marked as fail.
ALTER TABLE grading_scheme_rules ADD COLUMN is_pass TINYINT(1) NOT NULL DEFAULT 1;
UPDATE grading_scheme_rules SET is_pass = 0 WHERE grade_point = 0;

-- 2. Library: optional publisher and publication year.
ALTER TABLE library_books
    ADD COLUMN publisher VARCHAR(120) NULL,
    ADD COLUMN publication_year SMALLINT UNSIGNED NULL;

-- 3. Syllabus: learning outcomes, reference books, last update time.
ALTER TABLE syllabus
    ADD COLUMN learning_outcomes TEXT NULL,
    ADD COLUMN reference_books TEXT NULL,
    ADD COLUMN updated_at DATETIME NULL;

-- 4. Learning materials: unpublished files are never offered to students.
ALTER TABLE learning_materials ADD COLUMN is_published TINYINT(1) NOT NULL DEFAULT 1;

-- 5. Notices: more categories, target semester, publish/expiry times, pinning, one attachment.
--    published_at NULL means "published at created_at". is_active = 0 means unpublished/withdrawn.
ALTER TABLE notices
    MODIFY COLUMN category ENUM('exam','holiday','event','timetable','assignment','general',
                                'result','fee','library') NOT NULL DEFAULT 'general',
    ADD COLUMN semester_number TINYINT UNSIGNED NULL,
    ADD COLUMN published_at DATETIME NULL,
    ADD COLUMN expires_at DATETIME NULL,
    ADD COLUMN is_pinned TINYINT(1) NOT NULL DEFAULT 0,
    ADD COLUMN attachment_stored_name VARCHAR(48) NULL,
    ADD COLUMN attachment_original_name VARCHAR(255) NULL,
    ADD KEY idx_notices_visibility (is_active, published_at, expires_at);

-- 6. Fees: category and semester on the structure, waived status and remarks on the record,
--    and a status on each transaction so pending/failed payments are never counted.
ALTER TABLE fee_structures
    ADD COLUMN category VARCHAR(40) NOT NULL DEFAULT 'Tuition',
    ADD COLUMN semester_number TINYINT UNSIGNED NULL;
ALTER TABLE student_fee_records
    MODIFY COLUMN status ENUM('unpaid','partial','paid','waived') NOT NULL DEFAULT 'unpaid',
    ADD COLUMN remarks VARCHAR(255) NULL;
ALTER TABLE fee_transactions
    ADD COLUMN status ENUM('confirmed','pending','failed') NOT NULL DEFAULT 'confirmed';
