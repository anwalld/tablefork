#!/usr/bin/env bash
# Applies each migrations/NNN_name.sql once, in file name order, and records it
# in schema_migrations. Runs in the migrate service before the app starts.
set -euo pipefail

defaults=$(mktemp)
trap 'rm -f "$defaults"' EXIT
printf '[client]\nhost=%s\nuser=%s\npassword=%s\ndefault-character-set=utf8mb4\n' \
    "$MYSQL_HOST" "$MYSQL_USER" "$MYSQL_PASSWORD" >"$defaults"

sql() {
    mysql --defaults-extra-file="$defaults" --batch --skip-column-names "$MYSQL_DATABASE" "$@"
}

sql -e "CREATE TABLE IF NOT EXISTS schema_migrations (
            version    VARCHAR(255) NOT NULL PRIMARY KEY,
            applied_at TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
        )"

# Databases created before this runner got 001 from MySQL's init scripts.
has_users=$(sql -e "SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = DATABASE() AND table_name = 'users'")
recorded=$(sql -e "SELECT COUNT(*) FROM schema_migrations")
if [[ $has_users == 1 && $recorded == 0 ]]; then
    sql -e "INSERT INTO schema_migrations (version) VALUES ('001_init')"
fi

for file in /migrations/*.sql; do
    version=$(basename "$file" .sql)
    if [[ ! $version =~ ^[0-9]{3}_[a-z0-9_]+$ ]]; then
        echo "bad migration name: $version" >&2
        exit 1
    fi
    applied=$(sql -e "SELECT COUNT(*) FROM schema_migrations WHERE version = '$version'")
    if [[ $applied == 1 ]]; then
        continue
    fi
    echo "applying $version"
    sql <"$file"
    sql -e "INSERT INTO schema_migrations (version) VALUES ('$version')"
done
echo "schema is up to date"
