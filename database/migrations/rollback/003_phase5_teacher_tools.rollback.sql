-- Rollback for migration 003. Run MANUALLY, then: DELETE FROM schema_migrations WHERE version = '003_phase5_teacher_tools';
-- WARNING: dropping attendance_corrections discards the correction history (export it first), and remove the append-only
-- triggers first with: python scripts\protect_audit_tables.py --remove (they would otherwise block nothing here, but should not outlive the table). Before shrinking the notices ENUM:
--   UPDATE notices SET category = 'general' WHERE category = 'material';

ALTER TABLE notices
    DROP FOREIGN KEY fk_notices_class,
    DROP KEY idx_notices_class,
    DROP COLUMN class_id,
    MODIFY COLUMN category ENUM('exam','holiday','event','timetable','assignment','general',
                                'result','fee','library') NOT NULL DEFAULT 'general';
ALTER TABLE learning_materials DROP COLUMN description;
ALTER TABLE syllabus DROP COLUMN is_published;
DROP TABLE attendance_corrections;
ALTER TABLE attendance_records DROP COLUMN source;
