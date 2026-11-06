INSERT INTO users (id,username,password_hash) VALUES (9000001,'upgrade_fixture','unused-test-hash');
INSERT INTO members (id,type,user_id,display_name) VALUES (9000001,'human',9000001,'upgrade_fixture');
INSERT INTO rooms (id,name,last_seq) VALUES (9000001,'upgrade fixture',1);
INSERT INTO room_members (room_id,member_id,role) VALUES (9000001,9000001,'owner');
INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms)
VALUES (9000001,1,9000001,'upgrade-old-message','old history',1);
