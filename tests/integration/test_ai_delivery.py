"""Exercise Final redelivery through the real Python -> gRPC -> C++ path."""
import json
import os
import time
import uuid

import pytest

from ai_helpers import add_ai, ai_reply, members, store_key

pytestmark = pytest.mark.skipif(not os.environ.get('AI_TESTS'), reason='needs the AI test stack')


def wait_for(predicate):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(.1)
    raise AssertionError('delivery did not complete')


def room_state(redis, room_id):
    raw = redis.get(f'ai:room:{room_id}')
    return json.loads(raw) if raw else None


def queue_final(redis, room_id, member_id, generation_id, content, admission_id=None):
    state = wait_for(lambda: room_state(redis, room_id))
    assert state.get('active') is None
    ai = next((a for a in state.get('ais', []) if a[0] == member_id), None)
    admission_id = admission_id or (ai[5] if ai and len(ai)>5 else '')
    state['active'] = {
        'id': generation_id, 'slug': ai[1] if ai else f'role_{member_id}', 'member_id': member_id, 'admission_id': admission_id,
        'payer': [0, 0], 'kind': 'reply', 'epoch': state['epoch'], 'hop': 1, 'slot': 0,
        'attempt': 1, 'status': 'final', 'owner': 'delivery-test',
        'lease_until': 0, 'retry_at': 0, 'content': content,
    }
    with redis.pipeline(transaction=True) as transaction:
        transaction.set(f'ai:room:{room_id}', json.dumps(state))
        transaction.sadd('ai:work', room_id)
        transaction.execute()


def test_repeated_final_is_stored_once_and_confirmed(make_user, make_room, make_socket, redis):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    store_key(owner, 'anthropic')
    add_ai(owner, room_id, 'claude')
    socket = make_socket(owner)
    socket.subscribe(room_id)
    socket.post(room_id, '@Claude final replay')
    final = ai_reply(socket)
    wait_for(lambda: room_state(redis, room_id) and room_state(redis, room_id).get('active') is None)
    queue_final(redis, room_id, final['member']['id'], final['generation_id'], final['content'])
    wait_for(lambda: room_state(redis, room_id).get('active') is None)
    history = owner.get(f'/api/rooms/{room_id}/messages').json()['messages']
    assert len([m for m in history if m.get('generation_id') == final['generation_id']]) == 1


def test_final_after_ai_removed_is_rejected_and_task_finishes(make_user, make_room, redis):
    owner, guest = make_user(), make_user()
    room_id = make_room(owner, guest)
    store_key(owner, 'anthropic')
    add_ai(owner, room_id, 'claude')
    ai = next(m for m in members(owner, room_id) if m['type'] == 'ai')
    wait_for(lambda: room_state(redis, room_id))
    old_admission=wait_for(lambda: room_state(redis,room_id))['ais'][0][5]
    assert owner.request('DELETE', f"/api/rooms/{room_id}/ai-members/{ai['id']}").status_code == 200
    wait_for(lambda: not room_state(redis, room_id)['ais'])
    generation_id = uuid.uuid4().hex
    queue_final(redis, room_id, ai['id'], generation_id, 'late reply', old_admission)
    wait_for(lambda: room_state(redis, room_id).get('active') is None)
    history = owner.get(f'/api/rooms/{room_id}/messages').json()['messages']
    assert all(m.get('generation_id') != generation_id for m in history)
