-- A dedicated empty stopword list makes the candidate contract independent
-- from the server's default stopwords. Keep it empty for this index's lifetime.
CREATE TABLE message_search_stopwords (
    value VARCHAR(30) NOT NULL
) ENGINE=InnoDB;
SET @message_stopword_table = CONCAT(DATABASE(), '/message_search_stopwords');
SET SESSION innodb_ft_user_stopword_table = @message_stopword_table;
ALTER TABLE messages ADD FULLTEXT KEY ft_messages_content (content) WITH PARSER ngram;
