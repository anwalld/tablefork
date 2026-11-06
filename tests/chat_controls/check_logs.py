"""Validate allowlisted structured records from an actual service run."""
import json
import sys
from pathlib import Path

records = []
raw = '\n'.join(Path(name).read_text() for name in sys.argv[1:])
for line in raw.splitlines():
    start = line.find('{')
    if start < 0:
        continue
    try:
        item = json.loads(line[start:])
    except json.JSONDecodeError:
        continue
    if item.get('event') in ('request_stage', 'gateway_request'):
        records.append(item)
assert records, 'no structured records found'
stages = {item.get('stage') for item in records}
assert {'http', 'mysql', 'redis', 'message_store'} <= stages, stages
allowed = {'event', 'request_id', 'instance', 'operation', 'stage', 'result',
           'elapsed_ms', 'timestamp_ms', 'room_id', 'generation_id', 'attempt',
           'method', 'status', 'elapsed_s', 'upstream_connect_s',
           'upstream_response_s', 'upstream'}
for item in records:
    assert set(item) <= allowed, set(item) - allowed
    if 'elapsed_ms' in item:
        assert item['elapsed_ms'] >= 0
structured = json.dumps(records)
for secret in ['correct-horse-battery', 'fake-plaintext-marker-7f3a', 'browser-search-needle', 'partial reply']:
    assert secret not in structured, 'sensitive payload leaked'
print(json.dumps({'records': len(records), 'stages': sorted(str(stage) for stage in stages)}))
