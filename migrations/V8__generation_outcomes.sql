-- Separate processing progress from the reason a generation ended.
ALTER TABLE ai_generations
    ADD COLUMN phase ENUM('generating','ready','finished') NOT NULL DEFAULT 'generating',
    ADD COLUMN outcome ENUM('completed','failed','stopped','cancelled','legacy_unknown') NULL,
    ADD COLUMN last_nonempty_content TEXT NULL,
    ADD COLUMN content_attempt INT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN failure_code VARCHAR(64) NOT NULL DEFAULT '',
    ADD COLUMN snapshot_revision BIGINT UNSIGNED NOT NULL DEFAULT 0,
    ADD KEY idx_generation_phase (room_id, phase);

UPDATE ai_generations g
LEFT JOIN messages m ON m.room_id=g.room_id AND m.member_id=g.member_id
    AND m.client_msg_id=g.generation_id
SET g.phase=CASE
        WHEN m.seq IS NOT NULL OR g.status='saved' THEN 'finished'
        WHEN g.stopped OR g.status IN ('final','stopped') THEN 'ready'
        ELSE 'generating' END,
    g.outcome=CASE
        WHEN m.seq IS NOT NULL THEN IF(m.stopped,'stopped','completed')
        WHEN g.stopped OR g.status='stopped' THEN 'stopped'
        WHEN g.status='final' THEN 'completed'
        WHEN g.status='saved' THEN 'legacy_unknown'
        ELSE NULL END,
    g.last_nonempty_content=IF(CHAR_LENGTH(TRIM(g.content))>0,g.content,NULL),
    g.content_attempt=IF(CHAR_LENGTH(TRIM(g.content))>0,g.attempt,0);

ALTER TABLE messages
    ADD COLUMN generation_outcome ENUM('completed','failed','stopped') NOT NULL DEFAULT 'completed',
    ADD COLUMN generation_attempt INT UNSIGNED NOT NULL DEFAULT 0;
UPDATE messages SET generation_outcome='stopped' WHERE stopped=TRUE;

ALTER TABLE room_ai_controls
    ADD COLUMN revision BIGINT UNSIGNED NOT NULL DEFAULT 0;
