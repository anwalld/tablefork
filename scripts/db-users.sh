#!/usr/bin/env bash
# Creates the AI service's MySQL account with only the access it needs. Runs
# as root after the migrations, on every start, so a changed AI_MYSQL_PASSWORD
# takes effect and new grants are applied. Every statement is safe to repeat.
set -euo pipefail

defaults=$(mktemp)
trap 'rm -f "$defaults"' EXIT
printf '[client]\nhost=%s\nuser=root\npassword=%s\n' "$MYSQL_HOST" "$MYSQL_ROOT_PASSWORD" >"$defaults"

# Quote the password as an SQL string literal.
password=${AI_MYSQL_PASSWORD//\\/\\\\}
password=${password//\'/\'\'}

mysql --defaults-extra-file="$defaults" <<SQL
CREATE USER IF NOT EXISTS 'ai'@'%' IDENTIFIED BY '$password';
ALTER USER 'ai'@'%' IDENTIFIED BY '$password';
GRANT SELECT, INSERT, UPDATE ON chat.user_api_keys TO 'ai'@'%';
GRANT SELECT, INSERT ON chat.room_ai_controls TO 'ai'@'%';
GRANT SELECT, INSERT, UPDATE ON chat.ai_generations TO 'ai'@'%';
GRANT SELECT ON chat.ai_profiles TO 'ai'@'%';
GRANT SELECT ON chat.ai_model_types TO 'ai'@'%';
GRANT SELECT ON chat.room_ai_personas TO 'ai'@'%';
GRANT SELECT ON chat.messages TO 'ai'@'%';
GRANT SELECT (id, type, user_id, display_name) ON chat.members TO 'ai'@'%';
GRANT SELECT ON chat.room_members TO 'ai'@'%';
GRANT SELECT (id, name, parent_room_id, fork_seq, depth, topic, status) ON chat.rooms TO 'ai'@'%';
GRANT SELECT ON chat.room_council TO 'ai'@'%';
GRANT SELECT, INSERT, UPDATE ON chat.room_summaries TO 'ai'@'%';
GRANT SELECT, INSERT ON chat.room_memories TO 'ai'@'%';
GRANT SELECT, INSERT ON chat.user_memories TO 'ai'@'%';
GRANT SELECT, UPDATE, DELETE ON chat.knowledge_documents TO 'ai'@'%';
GRANT SELECT, INSERT, DELETE ON chat.knowledge_chunks TO 'ai'@'%';
SQL
echo "ai account is up to date"
