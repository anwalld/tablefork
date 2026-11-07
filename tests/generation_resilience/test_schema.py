"""Fresh and upgraded MySQL must enforce the generation result boundary."""
import os
import uuid

import pymysql
import pytest


@pytest.fixture
def db():
    connection = pymysql.connect(host=os.getenv('MYSQL_HOST','mysql'), user='chat',
        password=os.environ['MYSQL_PASSWORD'], database=os.environ['MYSQL_DATABASE'], autocommit=True)
    yield connection
    connection.close()


def test_upgrade_result_mapping(db):
    with db.cursor() as cursor:
        cursor.execute("SELECT generation_id,phase,outcome,content,content_attempt FROM ai_generations ORDER BY generation_id")
        rows = cursor.fetchall()
    if os.environ['MYSQL_DATABASE'] == 'chat_fresh':
        assert rows == ()
    else:
        assert rows == (
            ('ready','ready','completed','complete',2),
            ('running','generating',None,'first partial',2),
            ('saved-stop','finished','stopped','stop saved',2),
            ('saved-success','finished','completed','complete saved',2),
            ('stopped','ready','stopped','stopped text',2),
            ('unknown','finished','legacy_unknown','',0))


def test_finished_message_metadata_and_removed_fields(db):
    with db.cursor() as cursor:
        cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() "
                       "AND table_name='ai_generations' AND column_name IN ('status','stopped')")
        assert cursor.fetchall() == ()
        cursor.execute('SELECT seq,generation_outcome FROM messages ORDER BY room_id,seq')
        expected = () if os.environ['MYSQL_DATABASE']=='chat_fresh' else ((1,'completed'),(2,'stopped'))
        assert cursor.fetchall() == expected
        cursor.execute('SELECT version,success FROM flyway_schema_history WHERE version IS NOT NULL ORDER BY installed_rank')
        assert cursor.fetchall() == tuple((str(i),1) for i in range(1,11))


@pytest.mark.parametrize('phase,outcome,accepted', [
    ('generating',None,True), ('generating','failed',False),
    ('ready',None,False), ('finished',None,False),
    ('ready','failed',True), ('finished','cancelled',True),
    ('finished','legacy_unknown',True)])
def test_phase_outcome_constraint(db,phase,outcome,accepted):
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO rooms (name) VALUES ('constraint test')")
        room=cursor.lastrowid
        try:
            query=('INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,phase,outcome) '
                   'VALUES (%s,%s,1,1,\'\',%s,%s)')
            values=(uuid.uuid4().hex,room,phase,outcome)
            if accepted:
                cursor.execute(query,values)
            else:
                with pytest.raises(pymysql.err.OperationalError) as error:
                    cursor.execute(query,values)
                assert error.value.args[0]==3819
        finally:
            cursor.execute('DELETE FROM rooms WHERE id=%s',(room,))


@pytest.mark.parametrize('keyword', ['量子纠缠','你好','中文','CASE','case','量','空 格','标点!','%_','😀','a-the',''])
def test_ngram_candidates_equal_literal_query(db, keyword):
    texts = ['稀有的量子纠缠实验','量子与纠缠分开','你好中文世界','不同的中文消息',
             'Hello CASE case','空 格与标点!','字面%_符号😀','a-the other', '普通背景']
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO rooms (name) VALUES ('search corpus')")
        room=cursor.lastrowid
        cursor.execute("SELECT member_id FROM ai_profiles WHERE slug='claude'")
        member=cursor.fetchone()[0]
        try:
            cursor.executemany('INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms) VALUES (%s,%s,%s,%s,%s,%s)',
                [(room,index,member,str(index),text,index*100) for index,text in enumerate(texts,1)])
            exact='SELECT seq,content FROM messages WHERE room_id=%s AND LOCATE(%s,content)>0 AND seq<%s ORDER BY seq DESC LIMIT 2'
            candidate='SELECT seq,content FROM messages WHERE room_id=%s AND LOCATE(%s,content)>0 AND seq<%s AND MATCH(content) AGAINST (%s IN BOOLEAN MODE) ORDER BY seq DESC LIMIT 2'
            eligible=len(keyword)>=2 and all('\u4e00'<=char<='\u9fff' for char in keyword)
            before=999
            while True:
                cursor.execute(exact,(room,keyword,before))
                baseline=cursor.fetchall()
                if eligible:
                    cursor.execute(candidate,(room,keyword,before,'"'+keyword+'"'))
                    assert cursor.fetchall()==baseline
                if not baseline: break
                before=baseline[-1][0]
        finally:
            cursor.execute('DELETE FROM rooms WHERE id=%s',(room,))


def test_fixed_corpus_search_plan_and_latency(db):
    import json
    import statistics
    import time
    from pathlib import Path
    corpus_size=12000
    with db.cursor() as cursor:
        cursor.execute("INSERT INTO rooms (name) VALUES ('search benchmark')")
        room=cursor.lastrowid
        cursor.execute("SELECT member_id FROM ai_profiles WHERE slug='claude'")
        member=cursor.fetchone()[0]
        try:
            cursor.executemany('INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms) VALUES (%s,%s,%s,%s,%s,%s)',
                [(room,seq,member,str(seq),('量子纠缠实验' if seq%997==0 else '普通聊天消息')+' background '+str(seq),seq)
                 for seq in range(1,corpus_size+1)])
            base='SELECT seq FROM messages WHERE room_id=%s AND LOCATE(%s,content)>0 '
            suffix='ORDER BY seq DESC LIMIT 30'
            indexed=base+'AND MATCH(content) AGAINST (%s IN BOOLEAN MODE) '+suffix
            exact=base+suffix
            measurements={}
            for name,sql,values in [('exact',exact,(room,'量子纠缠')),('ngram',indexed,(room,'量子纠缠','"量子纠缠"'))]:
                cursor.execute('EXPLAIN FORMAT=JSON '+sql,values)
                plan=json.loads(cursor.fetchone()[0])
                durations=[]
                results=[]
                for _ in range(15):
                    started=time.perf_counter()
                    cursor.execute(sql,values)
                    results=cursor.fetchall()
                    durations.append((time.perf_counter()-started)*1000)
                measurements[name]={'p50_ms':statistics.median(durations), 'max_ms':max(durations),
                    'plan':plan,'results':results}
            assert measurements['ngram']['results']==measurements['exact']['results']
            assert len(measurements['exact']['results'])==12
            assert 'fulltext' in json.dumps(measurements['ngram']['plan']).lower()
            # Refresh InnoDB stats so data/index sizes reflect the 12k-row corpus
            # and the ngram fulltext index, instead of the pre-load estimates.
            cursor.execute("ANALYZE TABLE messages")
            cursor.execute("SELECT data_length,index_length FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='messages'")
            size=cursor.fetchone()
            evidence=Path(os.environ['EVIDENCE_DIR'])
            (evidence/('search-benchmark-'+os.environ['MYSQL_DATABASE']+'.json')).write_text(
                json.dumps({'corpus_size':corpus_size,'table_bytes':size,'measurements':measurements},ensure_ascii=False,indent=2))
        finally:
            cursor.execute('DELETE FROM rooms WHERE id=%s',(room,))
