import importlib.util
from pathlib import Path
import threading
import socket
import gc
import unittest

spec=importlib.util.spec_from_file_location('coordinator',Path(__file__).parents[2]/'scripts/request-buffer-coordinator.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class Route:
    def __init__(self): self.promotions=0; self.disabled=False; self.current='redis-1'
    def master(self): return self.current
    def ready(self,host): return host==self.current and not self.disabled
    def disable(self): self.disabled=True
    def failover(self): self.promotions+=1

class Backends:
    def __init__(self):
        self.missing=set(); self.grants=[]; self.delay=None
        self.queue=dict(epoch='',revision=0,incarnation='ram-boot')
        self.states={h:dict(boot=h+'-boot',epoch='',revision=0,queue_ready=True) for h in ['A','B']}
    def post(self,host,action,body):
        if host in self.missing: raise ConnectionError('control link unavailable')
        state=self.states[host]
        if action=='status': return {**state,'queue':self.queue.copy()}
        if action=='queue': self.queue.update(epoch=body['epoch'],revision=body['revision']); return dict(ok=True)
        if action in ['fence','pause']:
            state.update(epoch=body['epoch'],revision=body['revision']); return dict(ok=True)
        if action=='challenge':
            if self.delay:
                self.delay[0].set(); self.delay[1].wait(3)
            return dict(ok=True,nonce='unique-challenge')
        if action=='grant': self.grants.append((host,body)); return dict(ok=True)
        raise AssertionError(action)
    def healthy(self,host): return host not in self.missing

class CoordinatorTests(unittest.TestCase):
    def create(self):
        self.now=0
        def clock(): return self.now
        def sleep(seconds): self.now+=seconds
        self.route=Route(); self.backends=Backends()
        return module.Coordinator(self.route,self.backends,['A','B'],clock=clock,sleep=sleep)
    def test_missing_manual_ack_never_promotes_and_only_resumes_verified_original_route(self):
        coordinator=self.create(); self.backends.missing={'B'}
        outcome=coordinator.controlled('redis-1')
        self.assertIn('cancelled',outcome)
        self.assertEqual(self.route.promotions,0)
        self.assertEqual(set(coordinator.active),{'A'})
        self.assertGreaterEqual(self.now,5)
    def test_manual_all_ack_disables_route_before_starting_promotion(self):
        coordinator=self.create()
        self.assertEqual(coordinator.controlled('redis-1'),'started')
        self.assertTrue(self.route.disabled)
        self.assertEqual(self.route.promotions,1)
        self.assertEqual(coordinator.pending_switch,'redis-1')
        self.assertFalse(coordinator.active)
    def test_automatic_resume_waits_old_permit_and_uses_only_healthy_backend(self):
        coordinator=self.create(); self.backends.missing={'B'}
        self.assertFalse(coordinator.pause())
        self.assertTrue(coordinator.resume('redis-1'))
        self.assertGreaterEqual(self.now,5)
        self.assertEqual(set(coordinator.active),{'A'})
    def test_delayed_challenge_cannot_emit_grant_after_phase_changes(self):
        coordinator=self.create(); coordinator.pause(); coordinator.resume('redis-1')
        entered,released=threading.Event(),threading.Event()
        self.backends.delay=(entered,released)
        thread=threading.Thread(target=coordinator.renew_once,args=('A',))
        thread.start(); self.assertTrue(entered.wait(1))
        coordinator.revoke()
        released.set(); thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.backends.grants,[])
    def test_changed_original_route_prevents_cancel_reopening(self):
        coordinator=self.create(); self.backends.missing={'B'}; self.route.current='redis-2'
        coordinator.controlled('redis-1')
        self.assertEqual(self.route.promotions,0)
        self.assertFalse(coordinator.active)
    def test_queue_redis_incarnation_change_forces_new_barrier(self):
        coordinator=self.create(); coordinator.pause(); coordinator.resume('redis-1')
        self.backends.queue['incarnation']='after-restart'
        self.assertFalse(coordinator.refresh())
    def test_coordinator_freeze_cannot_renew_without_fresh_route_verification(self):
        coordinator=self.create(); coordinator.pause(); coordinator.resume('redis-1')
        self.now+=6
        coordinator.renew_once('A')
        self.assertEqual(self.backends.grants,[])
    def test_stopped_old_primary_is_not_reopened_while_sentinel_is_still_voting(self):
        coordinator=self.create()
        calls=[]
        counter=[0]
        def master():
            counter[0]+=1
            if counter[0]>=4: coordinator.stop.set()
            return 'redis-1'
        self.route.master=master
        self.route.primary=lambda host: counter[0]>=3
        self.route.point=lambda host: (calls.append(counter[0]),setattr(self.route,'disabled',False))
        coordinator.run()
        self.assertEqual(calls,[3])
    def test_redis_control_connection_closes_without_waiting_for_gc(self):
        listener=socket.socket()
        listener.bind(('127.0.0.1',0)); listener.listen(1)
        closed=threading.Event()
        def peer():
            connection,_=listener.accept()
            with connection:
                connection.settimeout(1)
                with connection.makefile('rb') as stream:
                    for reply in [b'+OK\r\n',b'+PONG\r\n']:
                        count=int(stream.readline()[1:])
                        for _ in range(count):
                            size=int(stream.readline()[1:]); stream.read(size); stream.read(2)
                        connection.sendall(reply)
                    if connection.recv(1)==b'': closed.set()
        thread=threading.Thread(target=peer,daemon=True); thread.start()
        was_enabled=gc.isenabled(); gc.disable()
        try:
            self.assertEqual(module.redis_command('127.0.0.1',listener.getsockname()[1],'probe-not-secret','PING'),'PONG')
            self.assertTrue(closed.wait(.5),'control socket remained open until cyclic GC')
        finally:
            if was_enabled: gc.enable()
            gc.collect(); listener.close(); thread.join(1)
    def test_new_coordinator_does_not_reuse_previous_epoch(self):
        first=self.create(); first.pause(); first.resume('redis-1')
        restarted=module.Coordinator(self.route,self.backends,['A','B'],clock=lambda:self.now,sleep=lambda seconds:setattr(self,'now',self.now+seconds))
        restarted.pause(); restarted.resume('redis-1')
        self.assertNotEqual(first.epoch,restarted.epoch)
        self.assertGreaterEqual(self.now,10)
        self.assertTrue(all(value['epoch']==restarted.epoch for value in restarted.active.values()))

if __name__=='__main__': unittest.main(verbosity=2)
