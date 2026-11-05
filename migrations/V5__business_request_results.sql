-- No FK to business resources: deleting a room must not make an old request
-- eligible to create it again. Contains results, never raw credentials.
CREATE TABLE business_request_results (
    actor VARBINARY(128) NOT NULL,
    request_id VARBINARY(128) NOT NULL,
    operation VARBINARY(128) NOT NULL,
    fingerprint CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    result JSON NULL,
    kind ENUM('sql', 'external') NOT NULL DEFAULT 'sql',
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (actor, request_id)
);
