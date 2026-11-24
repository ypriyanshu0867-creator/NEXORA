-- Rollback for migration 002. Run MANUALLY, then: DELETE FROM schema_migrations WHERE version = '002_phase4_student_portal';
-- WARNING: this discards the new columns' data. Before shrinking an ENUM, move or delete rows that use the
-- new values (notices with category result/fee/library, fee records with status 'waived').
--   UPDATE notices SET category = 'general' WHERE category IN ('result','fee','library');
--   UPDATE student_fee_records SET status = 'paid' WHERE status = 'waived';
--   DELETE FROM fee_transactions WHERE status <> 'confirmed';   (or export them first)

ALTER TABLE fee_transactions DROP COLUMN status;
ALTER TABLE student_fee_records
    MODIFY COLUMN status ENUM('unpaid','partial','paid') NOT NULL DEFAULT 'unpaid',
    DROP COLUMN remarks;
ALTER TABLE fee_structures DROP COLUMN category, DROP COLUMN semester_number;
ALTER TABLE notices
    DROP KEY idx_notices_visibility,
    DROP COLUMN attachment_original_name, DROP COLUMN attachment_stored_name, DROP COLUMN is_pinned,
    DROP COLUMN expires_at, DROP COLUMN published_at, DROP COLUMN semester_number,
    MODIFY COLUMN category ENUM('exam','holiday','event','timetable','assignment','general')
        NOT NULL DEFAULT 'general';
ALTER TABLE learning_materials DROP COLUMN is_published;
ALTER TABLE syllabus DROP COLUMN updated_at, DROP COLUMN reference_books, DROP COLUMN learning_outcomes;
ALTER TABLE library_books DROP COLUMN publication_year, DROP COLUMN publisher;
ALTER TABLE grading_scheme_rules DROP COLUMN is_pass;
