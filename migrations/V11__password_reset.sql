-- Password recovery via a security question. The question is ordinary text;
-- only a slow salted hash of the answer is stored, like a password. Both are
-- nullable so accounts created before this feature keep working.
ALTER TABLE users
    ADD COLUMN security_question VARCHAR(255) NULL,
    ADD COLUMN security_answer_hash VARCHAR(255) NULL;