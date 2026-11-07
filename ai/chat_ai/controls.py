"""Durable generation results and control fences shared with the chat service."""

from typing import NamedTuple


class Generation(NamedTuple):
    phase: str
    content: str
    outcome: str | None
    attempt: int
    content_attempt: int
    failure_code: str

    @property
    def stopped(self):
        return self.outcome == 'stopped'


class GenerationControls:
    def __init__(self, pool):
        self.pool = pool

    async def room(self, room_id):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute("SELECT paused, ignore_through_seq FROM room_ai_controls WHERE room_id=%s", (room_id,))
            row = await cursor.fetchone()
            return (bool(row[0]), int(row[1])) if row else (False, 0)

    async def generation(self, generation_id):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "SELECT phase,content,outcome,attempt,content_attempt,failure_code "
                "FROM ai_generations WHERE generation_id=%s", (generation_id,))
            row = await cursor.fetchone()
            return Generation(*row) if row else None

    async def generations(self, generation_ids):
        results = {}
        # Bound query size while sharing one read across active rooms.
        ids = list(generation_ids)
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            for start in range(0, len(ids), 256):
                batch = ids[start:start + 256]
                placeholders = ','.join(['%s'] * len(batch))
                await cursor.execute(
                    'SELECT generation_id,phase,content,outcome,attempt,content_attempt,failure_code '
                    f'FROM ai_generations WHERE generation_id IN ({placeholders})', batch)
                for row in await cursor.fetchall():
                    results[row[0]] = Generation(*row[1:])
        return results

    async def reserve(self, room_id, job):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await connection.begin()
            try:
                await cursor.execute("INSERT IGNORE INTO room_ai_controls (room_id) VALUES (%s)", (room_id,))
                await cursor.execute("SELECT paused, ignore_through_seq FROM room_ai_controls WHERE room_id=%s FOR SHARE", (room_id,))
                control = await cursor.fetchone()
                if control[0] or (control[1] and job.get('trigger_seq', 0) <= control[1]):
                    await connection.rollback()
                    return False
                await cursor.execute(
                    "SELECT p.is_legacy,HEX(a.admission_id),a.persona_version,a.persona "
                    "FROM room_ai_personas a JOIN ai_profiles p ON p.member_id=a.member_id "
                    "WHERE a.room_id=%s AND a.member_id=%s FOR SHARE", (room_id, job['member_id']))
                admission = await cursor.fetchone()
                if not admission:
                    await connection.rollback()
                    return False
                if not job.get('admission_id') and admission[0]:
                    job['admission_id'] = admission[1]
                if admission[1] != job.get('admission_id'):
                    await connection.rollback()
                    return False
                await cursor.execute(
                    "SELECT HEX(admission_id),persona_version,persona_snapshot FROM ai_generations WHERE generation_id=%s",
                    (job['id'],))
                pinned = await cursor.fetchone()
                if pinned and pinned[0]:
                    if pinned[0] != admission[1]:
                        await connection.rollback()
                        return False
                    job['persona_version'], job['persona_snapshot'] = pinned[1], pinned[2]
                elif not admission[0]:
                    job['persona_version'], job['persona_snapshot'] = admission[2], admission[3]
                await cursor.execute(
                    "INSERT IGNORE INTO ai_generations (generation_id,room_id,member_id,payer_member_id,trigger_seq,attempt,content,admission_id,persona_version,persona_snapshot) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'',UNHEX(%s),%s,%s)",
                    (job['id'], room_id, job['member_id'], job['payer'][0], job.get('trigger_seq', 0), job['attempt'],
                     job.get('admission_id') if 'persona_snapshot' in job else None,
                     job.get('persona_version') if 'persona_snapshot' in job else None, job.get('persona_snapshot')))
                await cursor.execute(
                    "UPDATE ai_generations SET attempt=%s,content='' "
                    "WHERE generation_id=%s AND phase='generating' AND attempt<%s",
                    (job['attempt'], job['id'], job['attempt']))
                await cursor.execute("SELECT phase FROM ai_generations WHERE generation_id=%s", (job['id'],))
                row = await cursor.fetchone()
                await connection.commit()
                return row[0] == 'generating'
            except BaseException:
                await connection.rollback()
                raise

    async def snapshot(self, job, content, final=False):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE ai_generations SET content=%s,phase=%s,outcome=%s,"
                "last_nonempty_content=IF(%s,%s,last_nonempty_content),"
                "content_attempt=IF(%s,%s,content_attempt),"
                "snapshot_revision=snapshot_revision+1 "
                "WHERE generation_id=%s AND attempt=%s AND phase='generating'",
                (content, 'ready' if final else 'generating', 'completed' if final else None,
                 bool(content.strip()), content, bool(content.strip()), job['attempt'], job['id'], job['attempt']))
            return cursor.rowcount > 0

    async def failed(self, job, code='interrupted'):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE ai_generations SET phase='ready',outcome='failed',"
                "content=COALESCE(last_nonempty_content,''),failure_code=%s "
                "WHERE generation_id=%s AND attempt=%s AND phase='generating'",
                (code, job['id'], job['attempt']))
        return await self.generation(job['id'])

    async def cancelled(self, job):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE ai_generations SET phase='finished',outcome='cancelled',content='' "
                "WHERE generation_id=%s AND phase<>'finished'", (job['id'],))
