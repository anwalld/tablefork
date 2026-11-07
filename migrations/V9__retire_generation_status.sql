-- Apply with the coordinated C++/Python upgrade. V8 preserves the old result
-- meaning before the obsolete mixed status and duplicate stop flag are retired.
ALTER TABLE ai_generations
    DROP COLUMN status,
    DROP COLUMN stopped,
    ADD CONSTRAINT ck_generation_result CHECK (
        (phase='generating' AND outcome IS NULL)
        OR (phase IN ('ready','finished') AND outcome IS NOT NULL)
    );
