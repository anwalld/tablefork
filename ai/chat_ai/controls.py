"""Durable generation fences shared with the chat service's stop transaction."""


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
            await cursor.execute("SELECT status, content, stopped FROM ai_generations WHERE generation_id=%s", (generation_id,))
            return await cursor.fetchone()

    async def reserve(self, room_id, job):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await connection.begin()
            try:
                # A control row exists before any generation can start. Pause
                # locks the same row, so registration cannot pass its cutoff.
                await cursor.execute("INSERT IGNORE INTO room_ai_controls (room_id) VALUES (%s)", (room_id,))
                await cursor.execute("SELECT paused, ignore_through_seq FROM room_ai_controls WHERE room_id=%s FOR SHARE", (room_id,))
                control = await cursor.fetchone()
                if control[0] or (control[1] and job.get('trigger_seq', 0) <= control[1]):
                    await connection.rollback()
                    return False
                await cursor.execute(
                    "INSERT IGNORE INTO ai_generations (generation_id,room_id,member_id,payer_member_id,trigger_seq,attempt,content) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'')",
                    (job['id'], room_id, job['member_id'], job['payer'][0], job.get('trigger_seq', 0), job['attempt']))
                await cursor.execute(
                    "UPDATE ai_generations SET attempt=%s, content='' WHERE generation_id=%s AND status='generating' AND attempt<%s",
                    (job['attempt'], job['id'], job['attempt']))
                await cursor.execute("SELECT status FROM ai_generations WHERE generation_id=%s", (job['id'],))
                row = await cursor.fetchone()
                await connection.commit()
                return row[0] == 'generating'
            except BaseException:
                await connection.rollback()
                raise

    async def snapshot(self, job, content, final=False):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE ai_generations SET content=%s, status=%s "
                "WHERE generation_id=%s AND attempt=%s AND status='generating'",
                (content, 'final' if final else 'generating', job['id'], job['attempt']))
            return cursor.rowcount > 0

    async def failed(self, job):
        async with self.pool.acquire() as connection, connection.cursor() as cursor:
            await cursor.execute(
                "UPDATE ai_generations SET status='saved', content='' "
                "WHERE generation_id=%s AND attempt=%s AND status IN ('generating','final')",
                (job['id'], job['attempt']))
