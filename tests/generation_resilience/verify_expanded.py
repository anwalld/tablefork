"""V8 must preserve each old value before V9 retires the old fields."""
import json
import os
from pathlib import Path
from upgrade_fixture import canonical, connect

snapshot = json.loads(Path(os.environ['EVIDENCE_DIR'], 'v7-old-columns.json').read_text())
with connect() as db, db.cursor() as cursor:
    for table, order in [('ai_generations','generation_id'),('messages','room_id,seq')]:
        columns = ','.join('`'+column+'`' for column in snapshot[table]['columns'])
        cursor.execute(f'SELECT {columns} FROM {table} ORDER BY {order}')
        assert canonical(cursor.fetchall()) == snapshot[table]['rows'], table
print('V8 preserves every V7 generation and message column value')
