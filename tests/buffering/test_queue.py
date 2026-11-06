"""Real Redis script tests, no Python dependencies or business data volumes.
Run with QUEUE_TEST_PORT set by run_queue_tests.sh; destructive only to a
random queue namespace. RESP codec is deliberately local to this probe.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import socket
import time
import unittest
import uuid

SCRIPT = (Path(__file__).parents[2] / 'src/buffering/lua/queue.lua').read_text()

class Redis:
    def __init__(self):
        self.sock = socket.create_connection(('127.0.0.1', int(os.environ['QUEUE_TEST_PORT'])), 3)
        self.sock.settimeout(3)
        self.file = self.sock.makefile('rb')
    def close(self):
        self.file.close()
        self.sock.close()
    def command(self, *parts):
        parts = [str(p).encode() for p in parts]
        self.sock.sendall(b'*%d\r\n' % len(parts) + b''.join(b'$%d\r\n' % len(p) + p + b'\r\n' for p in parts))
        return self.read()
    def read(self):
        line = self.file.readline()
        if not line:
            raise ConnectionError('Redis disconnected; command outcome is unknown')
        kind, text = line[:1], line[1:-2]
        if kind == b'+': return text.decode()
        if kind == b'-': raise RuntimeError(text.decode())
        if kind == b':': return int(text)
        size = int(text)
        if size == -1: return None
        if kind == b'$':
            data = self.file.read(size)
            if self.file.read(2) != b'\r\n': raise ConnectionError('short response')
            return data.decode()
        if kind == b'*': return [self.read() for _ in range(size)]
        raise RuntimeError('invalid RESP reply')

class QueueTests(unittest.TestCase):
    def setUp(self):
        self.redis = Redis()
        self.ns = 'queue-test:' + uuid.uuid4().hex
        self.cfg = dict(max_waiting=1024,max_body_bytes=16*1024*1024,max_memory=64*1024*1024,
                        max_result_bytes=512*1024,wait_ms=45000,result_ms=300000,claim_ms=30000)
        self.init()
    def tearDown(self):
        for key in self.redis.command('KEYS', self.ns + ':*'):
            self.redis.command('DEL', key)
        self.redis.close()
    def op(self, action, client=None, **kwargs):
        return json.loads((client or self.redis).command('EVAL', SCRIPT, 1, self.ns, action, json.dumps(kwargs)))
    def init(self, **overrides):
        self.cfg.update(overrides)
        self.redis.command('DEL', self.ns + ':meta')
        return self.op('init', incarnation='boot', config=self.cfg)
    def admit(self, id='task-1', body_bytes=10, payload='encrypted-payload', **overrides):
        args=dict(id=id, owner='owner-hash', binding='request-hash', payload=payload, body_bytes=body_bytes)
        args.update(overrides)
        return self.op('admit', **args)
    def resume(self):
        return self.op('control', expected_epoch='', epoch='epoch-1', revision=1, mode='draining')
    def claim(self, worker='A', token='claim-a'):
        return self.op('claim', epoch='epoch-1', worker=worker, claim=token)
    def query(self, id='task-1', **kwargs):
        return self.op('query', id=id, owner=kwargs.get('owner','owner-hash'))
    def test_count_and_bytes_preserve_older_tasks(self):
        self.init(max_waiting=2, max_body_bytes=25)
        self.assertEqual(self.admit()['state'], 'waiting')
        self.assertEqual(self.admit('task-2', 15)['state'], 'waiting')
        self.assertEqual(self.admit('task-3')['error'], 'queue_full')
        self.assertEqual(self.query()['state'], 'waiting')
        self.assertEqual(self.op('status')['body_bytes'], 25)
    def test_body_limit_independent_of_count(self):
        self.init(max_body_bytes=15)
        self.admit()
        self.assertEqual(self.admit('task-2')['error'], 'queue_full')
        self.assertEqual(self.op('status')['waiting'], 1)
    def test_uncertain_admission_retry_reuses_task_and_binding(self):
        self.admit()
        self.assertEqual(self.admit(payload='different-random-encryption')['id'], 'task-1')
        self.assertEqual(self.op('status')['waiting'], 1)
        self.assertEqual(self.admit(binding='other-parameters')['error'], 'request_conflict')
        self.assertEqual(self.admit(owner='other-owner')['error'], 'forbidden')
    def test_query_requires_capability_and_does_not_execute(self):
        self.admit()
        self.assertEqual(self.query(owner='guess')['error'], 'forbidden')
        self.assertEqual(self.query()['state'], 'waiting')
        self.assertEqual(self.query('lost')['error'], 'result_unknown')
    def test_claim_reports_actual_queue_wait(self):
        self.admit()
        time.sleep(.03)
        self.resume()
        claimed = self.claim()
        self.assertGreaterEqual(claimed['wait_ms'], 20)

    def test_fifo_and_multi_backend_claims_are_exclusive(self):
        for i in range(20): self.admit(f'task-{i}')
        self.resume()
        first = self.claim()
        self.assertEqual(first['id'], 'task-0')
        def claim(i):
            client=Redis()
            try: return self.op('claim', client, epoch='epoch-1',worker=f'w{i}',claim=f'c{i}')
            finally: client.close()
        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results=list(pool.map(claim, range(25)))
        ids=[r['id'] for r in results if 'id' in r]
        self.assertEqual(len(ids), 19)
        self.assertEqual(len(set(ids)), 19)
        self.assertNotIn(first['id'], ids)
    def test_recovery_arrivals_queue_until_all_work_finished(self):
        self.admit(); self.resume(); claim=self.claim()
        self.assertEqual(self.admit('task-2')['state'],'waiting')
        self.assertEqual(self.op('status')['mode'],'draining')
        self.op('finish',id=claim['id'],worker='A',claim='claim-a',state='succeeded',result='ciphertext')
        self.assertEqual(self.query()['result'],'ciphertext')
        claim=self.claim(token='claim-b')
        self.op('finish',id=claim['id'],worker='A',claim='claim-b',state='failed',result='encrypted-error')
        self.assertEqual(self.op('status')['mode'],'direct')
        self.assertTrue(self.admit('task-3')['direct'])
    def test_expiry_releases_bytes_and_never_executes(self):
        self.init(wait_ms=40)
        self.admit()
        time.sleep(.08)
        self.resume()
        self.assertEqual(self.query()['state'],'expired')
        self.assertTrue(self.claim()['empty'])
        self.assertEqual(self.op('status')['body_bytes'],0)
    def test_expiry_sweep_bounded_does_not_execute_tail(self):
        self.init(wait_ms=120)
        for i in range(260): self.admit(f'task-{i}')
        time.sleep(.15)
        self.resume()
        self.assertTrue(self.claim()['empty'])
        self.assertEqual(self.op('status')['waiting'],0)
    def test_lost_worker_is_unknown_and_stale_finish_fenced(self):
        self.init(claim_ms=40)
        self.admit(); self.resume(); self.claim()
        time.sleep(.08)
        self.assertEqual(self.query()['state'],'unknown')
        self.assertEqual(self.op('finish',id='task-1',worker='A',claim='claim-a',state='succeeded',result='late')['error'],'lost_claim')
    def test_safe_takeover_uses_new_claim_and_retains_original_payload(self):
        self.init(claim_ms=40)
        self.admit(recoverable=True); self.resume(); self.claim()
        time.sleep(.08)
        self.assertEqual(self.query()['state'],'recovering')
        second=self.claim(worker='B',token='new-claim')
        self.assertEqual(second['payload'],'encrypted-payload')
        self.assertEqual(self.op('finish',id='task-1',worker='A',claim='claim-a',state='succeeded',result='late')['error'],'lost_claim')
        self.op('finish',id='task-1',worker='B',claim='new-claim',state='succeeded',result='recovered-sql-result')
        self.assertEqual(self.query()['result'],'recovered-sql-result')
    def test_safe_unknown_result_is_recovered_without_losing_payload(self):
        self.admit(recoverable=True); self.resume(); self.claim()
        self.op('finish',id='task-1',worker='A',claim='claim-a',state='unknown',result='ambiguous-503')
        self.assertEqual(self.query()['state'],'recovering')
        claimed=self.claim(worker='B',token='b')
        self.assertEqual(claimed['payload'],'encrypted-payload')
        self.op('finish',id='task-1',worker='B',claim='b',state='succeeded',result='same-committed-result')
        self.assertEqual(self.query()['result'],'same-committed-result')
    def test_result_budget_failure_does_not_claim_success(self):
        self.init(max_result_bytes=4)
        self.admit(); self.resume(); self.claim()
        self.op('finish',id='task-1',worker='A',claim='claim-a',state='succeeded',result='large-result')
        self.assertEqual(self.query()['state'],'unknown')
    def test_memory_budget_includes_ciphertext_not_only_body(self):
        self.init(max_memory=2200)
        self.assertEqual(self.admit(payload='X'*200)['error'],'queue_full')
        self.admit(payload='X'*100)
        self.assertEqual(self.op('status')['memory'],2148)
        self.assertEqual(self.admit('task-2',payload='X')['error'],'queue_full')
    def test_old_control_cannot_undo_new_pause(self):
        self.admit(); self.resume()
        self.assertTrue(self.op('control',expected_epoch='epoch-1',epoch='epoch-1',revision=2,mode='paused')['ok'])
        self.assertEqual(self.op('control',expected_epoch='epoch-1',epoch='epoch-1',revision=1,mode='draining')['error'],'stale_control')
        self.assertTrue(self.claim()['empty'])
    def test_result_cache_expires_to_unknown_and_releases_budget(self):
        self.init(result_ms=40)
        self.admit(); self.resume(); self.claim()
        self.op('finish',id='task-1',worker='A',claim='claim-a',state='succeeded',result='ciphertext')
        time.sleep(.08)
        self.assertEqual(self.query()['error'],'result_unknown')
        self.assertEqual(self.op('status')['memory'],0)
    def test_restart_state_missing_is_not_business_failure(self):
        self.admit()
        # This models the dedicated pure-RAM Redis losing every key, not a
        # fictitious backend-local queue. Other namespaces are untouched.
        for key in self.redis.command('KEYS',self.ns+':*'): self.redis.command('DEL',key)
        self.assertEqual(self.query()['error'],'queue_restarted')
        self.op('init',incarnation='new-boot',config=self.cfg)
        self.assertEqual(self.query()['error'],'result_unknown')
    def test_redis_persistence_and_eviction_configuration(self):
        self.assertEqual(self.redis.command('CONFIG','GET','save')[1], '')
        self.assertEqual(self.redis.command('CONFIG','GET','appendonly')[1], 'no')
        self.assertEqual(self.redis.command('CONFIG','GET','maxmemory-policy')[1], 'noeviction')

if __name__ == '__main__': unittest.main(verbosity=2)
