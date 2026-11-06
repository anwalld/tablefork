"""Run through the normal TLS gateway, shared request gate and real services."""
import time
import pytest

from ai_helpers import add_ai, ai_reply, store_key


def test_attention_and_search_through_shared_gate(make_user, make_room, make_socket):
    owner, reader = make_user(), make_user()
    room = make_room(owner, reader)
    socket = make_socket(owner)
    socket.subscribe(room)
    ack = socket.post(room, f'@{reader.username} search-literal-%_')
    socket.expect('message')
    def summary():
        return next(r for r in reader.get('/api/rooms').json()['rooms'] if r['id'] == room)
    reader_socket = make_socket(reader)
    reader_socket.subscribe(room)
    state = summary()
    assert state['unread_count'] == 1 and state['mention_seq'] == ack['seq']
    result = reader.get(f'/api/rooms/{room}/search?q=%25_').json()
    assert [m['seq'] for m in result['messages']] == [ack['seq']]
    assert reader.request('PUT', f'/api/rooms/{room}/read', json={'seq': ack['seq']}).status_code == 200
    assert reader_socket.expect('read_updated')['room_id'] == room
    with pytest.raises(TimeoutError):
        socket.expect('read_updated', timeout=.2)
    assert summary()['unread_count'] == 0
    outsider = make_user()
    assert outsider.get(f'/api/rooms/{room}/search').status_code == 404


@pytest.mark.skipif(__import__('os').environ.get('AI_TESTS') != '1', reason='requires fake model')
def test_pause_stops_partial_and_resume_does_not_replay(make_user, make_room, make_socket):
    owner = make_user()
    room = make_room(owner)
    store_key(owner, 'anthropic')
    assert add_ai(owner, room, 'claude').status_code == 200
    socket = make_socket(owner)
    socket.subscribe(room)
    socket.post(room, '@claude #restart stop this reply')
    deadline = time.monotonic() + 5
    while True:
        chunk = socket.expect('ai_chunk', timeout=max(.1, deadline-time.monotonic()))
        if chunk.get('snapshot'):
            break
        assert time.monotonic() < deadline
    assert owner.request('PUT', f'/api/rooms/{room}/ai-state', json={'paused': True}).status_code == 200
    history = owner.get(f'/api/rooms/{room}/messages').json()['messages']
    partial = [m for m in history if m.get('generation_id') == chunk['generation_id']]
    assert len(partial) == 1 and partial[0]['stopped'] and partial[0]['content']
    socket.post(room, '@claude do not answer paused-message')
    time.sleep(.4)
    assert owner.request('PUT', f'/api/rooms/{room}/ai-state', json={'paused': False}).status_code == 200
    time.sleep(.4)
    history = owner.get(f'/api/rooms/{room}/messages').json()['messages']
    assert len([m for m in history if m['member']['type'] == 'ai']) == 1
    socket.post(room, '@claude answer-new-message')
    final = ai_reply(socket)
    while final.get('stopped'):
        final = ai_reply(socket)
    assert 'answer-new-message' in final['content']


@pytest.mark.skipif(__import__('os').environ.get('AI_TESTS') != '1', reason='requires fake model')
def test_concurrent_stop_and_completion_store_one_result(make_user, make_room, make_socket):
    from concurrent.futures import ThreadPoolExecutor
    from conftest import Client
    owner = make_user()
    room = make_room(owner)
    store_key(owner, 'anthropic')
    assert add_ai(owner, room, 'claude').status_code == 200
    socket = make_socket(owner)
    socket.subscribe(room)
    socket.post(room, '@claude concurrent-stop-proof')
    deadline = time.monotonic() + 5
    while True:
        chunk = socket.expect('ai_chunk', timeout=max(.1, deadline-time.monotonic()))
        if chunk.get('snapshot'):
            break
        assert time.monotonic() < deadline
    clients = [Client(), Client()]
    for client in clients:
        client.session.cookies.update(owner.session.cookies)
    path = f'/api/rooms/{room}/ai-generations/{chunk["generation_id"]}/stop'
    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda client: client.post(path).status_code, clients))
    assert statuses == [200, 200]
    messages = owner.get(f'/api/rooms/{room}/messages').json()['messages']
    result = [m for m in messages if m.get('generation_id') == chunk['generation_id']]
    assert len(result) == 1
    assert result[0]['content'].startswith(chunk['snapshot'])
