CREATE TABLE users (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(32) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at    TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uq_users_username (username)
);

-- A member is whoever can sit in a room: a human user today, an AI later.
CREATE TABLE members (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    type         ENUM('human', 'ai') NOT NULL,
    user_id      BIGINT UNSIGNED NULL,
    display_name VARCHAR(64) NOT NULL,
    UNIQUE KEY uq_members_user (user_id),
    CONSTRAINT fk_members_user FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE rooms (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(64) NOT NULL,
    last_seq   BIGINT UNSIGNED NOT NULL DEFAULT 0,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
);

CREATE TABLE room_members (
    room_id   BIGINT UNSIGNED NOT NULL,
    member_id BIGINT UNSIGNED NOT NULL,
    joined_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (room_id, member_id),
    KEY idx_room_members_member (member_id),
    CONSTRAINT fk_room_members_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE,
    CONSTRAINT fk_room_members_member FOREIGN KEY (member_id) REFERENCES members (id)
);

CREATE TABLE room_invites (
    room_id    BIGINT UNSIGNED NOT NULL,
    user_id    BIGINT UNSIGNED NOT NULL,
    invited_by BIGINT UNSIGNED NOT NULL,
    created_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (room_id, user_id),
    KEY idx_room_invites_user (user_id),
    CONSTRAINT fk_room_invites_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE,
    CONSTRAINT fk_room_invites_user FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_room_invites_inviter FOREIGN KEY (invited_by) REFERENCES members (id)
);

CREATE TABLE messages (
    id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    room_id       BIGINT UNSIGNED NOT NULL,
    seq           BIGINT UNSIGNED NOT NULL,
    member_id     BIGINT UNSIGNED NOT NULL,
    client_msg_id VARCHAR(64) NOT NULL,
    content       TEXT NOT NULL,
    created_at_ms BIGINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_messages_room_seq (room_id, seq),
    UNIQUE KEY uq_messages_client (room_id, member_id, client_msg_id),
    CONSTRAINT fk_messages_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE,
    CONSTRAINT fk_messages_member FOREIGN KEY (member_id) REFERENCES members (id)
);
