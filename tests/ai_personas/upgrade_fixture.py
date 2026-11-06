"""V6-only fixture and column-aware snapshot; run in a disposable database."""

import json
import os
from pathlib import Path

import pymysql


def connect():
    return pymysql.connect(
        host=os.environ['MYSQL_HOST'], user='chat',
        password=os.environ['MYSQL_PASSWORD'],
        database=os.environ['MYSQL_DATABASE'], charset='utf8mb4',
    )


def rows_snapshot(connection, columns=None):
    with connection.cursor() as cursor:
        if columns is None:
            cursor.execute('SHOW TABLES')
            tables = [r[0] for r in cursor.fetchall() if r[0] != 'flyway_schema_history']
            columns = {}
            for table in tables:
                cursor.execute(f'SHOW COLUMNS FROM `{table}`')
                columns[table] = [r[0] for r in cursor.fetchall()]
        data = {}
        for table, names in columns.items():
            # Table and column names come from schema metadata, never requests.
            selected = ','.join(f'`{name}`' for name in names)
            cursor.execute(f'SELECT {selected} FROM `{table}`')
            data[table] = sorted(
                json.dumps(row, default=str, ensure_ascii=False) for row in cursor.fetchall()
            )
    return {'columns': columns, 'data': data}


def prepare():
    connection = connect()
    try:
        with connection.cursor() as cursor:
            cursor.executemany(
                'INSERT INTO users (id,username,password_hash) VALUES (%s,%s,%s)',
                [(10001, 'schema_creator', 'unused-test-hash'),
                 (10002, 'schema_peer', 'unused-test-hash')],
            )
            cursor.execute("INSERT INTO members (id,type,user_id,display_name) VALUES "
                           "(10001,'human',10001,'创建者'),(10002,'human',10002,'同伴')")
            cursor.execute("INSERT INTO rooms (id,name,last_seq) VALUES "
                           "(5001,'学习群',2),(5002,'闲聊群',1)")
            cursor.execute("INSERT INTO room_members (room_id,member_id,role,read_seq) VALUES "
                           "(5001,10001,'owner',1),(5001,10002,'member',0),(5002,10001,'owner',0)")
            cursor.execute("INSERT INTO room_members (room_id,member_id) "
                           "SELECT r.id,p.member_id FROM rooms r JOIN ai_profiles p "
                           "WHERE p.slug='claude' OR (r.id=5001 AND p.slug='deepseek')")
            cursor.execute("INSERT INTO ai_member_requests (room_id,member_id,requested_by) "
                           "SELECT 5001,member_id,10001 FROM ai_profiles WHERE slug='gpt'")
            cursor.execute("INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms) "
                           "VALUES (5001,1,10001,'old-human','升级前人类消息',1)")
            cursor.execute("INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms,stopped) "
                           "SELECT 5001,2,member_id,'old-stopped','升级前停止片段',2,TRUE "
                           "FROM ai_profiles WHERE slug='claude'")
            cursor.execute("INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms) "
                           "SELECT 5002,1,member_id,'old-final','另一房间的旧回复',3 "
                           "FROM ai_profiles WHERE slug='claude'")
            cursor.execute("INSERT INTO ai_generations "
                           "(generation_id,room_id,member_id,payer_member_id,trigger_seq,attempt,content,status,stopped) "
                           "SELECT 'legacy-active',5001,member_id,10001,1,2,'未完成快照','generating',FALSE "
                           "FROM ai_profiles WHERE slug='claude'")
            cursor.execute("INSERT INTO ai_generations "
                           "(generation_id,room_id,member_id,payer_member_id,trigger_seq,attempt,content,status,stopped) "
                           "SELECT 'legacy-stopped',5001,member_id,10001,1,1,'升级前停止片段','saved',TRUE "
                           "FROM ai_profiles WHERE slug='claude'")
            cursor.execute('INSERT INTO room_ai_controls (room_id,paused,ignore_through_seq) VALUES (5001,TRUE,2)')
            cursor.execute('INSERT INTO message_mentions (room_id,seq,member_id) VALUES (5001,1,10002)')
        connection.commit()
        snapshot = rows_snapshot(connection)
        path = Path(os.environ['EVIDENCE_DIR']) / 'v6-before-upgrade.json'
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
        print('V6 fixture and original-column snapshot saved')
    finally:
        connection.close()


if __name__ == '__main__':
    prepare()
