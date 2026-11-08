-- Room knowledge base. Documents and their chunks live here; vectors live in
-- the pgvector container, keyed by chunk id. The AI service picks up pending
-- documents, chunks and embeds them, and removes vectors of deleted ones.
CREATE TABLE knowledge_documents (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    room_id            BIGINT UNSIGNED NOT NULL,
    uploader_member_id BIGINT UNSIGNED NOT NULL,
    uploader_user_id   BIGINT UNSIGNED NOT NULL,
    name               VARCHAR(255) NOT NULL,
    kind               ENUM('text','markdown','log') NOT NULL,
    size_bytes         INT UNSIGNED NOT NULL,
    content            MEDIUMTEXT NOT NULL,
    -- pending: waiting for the AI service; ready: searchable; not_ready: the
    -- uploader has no embedding key; failed: chunking/embedding failed;
    -- deleted: hidden at once, vectors removed by the AI service, then the row.
    status             ENUM('pending','ready','not_ready','failed','deleted') NOT NULL DEFAULT 'pending',
    status_reason      VARCHAR(255) NULL,
    chunk_count        INT UNSIGNED NOT NULL DEFAULT 0,
    created_at_ms      BIGINT NOT NULL,
    KEY idx_knowledge_documents_room (room_id, status),
    KEY idx_knowledge_documents_status (status),
    CONSTRAINT fk_knowledge_documents_room FOREIGN KEY (room_id) REFERENCES rooms (id) ON DELETE CASCADE
);

CREATE TABLE knowledge_chunks (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    document_id BIGINT UNSIGNED NOT NULL,
    room_id     BIGINT UNSIGNED NOT NULL,
    idx         INT UNSIGNED NOT NULL,
    location    VARCHAR(255) NOT NULL,
    content     TEXT NOT NULL,
    KEY idx_knowledge_chunks_document (document_id, idx),
    CONSTRAINT fk_knowledge_chunks_document FOREIGN KEY (document_id)
        REFERENCES knowledge_documents (id) ON DELETE CASCADE
);
