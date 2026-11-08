-- Breakout (small group) rooms. A breakout is an ordinary room whose
-- parent_room_id links it to the room it split from; fork_seq records the
-- point it forked so its context can include only the parent's history up to
-- that point. depth bounds nesting, status drives its lifecycle.
ALTER TABLE rooms
    ADD COLUMN parent_room_id BIGINT UNSIGNED NULL,
    ADD COLUMN fork_seq BIGINT UNSIGNED NULL,
    ADD COLUMN depth INT UNSIGNED NOT NULL DEFAULT 0,
    ADD COLUMN topic VARCHAR(255) NULL,
    ADD COLUMN status ENUM('active','concluding','closed') NOT NULL DEFAULT 'active',
    ADD KEY idx_rooms_parent (parent_room_id),
    ADD CONSTRAINT fk_rooms_parent FOREIGN KEY (parent_room_id)
        REFERENCES rooms (id) ON DELETE CASCADE;