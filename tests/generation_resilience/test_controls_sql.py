"""Generation state transitions against real disposable MySQL."""
import asyncio
import os
import uuid

import aiomysql
import pytest

from chat_ai.controls import GenerationControls


@pytest.fixture
async def state():
    pool=await aiomysql.create_pool(host=os.getenv('MYSQL_HOST','mysql'),user='chat',
        password=os.environ['MYSQL_PASSWORD'],db=os.environ['MYSQL_DATABASE'],autocommit=True,charset='utf8mb4')
    async with pool.acquire() as connection,connection.cursor() as cursor:
        await cursor.execute("INSERT INTO rooms (name) VALUES ('generation SQL transition')")
        room=cursor.lastrowid
        await cursor.execute("SELECT member_id FROM ai_profiles WHERE slug='claude'")
        member=(await cursor.fetchone())[0]
        job={'id':uuid.uuid4().hex,'attempt':1}
        await cursor.execute("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,attempt,content) VALUES (%s,%s,%s,1,1,'')",(job['id'],room,member))
    yield pool,GenerationControls(pool),job
    async with pool.acquire() as connection,connection.cursor() as cursor:
        await cursor.execute('DELETE FROM rooms WHERE id=%s',(room,))
    pool.close()
    await pool.wait_closed()


async def test_last_nonempty_survives_empty_attempt_and_old_snapshot(state):
    pool,controls,job=state
    assert await controls.snapshot(job,'first text')
    async with pool.acquire() as connection,connection.cursor() as cursor:
        await cursor.execute("UPDATE ai_generations SET attempt=2,content='' WHERE generation_id=%s",(job['id'],))
    assert not await controls.snapshot(job,'stale owner')
    second={**job,'attempt':2}
    assert await controls.snapshot(second,' \n\t')
    final=await controls.failed(second,'provider_error')
    assert (final.phase,final.outcome,final.content,final.content_attempt)==('ready','failed','first text',1)
    assert not await controls.snapshot(second,'late success',final=True)
    assert (await controls.failed(second)).content=='first text'


async def test_snapshot_and_failure_race_has_one_durable_result(state):
    _,controls,job=state
    written,result=await asyncio.gather(controls.snapshot(job,'racing partial'),controls.failed(job))
    final=await controls.generation(job['id'])
    assert final.outcome=='failed' and final.phase=='ready'
    assert final.content==('racing partial' if written else '')
    assert not await controls.snapshot(job,'post-failure')
    assert (await controls.generations([job['id']]))[job['id']]==final


async def test_stop_and_finished_cancellation_fence_old_writes(state):
    pool,controls,job=state
    assert await controls.snapshot(job,'preserved')
    async with pool.acquire() as connection,connection.cursor() as cursor:
        await cursor.execute("UPDATE ai_generations SET phase='ready',outcome='stopped' WHERE generation_id=%s",(job['id'],))
    assert (await controls.failed(job)).outcome=='stopped'
    assert not await controls.snapshot(job,'late completed',final=True)
    await controls.cancelled(job)
    cancelled=await controls.generation(job['id'])
    assert (cancelled.phase,cancelled.outcome,cancelled.content)==('finished','cancelled','')
    assert (await controls.failed(job)).outcome=='cancelled'


async def test_failed_cache_entries_refill_context_from_sql(state):
    import json
    import fakeredis
    from chat_ai.context import recent_messages, to_chat

    pool,_,job=state
    async with pool.acquire() as connection,connection.cursor() as cursor:
        await cursor.execute('SELECT room_id,member_id FROM ai_generations WHERE generation_id=%s',(job['id'],))
        room,member=await cursor.fetchone()
        await cursor.executemany('INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms,generation_outcome) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s)',[(room,seq,member,str(seq),text,seq,outcome)
            for seq,text,outcome in [(1,'usable one','completed'),(2,'usable two','completed'),
                                      (3,'failed residue','failed'),(4,'','failed')]])
    cache=fakeredis.FakeAsyncRedis(decode_responses=True)
    try:
        for seq,text in [(3,'failed residue'),(4,'')]:
            entry={'member':{'id':member,'name':'Claude','type':'ai'},'content':text,'seq':seq}
            await cache.zadd(f'room:{room}:recent',{json.dumps(entry):seq})
        # Old AI cache entries have no outcome and must be resolved from SQL.
        entries=await recent_messages(cache,pool,room,2)
        assert [entry['content'] for entry in entries]==['usable one','usable two']
        assert [message.content for message in to_chat(entries,member,2,1000)]==['usable one','usable two']
        await cache.delete(f'room:{room}:recent')
        assert await recent_messages(cache,pool,room,2)==entries
    finally:
        await cache.aclose()


async def test_success_and_failure_cannot_overwrite_the_winner(state):
    _,controls,job=state
    completed,_=await asyncio.gather(controls.snapshot(job,'complete',final=True),controls.failed(job))
    result=await controls.generation(job['id'])
    assert result.phase=='ready'
    assert result.outcome==('completed' if completed else 'failed')
    assert result.content==('complete' if completed else '')
    assert not await controls.snapshot(job,'late overwrite',final=True)
    assert (await controls.failed(job)).outcome==result.outcome
