"""Opt-in host-orchestrated faults; ordinary pytest never pauses services."""
import json
import os
from pathlib import Path
import time
import uuid

import pytest
import requests
from redis import RedisError

from conftest import Client
from test_failover import node, proxy_routes_to

pytestmark=pytest.mark.skipif(os.environ.get('BUFFER_CONTROL_REAL')!='1',reason='requires isolated buffering host fault driver')
FLAGS=Path('/fault-control')

def control(instance,action='status',body=None):
    response=requests.post(f'http://app-{instance}:8000/internal/buffer/{action}',json=body or {},
        headers={'Authorization':'Bearer '+os.environ['BUFFER_CONTROL_TOKEN']},timeout=3)
    assert response.status_code==200,response.text
    return response.json()

def wait_for(predicate,seconds=30):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        try:
            value=predicate()
            if value: return value
        except (requests.RequestException,OSError,RedisError): pass
        time.sleep(.1)
    raise AssertionError('fault condition did not converge within bounded deadline')

def signal(name):
    (FLAGS/name).touch()
    wait_for(lambda:(FLAGS/(name+'.done')).exists(),30)
    path=FLAGS/(name+'.json')
    return json.loads(path.read_text()) if path.exists() else None

def active(instance): return control(instance)['mode']!='paused'

def test_real_control_partition_cancel_failover_freeze_and_restart(make_user,make_room,make_socket,db):
    user=make_user(); room=make_room(user)
    a=control(1); b=control(2)
    assert a['epoch']==b['epoch'] and active(1) and active(2)
    sentinel=node('sentinel-1',26379)
    before_master=sentinel.sentinel_get_master_addr_by_name('chat')[0]
    before_epoch=sentinel.sentinel_master('chat')['config-epoch']
    FLAGS.joinpath('blocked').touch()
    try:
        wait_for(lambda:control(2)['mode']=='paused',10)
        assert active(1),'healthy A must remain usable when B control is lost'
        bclient=user.at_instance(2)
        before=time.monotonic()
        admission=bclient.session.post(bclient.base_url+'/api/rooms',json={'name':'partition-queued'},timeout=5)
        assert admission.status_code==202,admission.text
        assert time.monotonic()-before<5
        result=bclient.task_result(admission.json()['task_id'])
        assert result.status_code==201,result.text
        print('real control partition: B lease expired, A executed B admission',flush=True)
        outcome=signal('manual-switch')
        assert outcome['exit']!=0 and 'cancelled' in outcome['output'],outcome
        assert sentinel.sentinel_get_master_addr_by_name('chat')[0]==before_master
        assert sentinel.sentinel_master('chat')['config-epoch']==before_epoch
        print('real manual switch: missing B pause ACK cancelled before Sentinel promotion',flush=True)
        # Diagnostic metadata only, never session/device token values.
        import hashlib
        store=node('redis-proxy')
        try:
            session_cookies=[c for c in user.session.cookies if c.name=='chat_session']
            ttls=[store.ttl('session:'+hashlib.sha256(c.value.encode()).hexdigest().upper()) for c in session_cookies]
            check=bclient.get('/api/auth/me')
            print('pre-WS auth diagnostics: session_cookie_count='+str(len(session_cookies))+' ttls='+str(ttls)+' B_me_status='+str(check.status_code),flush=True)
            assert check.status_code==200,check.text
        finally: store.close()
        writer=make_socket(user,instance=2)
        assert writer.subscribe(room)['type']=='subscribed'
        mid=writer.send_message(room,'queued through expired backend')
        frame=writer.expect('queued','ack','error',timeout=5)
        assert frame['type']=='queued',frame
        receipt=user.task_result(frame['task_id'])
        assert receipt.status_code==200,receipt.text
        ack=receipt.json(); assert ack['type']=='ack' and ack['client_msg_id']==mid
        with db.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM messages WHERE room_id=%s AND client_msg_id=%s',(room,mid))
            assert cursor.fetchone()[0]==1
        print('real WS admission: queued is distinct from durable ACK; one stored message',flush=True)
        started=time.monotonic()
        signal('stop-'+before_master)
        promoted='redis-2' if before_master=='redis-1' else 'redis-1'
        wait_for(lambda:sentinel.sentinel_get_master_addr_by_name('chat')[0]==promoted and proxy_routes_to(promoted) and active(1),30)
        response=user.get('/api/auth/me')
        assert response.status_code==200,response.text
        assert time.monotonic()-started<30
        assert control(2)['mode']=='paused'
        print(f'real automatic failure: healthy A recovered in {time.monotonic()-started:.3f}s while B control stayed blocked',flush=True)
        signal('start-'+before_master)
        old=node(before_master)
        try: wait_for(lambda:old.info('replication')['role']=='slave',30)
        finally: old.close()
    finally:
        FLAGS.joinpath('blocked').unlink(missing_ok=True)
        sentinel.close()
    wait_for(lambda:active(1) and active(2),30)
    b=control(2)
    challenge=control(2,'challenge',dict(boot=b['boot'],epoch=b['epoch'],revision=b['revision']))
    old_grant=dict(boot=b['boot'],epoch=b['epoch'],revision=b['revision'],nonce=challenge['nonce'],mode='direct')
    FLAGS.joinpath('blocked').touch()
    try:
        signal('pause-b')
        time.sleep(6)
        signal('unpause-b')
        assert control(2)['mode']=='paused'
        assert control(2,'grant',old_grant)['ok'] is False
        direct=user.at_instance(2)
        admission=direct.session.get(direct.base_url+'/api/auth/me',timeout=5)
        assert admission.status_code==202,admission.text
        assert direct.task_result(admission.json()['task_id']).status_code==200
        print('real frozen backend: expired grant rejected after unpause; requests stayed queued',flush=True)
    finally: FLAGS.joinpath('blocked').unlink(missing_ok=True)
    wait_for(lambda:active(1) and active(2),30)
    before=control(1); started=time.monotonic()
    outcome=signal('restart-proxy')
    assert outcome['same_container'] and outcome['same_ip'],outcome
    wait_for(lambda:active(1) and active(2) and control(1)['epoch']!=before['epoch'],30)
    assert time.monotonic()-started<30
    assert user.get('/api/auth/me').status_code==200
    print(f'real coordinator restart: new epoch, same proxy container/IP, recovered in {time.monotonic()-started:.3f}s',flush=True)
