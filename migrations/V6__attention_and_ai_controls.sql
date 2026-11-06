ALTER TABLE room_members ADD COLUMN read_seq BIGINT UNSIGNED NOT NULL DEFAULT 0;
UPDATE room_members rm JOIN rooms r ON r.id = rm.room_id SET rm.read_seq = r.last_seq;
ALTER TABLE messages ADD COLUMN stopped BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE message_mentions (
    room_id BIGINT UNSIGNED NOT NULL,
    seq BIGINT UNSIGNED NOT NULL,
    member_id BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (room_id, member_id, seq),
    CONSTRAINT fk_mentions_message FOREIGN KEY (room_id, seq)
        REFERENCES messages(room_id, seq) ON DELETE CASCADE,
    CONSTRAINT fk_mentions_member FOREIGN KEY (member_id) REFERENCES members(id)
);
CREATE TABLE room_ai_controls (
    room_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    paused BOOLEAN NOT NULL DEFAULT FALSE,
    ignore_through_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
    CONSTRAINT fk_ai_controls_room FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);
CREATE TABLE ai_generations (
    generation_id VARCHAR(64) NOT NULL PRIMARY KEY,
    room_id BIGINT UNSIGNED NOT NULL,
    member_id BIGINT UNSIGNED NOT NULL,
    payer_member_id BIGINT UNSIGNED NOT NULL,
    trigger_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
    attempt INT UNSIGNED NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    status ENUM('generating','final','stopped','saved') NOT NULL DEFAULT 'generating',
    stopped BOOLEAN NOT NULL DEFAULT FALSE,
    KEY idx_generations_room_status (room_id, status),
    CONSTRAINT fk_generations_room FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);
