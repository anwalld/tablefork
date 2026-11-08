import os
import uuid

import pytest

from ai_helpers import members, store_key, ai_reply


def create(client, name='Claude A', request_id=None):
    result = client.request('POST', '/api/ai/roles', json={'model_slug':'claude','name':name},
                            headers={'X-Request-Id':request_id or uuid.uuid4().hex})
    assert result.status_code == 201, result.text
    return result.json()


def invite(client, room, role, persona='严谨'):
    return client.post(f'/api/rooms/{room}/ai-members', json={'member_id':role['id'],'persona':persona})


def test_duplicate_names_private_catalog_and_request_dedup(make_user, db):
    owner, other = make_user(), make_user()
    request_id=uuid.uuid4().hex
    first=create(owner,request_id=request_id)
    assert create(owner,request_id=request_id)==first
    second=create(owner)
    assert second['id'] != first['id']
    roles=owner.get('/api/ai/roles').json()['agents']
    assert {r['id'] for r in roles}=={first['id'],second['id']}
    assert all(r['model_slug']=='claude' and r['name']=='Claude A' for r in roles)
    assert other.get('/api/ai/roles').json()['agents']==[]


def test_only_creator_can_invite_even_when_other_is_owner(make_user,make_room):
    owner, guest=make_user(),make_user()
    room=make_room(owner,guest)
    role=create(guest)
    store_key(owner,'anthropic')
    result=invite(owner,room,role)
    assert result.status_code==404 and result.json()['error']['code']=='ai_not_found'
    assert not [m for m in members(owner,room) if m['type']=='ai']


def test_other_rooms_private_id_is_not_exposed_by_remove_or_bad_invite(make_user,make_room):
    owner,other=make_user(),make_user();room=make_room(owner);role=create(other)
    response=owner.request('DELETE',f"/api/rooms/{room}/ai-members/{role['id']}")
    assert response.status_code==404 and response.json()['error']['code']=='ai_not_found'
    response=owner.post(f'/api/rooms/{room}/ai-members',json={'member_id':'invalid','persona':''})
    assert response.status_code==400


def test_invalid_or_expired_structured_ai_target_rejects_before_message_save(make_user,make_room,make_socket):
    owner,other=make_user(),make_user();room=make_room(owner);role=create(other)
    socket=make_socket(owner);socket.subscribe(room)
    socket.send(type='send',room_id=room,client_msg_id=uuid.uuid4().hex,content='@Claude A hello',mention_ids=[role['id']])
    error=socket.expect('error')
    assert error['error']['code']=='invalid_argument'
    assert owner.get(f'/api/rooms/{room}/messages').json()['messages']==[]


def test_pending_persona_approval_and_creator_only_edit(make_user,make_room):
    owner,guest=make_user(),make_user()
    room=make_room(owner,guest)
    role=create(guest);store_key(guest,'anthropic')
    assert invite(guest,room,role,'学习助手').json()['status']=='pending'
    assert invite(guest,room,role,'学习助手').json()['status']=='pending'
    assert invite(guest,room,role,'偷偷替换').status_code==409
    assert owner.post(f"/api/rooms/{room}/ai-requests/{role['id']}/approve").status_code==200
    entry=next(m for m in members(guest,room) if m['id']==role['id'])
    assert entry['persona']=='学习助手' and entry['persona_version']==1
    path=f"/api/rooms/{room}/ais/{role['id']}/persona"
    assert owner.request('PUT',path,json={'persona':'越权','persona_version':1}).status_code==403
    assert guest.request('PUT',path,json={'persona':'新版本','persona_version':1}).status_code==200
    assert guest.request('PUT',path,json={'persona':'过期页面','persona_version':1}).status_code==409


def test_same_role_two_rooms_do_not_share_persona(make_user,make_room):
    owner=make_user();first,second=make_room(owner),make_room(owner)
    store_key(owner,'anthropic');role=create(owner)
    assert invite(owner,first,role,'严谨').status_code==200
    assert invite(owner,second,role,'活泼').status_code==200
    path=f"/api/rooms/{first}/ais/{role['id']}/persona"
    assert owner.request('PUT',path,json={'persona':'更严谨','persona_version':1}).status_code==200
    a=next(m for m in members(owner,first) if m['id']==role['id'])
    b=next(m for m in members(owner,second) if m['id']==role['id'])
    assert (a['persona'],a['persona_version'])==('更严谨',2)
    assert (b['persona'],b['persona_version'])==('活泼',1)


