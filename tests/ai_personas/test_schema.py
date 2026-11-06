"""Real MySQL constraints, not proof of future HTTP/generation behavior."""

import json
import os
from pathlib import Path
import uuid

import pymysql
import pytest

from upgrade_fixture import connect, rows_snapshot


@pytest.fixture
def db():
    connection = connect()
    yield connection
    connection.rollback()
    connection.close()


def scalar(db, sql, args=()):
    with db.cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.fetchone()[0]


def execute(db, sql, args=()):
    with db.cursor() as cursor:
        cursor.execute(sql, args)
        return cursor.lastrowid


def rejected(db, sql, args=(), codes=(3819, 1452, 1062)):
    with pytest.raises(pymysql.MySQLError) as error:
        execute(db, sql, args)
    assert error.value.args[0] in codes, error.value.args[0]


@pytest.fixture
def context(db):
    username = 'schema_' + uuid.uuid4().hex[:16]
    owner = execute(db, 'INSERT INTO users (username,password_hash) VALUES (%s,%s)',
                    (username, 'unused-test-hash'))
    human = execute(db, "INSERT INTO members (type,user_id,display_name) VALUES ('human',%s,'测试者')", (owner,))
    room = execute(db, "INSERT INTO rooms (name) VALUES ('测试房间')")
    execute(db, "INSERT INTO room_members (room_id,member_id,role) VALUES (%s,%s,'owner')", (room, human))

    def role(name='Claude A', creator=owner, model='claude', provider='anthropic'):
        member = execute(db, "INSERT INTO members (type,display_name) VALUES ('ai',%s)", (name,))
        execute(db, 'INSERT INTO ai_profiles (member_id,slug,provider,model_slug,creator_user_id,is_legacy) '
                    'VALUES (%s,%s,%s,%s,%s,FALSE)',
                (member, f'role_{member}', provider, model, creator))
        return member

    def join(member, room_id=room, persona='严谨', token=None):
        token = token or uuid.uuid4().bytes
        execute(db, 'INSERT INTO room_members (room_id,member_id) VALUES (%s,%s)', (room_id, member))
        execute(db, 'INSERT INTO room_ai_personas (room_id,member_id,admission_id,persona) VALUES (%s,%s,%s,%s)',
                (room_id, member, token, persona))
        return token

    return owner, human, room, role, join


def test_model_directory_and_legacy_roles(db):
    with db.cursor() as cursor:
        cursor.execute('SELECT model_slug,provider FROM ai_model_types ORDER BY model_slug')
        assert cursor.fetchall() == (('claude','anthropic'),('deepseek','deepseek'),('gemini','gemini'),('gpt','openai'))
        cursor.execute('SELECT slug,model_slug,creator_user_id,is_legacy FROM ai_profiles ORDER BY slug')
        roles = cursor.fetchall()
    assert len(roles) == 4
    assert all(slug == model and creator is None and legacy == 1 for slug,model,creator,legacy in roles)


def test_legacy_upgrade_preserves_all_original_columns(db):
    if os.environ['MYSQL_DATABASE'] != 'chat_upgrade':
        pytest.skip('V6 fixture only belongs to upgrade database')
    before = json.loads((Path(os.environ['EVIDENCE_DIR'])/'v6-before-upgrade.json').read_text())
    assert rows_snapshot(db, before['columns']) == before
    assert scalar(db, 'SELECT COUNT(*) FROM room_ai_personas') == 3
    assert scalar(db, "SELECT COUNT(DISTINCT admission_id) FROM room_ai_personas WHERE persona='' AND persona_version=1") == 3
    assert scalar(db, 'SELECT COUNT(*) FROM ai_generations WHERE admission_id IS NULL AND persona_version IS NULL AND persona_snapshot IS NULL') == 2
    assert scalar(db, 'SELECT COUNT(*) FROM ai_member_requests WHERE persona IS NULL') == 1


def test_same_owner_same_name_same_model_gets_distinct_roles(db, context):
    owner, _, _, role, _ = context
    first, second = role(), role()
    assert first != second
    assert scalar(db, "SELECT COUNT(*) FROM ai_profiles p JOIN members m ON m.id=p.member_id "
                      "WHERE p.creator_user_id=%s AND p.model_slug='claude' AND m.display_name='Claude A'", (owner,)) == 2


def test_private_profile_needs_real_creator_and_matching_provider(db, context):
    _, _, _, _, _ = context
    for creator, legacy, slug, provider in [(None,0,'private_a','anthropic'), (999999999,0,'private_b','anthropic'),
                                             (None,0,'private_c','openai')]:
        member = execute(db, "INSERT INTO members (type,display_name) VALUES ('ai','invalid')")
        rejected(db, 'INSERT INTO ai_profiles (member_id,slug,provider,model_slug,creator_user_id,is_legacy) '
                     "VALUES (%s,%s,%s,'claude',%s,%s)", (member,slug,provider,creator,legacy))
    owner = context[0]
    member = execute(db, "INSERT INTO members (type,display_name) VALUES ('ai','wrong provider')")
    rejected(db, 'INSERT INTO ai_profiles (member_id,slug,provider,model_slug,creator_user_id,is_legacy) '
                 "VALUES (%s,'wrong_provider','openai','claude',%s,FALSE)", (member,owner))
    rejected(db, "UPDATE ai_profiles SET creator_user_id=%s WHERE slug='claude'", (owner,))


