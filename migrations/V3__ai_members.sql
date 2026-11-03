-- Room roles. Every room has exactly one owner, kept so by the application.
ALTER TABLE room_members
    ADD COLUMN role            ENUM('owner', 'admin', 'member') NOT NULL DEFAULT 'member',
    ADD COLUMN role_changed_at TIMESTAMP(3) NULL;

-- Rooms created before roles existed get their earliest human member as owner.
UPDATE room_members rm
JOIN (
    SELECT room_id, member_id
    FROM (
        SELECT rm2.room_id,
               rm2.member_id,
               ROW_NUMBER() OVER (PARTITION BY rm2.room_id
                                  ORDER BY rm2.joined_at, rm2.member_id) AS position
        FROM room_members rm2
        JOIN members m ON m.id = rm2.member_id
        WHERE m.type = 'human'
    ) ranked
    WHERE position = 1
) first_human ON first_human.room_id = rm.room_id
             AND first_human.member_id = rm.member_id
SET rm.role = 'owner';

-- The preset AI members. Model names and personas live in ai/agents.toml,
-- keyed by slug.
CREATE TABLE ai_profiles (
    member_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
    slug      VARCHAR(32) NOT NULL,
    provider  ENUM('deepseek', 'openai', 'anthropic', 'gemini') NOT NULL,
    UNIQUE KEY uq_ai_profiles_slug (slug),
    CONSTRAINT fk_ai_profiles_member FOREIGN KEY (member_id) REFERENCES members (id)
);

INSERT INTO members (type, user_id, display_name) VALUES
    ('ai', NULL, 'DeepSeek'),
    ('ai', NULL, 'GPT'),
    ('ai', NULL, 'Claude'),
    ('ai', NULL, 'Gemini');

INSERT INTO ai_profiles (member_id, slug, provider)
SELECT id,
       CASE display_name
           WHEN 'DeepSeek' THEN 'deepseek'
           WHEN 'GPT' THEN 'gpt'
           WHEN 'Claude' THEN 'claude'
           WHEN 'Gemini' THEN 'gemini'
       END,
       CASE display_name
           WHEN 'DeepSeek' THEN 'deepseek'
           WHEN 'GPT' THEN 'openai'
           WHEN 'Claude' THEN 'anthropic'
           WHEN 'Gemini' THEN 'gemini'
       END
FROM members
WHERE type = 'ai' AND display_name IN ('DeepSeek', 'GPT', 'Claude', 'Gemini');

-- Requests from ordinary members to add an AI, awaiting an owner or admin.
CREATE TABLE ai_member_requests (
    room_id      BIGINT UNSIGNED NOT NULL,
    member_id    BIGINT UNSIGNED NOT NULL,
    requested_by BIGINT UNSIGNED NOT NULL,
    created_at   TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (room_id, member_id),
    CONSTRAINT fk_ai_requests_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_requests_member FOREIGN KEY (member_id) REFERENCES members (id),
    CONSTRAINT fk_ai_requests_requester FOREIGN KEY (requested_by) REFERENCES members (id)
);

-- Users' own model API keys, encrypted by the AI service with AES-256-GCM.
-- The master key is never stored in the database.
CREATE TABLE user_api_keys (
    user_id    BIGINT UNSIGNED NOT NULL,
    provider   ENUM('deepseek', 'openai', 'anthropic', 'gemini') NOT NULL,
    ciphertext VARBINARY(2048) NOT NULL,
    nonce      BINARY(12) NOT NULL,
    last4      VARCHAR(4) NOT NULL,
    updated_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, provider),
    CONSTRAINT fk_user_api_keys_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);