@pytest.mark.parametrize('removal',['leave','kick','ban'])
def test_creator_membership_loss_removes_owned_roles_and_pending(make_user,make_room,db,removal):
    owner,guest=make_user(),make_user();room=make_room(owner,guest)
    store_key(guest,'anthropic');role=create(guest)
    invite(guest,room,role)
    assert owner.post(f"/api/rooms/{room}/ai-requests/{role['id']}/approve").status_code==200
    pending=create(guest,name='pending');invite(guest,room,pending)
    if removal=='leave': result=guest.post(f'/api/rooms/{room}/leave',json={'confirm_delete':False})
    elif removal=='kick': result=owner.request('DELETE',f'/api/rooms/{room}/members/{guest.username}')
    else: result=owner.request('PUT',f'/api/rooms/{room}/bans/{guest.username}')
    assert result.status_code==200,result.text
    assert not [m for m in members(owner,room) if m['type']=='ai']
    assert owner.get(f'/api/rooms/{room}/ai-requests').json()['requests']==[]
    assert len(guest.get('/api/ai/roles').json()['agents'])==2


def test_old_public_bot_new_invitation_is_rejected(make_user,make_room):
    owner=make_user();room=make_room(owner);store_key(owner,'anthropic')
    result=owner.post(f'/api/rooms/{room}/ai-members',json={'slug':'claude','persona':''})
    assert result.status_code==404


def test_exit_and_approval_concurrently_cannot_leave_orphan_ai(make_user,make_room):
    from concurrent.futures import ThreadPoolExecutor
    owner,guest=make_user(),make_user();room=make_room(owner,guest)
    store_key(guest,'anthropic');role=create(guest)
    assert invite(guest,room,role).json()['status']=='pending'
    with ThreadPoolExecutor(max_workers=2) as executor:
        approve=executor.submit(owner.post,f"/api/rooms/{room}/ai-requests/{role['id']}/approve")
        leave=executor.submit(guest.post,f'/api/rooms/{room}/leave',json={'confirm_delete':False})
        assert leave.result().status_code==200
        assert approve.result().status_code in (200,404)
    assert not [m for m in members(owner,room) if m['type']=='ai']
    assert owner.get(f'/api/rooms/{room}/ai-requests').json()['requests']==[]


@pytest.mark.skipif(not os.environ.get('AI_TESTS'),reason='requires fake model stack')
def test_legacy_room_bot_still_replies_with_original_identity(make_user,make_room,make_socket,db):
    owner=make_user();room=make_room(owner);store_key(owner,'anthropic')
    with db.cursor() as cursor:
        cursor.execute("SELECT member_id FROM ai_profiles WHERE slug='claude' AND is_legacy=TRUE")
        member=cursor.fetchone()[0]
        cursor.execute('INSERT INTO room_members (room_id,member_id) VALUES (%s,%s)',(room,member))
        cursor.execute("INSERT INTO room_ai_personas (room_id,member_id,admission_id,persona) VALUES (%s,%s,%s,'')",(room,member,uuid.uuid4().bytes))
    socket=make_socket(owner);socket.subscribe(room)
    socket.post(room,'legacy compatibility')
    reply=ai_reply(socket)
    assert reply['member']['id']==member and reply['content'].startswith('Claude:')
    assert next(m for m in members(owner,room) if m['id']==member)['is_legacy']