def test_room_personas_are_independent_and_membership_is_required(db, context):
    _, human, room, role, join = context
    member = role()
    first = join(member)
    other = execute(db, "INSERT INTO rooms (name) VALUES ('另一个房间')")
    second = join(member, other, '活泼')
    assert first != second
    execute(db, "UPDATE room_ai_personas SET persona='更严谨',persona_version=persona_version+1 WHERE room_id=%s AND member_id=%s", (room,member))
    assert scalar(db, 'SELECT persona FROM room_ai_personas WHERE room_id=%s AND member_id=%s', (other,member)) == '活泼'
    assert scalar(db, 'SELECT persona_version FROM room_ai_personas WHERE room_id=%s AND member_id=%s', (room,member)) == 2
    another = role()
    rejected(db, "INSERT INTO room_ai_personas (room_id,member_id,admission_id,persona) VALUES (%s,%s,%s,'')", (room,another,uuid.uuid4().bytes))
    rejected(db, "INSERT INTO room_ai_personas (room_id,member_id,admission_id,persona) VALUES (%s,%s,%s,'')", (room,human,uuid.uuid4().bytes))
    rejected(db, 'UPDATE room_ai_personas SET admission_id=%s WHERE room_id=%s AND member_id=%s', (first,other,member))


@pytest.mark.parametrize('text,accepted', [('a'*16384,True),('a'*16385,False),('你'*5461,True),('你'*5462,False)])
def test_persona_byte_budget_in_room_request_and_generation(db, context, text, accepted):
    _, human, room, role, join = context
    member = role()
    token = join(member)
    queries = [
        ('UPDATE room_ai_personas SET persona=%s WHERE room_id=%s AND member_id=%s', (text,room,member)),
        ('INSERT INTO ai_member_requests (room_id,member_id,requested_by,persona) VALUES (%s,%s,%s,%s)', (room,member,human,text)),
        ('INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,admission_id,persona_version,persona_snapshot) '
         "VALUES (%s,%s,%s,%s,'',%s,1,%s)", (uuid.uuid4().hex,room,member,human,token,text)),
    ]
    for sql, args in queries:
        if accepted: execute(db, sql, args)
        else: rejected(db, sql, args, codes=(3819,))


def test_version_and_generation_snapshot_must_be_complete(db, context):
    _, human, room, role, join = context
    member=role(); token=join(member)
    rejected(db, 'UPDATE room_ai_personas SET persona_version=0 WHERE room_id=%s AND member_id=%s', (room,member), codes=(3819,))
    rejected(db, 'UPDATE room_ai_personas SET admission_id=%s WHERE room_id=%s AND member_id=%s', (bytes(16),room,member), codes=(3819,))
    for admission, version, persona in [(token,None,None),(None,1,''),(token,0,''),(token,1,None),(bytes(16),1,'')]:
        rejected(db, 'INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,admission_id,persona_version,persona_snapshot) '
                     "VALUES (%s,%s,%s,%s,'',%s,%s,%s)",
                 (uuid.uuid4().hex,room,member,human,admission,version,persona), codes=(3819,))
    execute(db, 'INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,admission_id,persona_version,persona_snapshot) '
                "VALUES ('full-snapshot',%s,%s,%s,'',%s,1,'')", (room,member,human,token))


def test_leaving_deletes_config_but_keeps_history_and_generation_snapshot(db, context):
    _, human, room, role, join = context
    member = role(); old_token = join(member)
    execute(db, 'INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,admission_id,persona_version,persona_snapshot) '
                "VALUES ('retired-admission',%s,%s,%s,'old partial',%s,1,'严谨')", (room,member,human,old_token))
    execute(db, 'INSERT INTO messages (room_id,seq,member_id,client_msg_id,content,created_at_ms) '
                "VALUES (%s,1,%s,'old-final','保留历史',1)", (room,member))
    execute(db, 'DELETE FROM room_members WHERE room_id=%s AND member_id=%s', (room,member))
    assert scalar(db, 'SELECT COUNT(*) FROM room_ai_personas WHERE room_id=%s AND member_id=%s', (room,member)) == 0
    assert scalar(db, 'SELECT COUNT(*) FROM messages WHERE room_id=%s', (room,)) == 1
    assert scalar(db, "SELECT admission_id FROM ai_generations WHERE generation_id='retired-admission'") == old_token
    new_token = join(member, persona='新入群人设')
    assert new_token != old_token
    assert scalar(db, "SELECT persona_snapshot FROM ai_generations WHERE generation_id='retired-admission'") == '严谨'
    # This proves storage preserves the old fence; action rejection is a later task.


def test_v6_writer_sql_shapes_still_work(db, context):
    _, human, room, _, _ = context
    legacy = scalar(db, "SELECT member_id FROM ai_profiles WHERE slug='gpt'")
    execute(db, 'INSERT INTO room_members (room_id,member_id) VALUES (%s,%s)', (room,legacy))
    execute(db, 'INSERT IGNORE INTO ai_member_requests (room_id,member_id,requested_by) VALUES (%s,%s,%s)', (room,legacy,human))
    execute(db, 'INSERT IGNORE INTO ai_generations (generation_id,room_id,member_id,payer_member_id,trigger_seq,attempt,content) '
                "VALUES ('v6-writer',%s,%s,%s,1,1,'')", (room,legacy,human))
    execute(db, "UPDATE ai_generations SET content='partial',status='stopped',stopped=TRUE WHERE generation_id='v6-writer'")
    assert scalar(db, "SELECT stopped FROM ai_generations WHERE generation_id='v6-writer'") == 1
    assert scalar(db, "SELECT persona_snapshot IS NULL FROM ai_generations WHERE generation_id='v6-writer'") == 1


def test_flyway_has_seven_valid_versions(db):
    with db.cursor() as cursor:
        cursor.execute('SELECT version,success FROM flyway_schema_history WHERE version IS NOT NULL ORDER BY installed_rank')
        assert cursor.fetchall() == tuple((str(i),1) for i in range(1,8))
