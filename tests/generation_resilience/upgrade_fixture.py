"""Seed V7 generations, recording every old column for the V8 check."""
import json
import os
from pathlib import Path

import pymysql


def connect():
    return pymysql.connect(host=os.getenv('MYSQL_HOST', 'mysql'), user='chat',
                           password=os.environ['MYSQL_PASSWORD'],
                           database=os.environ['MYSQL_DATABASE'], autocommit=True)


def canonical(rows):
    return json.loads(json.dumps(rows, default=lambda value: value.hex() if isinstance(value, bytes) else str(value)))


if __name__ == '__main__':
    with connect() as db, db.cursor() as cursor:
        cursor.execute("INSERT INTO rooms (id,name,last_seq) VALUES (5001,'generation upgrade',2)")
        cursor.execute("SELECT member_id FROM ai_profiles WHERE slug='claude'")
        member = cursor.fetchone()[0]
        cursor.execute('INSERT INTO room_members (room_id,member_id) VALUES (5001,%s)', (member,))
        for generation, status, stopped, content in [
            ('running','generating',False,'first partial'), ('ready','final',False,'complete'),
            ('stopped','stopped',True,'stopped text'), ('unknown','saved',False,''),
            ('saved-success','saved',False,'complete saved'), ('saved-stop','saved',True,'stop saved')]:
            cursor.execute('INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,attempt,content,status,stopped) '
                           'VALUES (%s,5001,%s,1,2,%s,%s,%s)', (generation,member,content,status,stopped))
        for seq, generation, stopped in [(1,'saved-success',False),(2,'saved-stop',True)]:
            cursor.execute('INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms,stopped) '
                           'SELECT room_id,%s,member_id,generation_id,content,1,%s FROM ai_generations WHERE generation_id=%s',
                           (seq,stopped,generation))
        snapshot = {}
        for table, order in [('ai_generations','generation_id'),('messages','room_id,seq')]:
            cursor.execute(f'SELECT * FROM {table} ORDER BY {order}')
            snapshot[table] = {'columns':[column[0] for column in cursor.description], 'rows':canonical(cursor.fetchall())}
        Path(os.environ['EVIDENCE_DIR'], 'v7-old-columns.json').write_text(json.dumps(snapshot,ensure_ascii=False))
