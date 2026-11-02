-- The shell migration runner used before Flyway kept its own history table.
DROP TABLE IF EXISTS schema_migrations;
