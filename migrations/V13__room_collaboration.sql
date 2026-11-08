-- Room-level switch that turns the collaborative flow on. When it is off,
-- the AIs behave exactly as before: probability-based pickup and idle chat.
ALTER TABLE rooms
    ADD COLUMN collaboration_enabled BOOLEAN NOT NULL DEFAULT FALSE;