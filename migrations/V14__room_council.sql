-- Council chairs and their ballots. The live round state lives in Redis
-- (council:{room}:{round}); these tables are the durable record the conclusion
-- message and history read back.
CREATE TABLE room_council (
    room_id   BIGINT UNSIGNED NOT NULL,
    member_id BIGINT UNSIGNED NOT NULL,
    PRIMARY KEY (room_id, member_id),
    CONSTRAINT fk_room_council_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE,
    CONSTRAINT fk_room_council_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
);

CREATE TABLE council_ballots (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    room_id           BIGINT UNSIGNED NOT NULL,
    round             INT UNSIGNED NOT NULL,
    topic             TEXT NOT NULL,
    proposer_member_id BIGINT UNSIGNED NOT NULL,
    lead_member_id    BIGINT UNSIGNED NOT NULL,
    outcome           ENUM('pass','reject','invalid','overruled') NULL,
    overridden_by_member_id BIGINT UNSIGNED NULL,
    created_at_ms     BIGINT UNSIGNED NOT NULL,
    UNIQUE KEY uq_council_ballots_round (room_id, round),
    CONSTRAINT fk_council_ballots_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE
);

CREATE TABLE council_votes (
    ballot_id BIGINT UNSIGNED NOT NULL,
    member_id BIGINT UNSIGNED NOT NULL,
    verdict   ENUM('pass','reject','abstain') NULL,
    reason    TEXT NULL,
    PRIMARY KEY (ballot_id, member_id),
    CONSTRAINT fk_council_votes_ballot FOREIGN KEY (ballot_id) REFERENCES council_ballots (id) ON DELETE CASCADE,
    CONSTRAINT fk_council_votes_member FOREIGN KEY (member_id) REFERENCES members (id) ON DELETE CASCADE
);