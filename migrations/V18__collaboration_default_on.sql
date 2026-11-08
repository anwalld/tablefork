-- New rooms start with collaboration on (host, council, breakouts, timeout
-- confirmation). Turning it off falls back to a plain multi-AI chat room.
-- Existing rooms keep their current setting.
ALTER TABLE rooms ALTER COLUMN collaboration_enabled SET DEFAULT TRUE;
