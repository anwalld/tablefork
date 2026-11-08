-- History summaries and memories for collaboration rooms.
--
-- room_summaries: one rolling summary per room. upto_seq is the last message
-- folded into it; messages after it are summarized in later segments. Redis
-- holds the hot copy, this table is the durable one.
CREATE TABLE room_summaries (
    room_id    BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    upto_seq   BIGINT UNSIGNED NOT NULL DEFAULT 0,
    content    TEXT NOT NULL,
    updated_at_ms BIGINT NOT NULL,
    CONSTRAINT fk_room_summaries_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE
);

-- Room memories, layered as in LobeHub's memory-user-memory, adapted to a
-- shared room: conclusions, background facts, to-dos and lessons learned.
CREATE TABLE room_memories (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    room_id    BIGINT UNSIGNED NOT NULL,
    layer      ENUM('conclusion','fact','todo','lesson') NOT NULL,
    content    VARCHAR(1000) NOT NULL,
    source_seq BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at_ms BIGINT NOT NULL,
    KEY idx_room_memories_room (room_id, id),
    CONSTRAINT fk_room_memories_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE
);

-- User memories: preferences and identity/background. Only injected into rooms
-- the user is a member of; source_room_id records where it was learned.
CREATE TABLE user_memories (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id    BIGINT UNSIGNED NOT NULL,
    layer      ENUM('preference','identity') NOT NULL,
    content    VARCHAR(1000) NOT NULL,
    source_room_id BIGINT UNSIGNED NULL,
    created_at_ms BIGINT NOT NULL,
    KEY idx_user_memories_user (user_id, id),
    CONSTRAINT fk_user_memories_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
