CREATE TABLE room_bans (
    room_id    BIGINT UNSIGNED NOT NULL,
    user_id    BIGINT UNSIGNED NOT NULL,
    banned_by  BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (room_id, user_id),
    KEY idx_room_bans_user (user_id),
    CONSTRAINT fk_room_bans_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE,
    CONSTRAINT fk_room_bans_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_room_bans_by FOREIGN KEY (banned_by) REFERENCES members (id) ON DELETE SET NULL
);
