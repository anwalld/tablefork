-- A private AI role's default persona, prefilled when it is invited into a
-- room. Each room still keeps its own copy in room_ai_personas.
ALTER TABLE ai_profiles
    ADD COLUMN default_persona TEXT NULL,
    ADD CONSTRAINT ck_ai_profile_default_persona CHECK (
        default_persona IS NULL OR OCTET_LENGTH(default_persona) <= 16384
    );