@pytest.mark.skipif(not os.environ.get('AI_TESTS'),reason='requires fake model stack')
def test_sql_persona_snapshot_survives_edit_during_generation(make_user,make_room,make_socket,db):
    owner=make_user();room=make_room(owner);store_key(owner,'anthropic');role=create(owner)
    assert invite(owner,room,role,'old pinned persona').status_code==200
    socket=make_socket(owner);socket.subscribe(room)
    socket.post(room,'#restart hold while editing')
    while True:
        chunk=socket.expect('ai_chunk')
        if chunk.get('snapshot'): break
    assert owner.request('PUT',f"/api/rooms/{room}/ais/{role['id']}/persona",json={'persona':'new future persona','persona_version':1}).status_code==200
    first=ai_reply(socket)
    with db.cursor() as cursor:
        cursor.execute('SELECT persona_version,persona_snapshot FROM ai_generations WHERE generation_id=%s',(first['generation_id'],))
        assert cursor.fetchone()==(1,'old pinned persona')
    socket.post(room,'new round')
    second=ai_reply(socket)
    with db.cursor() as cursor:
        cursor.execute('SELECT persona_version,persona_snapshot FROM ai_generations WHERE generation_id=%s',(second['generation_id'],))
        assert cursor.fetchone()==(2,'new future persona')


@pytest.mark.skipif(not os.environ.get('AI_TESTS'),reason='requires fake model stack')
def test_same_named_roles_only_selected_member_replies(make_user,make_room,make_socket):
    owner,guest=make_user(),make_user();room=make_room(owner,guest)
    store_key(owner,'anthropic');store_key(guest,'anthropic')
    first,second=create(owner),create(owner)
    assert invite(owner,room,first).status_code==200
    assert invite(owner,room,second).status_code==200
    socket=make_socket(guest);socket.subscribe(room)
    message_id=uuid.uuid4().hex
    socket.send(type='send',room_id=room,client_msg_id=message_id,content='@Claude A hello',mention_ids=[second['id']])
    assert socket.expect('ack')['client_msg_id']==message_id
    reply=ai_reply(socket)
    assert reply['member']['id']==second['id']
    with pytest.raises(TimeoutError): socket.expect('message',timeout=.5)


def test_saved_message_retry_keeps_receipt_after_mentioned_role_leaves(make_user, make_room, make_socket):
    owner = make_user()
    room = make_room(owner)
    role = create(owner)
    store_key(owner, "anthropic")
    assert invite(owner, room, role).status_code == 200
    socket = make_socket(owner)
    socket.subscribe(room)
    message_id = uuid.uuid4().hex
    payload = dict(type='send', room_id=room, client_msg_id=message_id,
                   content='@Claude A receipt test', mention_ids=[role['id']])
    socket.send(**payload)
    first = socket.expect('ack')
    assert owner.request('DELETE', f"/api/rooms/{room}/ai-members/{role['id']}").status_code == 200
    socket.send(**payload)
    duplicate = socket.expect('ack')
    assert duplicate['duplicate'] is True and duplicate['seq'] == first['seq']
    payload['client_msg_id'] = uuid.uuid4().hex
    socket.send(**payload)
    assert socket.expect('error')['error']['code'] == 'invalid_argument'


def test_role_default_persona_is_private_and_editable_by_its_creator(make_user, db):
    owner, other = make_user(), make_user()
    created = owner.request('POST', '/api/ai/roles',
                            json={'model_slug':'claude','name':'Claude M','persona':'# 角色\n- 说话简洁'},
                            headers={'X-Request-Id':uuid.uuid4().hex})
    assert created.status_code == 201, created.text
    role = created.json()
    assert role['persona'] == '# 角色\n- 说话简洁'
    listed = {r['id']: r for r in owner.get('/api/ai/roles').json()['agents']}
    assert listed[role['id']]['persona'] == '# 角色\n- 说话简洁'
    assert create(owner, name='Plain')['persona'] == ''

    path = f"/api/ai/roles/{role['id']}/persona"
    changed = owner.request('PUT', path, json={'persona':'## 新的人设'}, headers={'X-Request-Id':uuid.uuid4().hex})
    assert changed.status_code == 200, changed.text
    assert {r['id']: r for r in owner.get('/api/ai/roles').json()['agents']}[role['id']]['persona'] == '## 新的人设'

    denied = other.request('PUT', path, json={'persona':'x'}, headers={'X-Request-Id':uuid.uuid4().hex})
    assert denied.status_code == 403
    too_long = owner.request('PUT', path, json={'persona':'字' * 6000}, headers={'X-Request-Id':uuid.uuid4().hex})
    assert too_long.status_code == 400
    with db.cursor() as cursor:
        cursor.execute('SELECT default_persona FROM ai_profiles WHERE member_id=%s', (role['id'],))
        assert cursor.fetchone()[0] == '## 新的人设'
