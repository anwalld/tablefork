"""Two real C++ instances, MySQL and independent queue/business Redis.
Internal HTTP deliberately tests the service protocol, not TLS trust/browser UI.
"""
import json
import os
import secrets
import threading
import time
import unittest
from pathlib import Path
import pymysql
import concurrent.futures
import requests
import redis

A='http://app-1:8000'; B='http://app-2:8000'
TOKEN=os.environ['BUFFER_CONTROL_TOKEN']
OWNER=secrets.token_hex(32)
class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.epoch=secrets.token_hex(16); cls.revision=0; cls.queue_revision=0
        cls.boots={}; cls.active=False; cls.active_urls=[A,B]; cls.stop=threading.Event(); cls.lock=threading.RLock()
        for url in [A,B]:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                try:
                    info=cls.ctrl(url,'status',{})
                    if info.get('queue_ready'): cls.boots[url]=info['boot']; break
                except Exception: pass
                time.sleep(.1)
            else: raise AssertionError('C++ control did not start')
            cls.ctrl(url,'fence',dict(boot=cls.boots[url],expected_epoch=info['epoch'],epoch=cls.epoch,revision=1))
        cls.revision=1
        def renew():
            while not cls.stop.wait(.3):
                with cls.lock:
                    if not cls.active: continue
                    for url in cls.active_urls:
                        try: cls.grant(url)
                        except requests.RequestException: pass
        cls.thread=threading.Thread(target=renew,daemon=True); cls.thread.start()
    @classmethod
    def tearDownClass(cls): cls.stop.set(); cls.thread.join(3)
    @classmethod
    def ctrl(cls,url,action,body):
        response=requests.post(url+'/internal/buffer/'+action,json=body,headers={'Authorization':'Bearer '+TOKEN},timeout=3)
        response.raise_for_status(); return response.json()
    @classmethod
    def grant(cls,url):
        body=dict(boot=cls.boots[url],epoch=cls.epoch,revision=cls.revision)
        challenge=cls.ctrl(url,'challenge',body)
        if not challenge.get('ok'): raise AssertionError('challenge refused')
        result=cls.ctrl(url,'grant',{**body,'nonce':challenge['nonce'],'mode':'draining'})
        if not result.get('ok'): raise AssertionError('grant refused')
    @classmethod
    def pause(cls):
        with cls.lock:
            cls.active=False; cls.revision+=1; cls.queue_revision+=1
            for url in [A,B]:
                assert cls.ctrl(url,'pause',dict(boot=cls.boots[url],epoch=cls.epoch,revision=cls.revision))['ok']
            state=cls.ctrl(A,'status',{})['queue']
            cls.queue_revision=state['revision']+1
            assert cls.ctrl(A,'queue',dict(boot=cls.boots[A],epoch=cls.epoch,expected_epoch=state['epoch'],revision=cls.queue_revision,paused=True))['ok']
    @classmethod
    def resume(cls,urls=None):
        urls=urls or [A,B]
        with cls.lock:
            state=cls.ctrl(urls[0],'status',{})['queue']; cls.queue_revision=state['revision']+1
            assert cls.ctrl(urls[0],'queue',dict(boot=cls.boots[urls[0]],epoch=cls.epoch,expected_epoch=state['epoch'],revision=cls.queue_revision,paused=False))['ok']
            cls.active_urls=urls
            for url in urls: cls.grant(url)
            cls.active=True
    def call(self,session,method,path,url=A,request_id=None,**kwargs):
        return session.request(method,url+path,headers={'X-Queue-Owner':OWNER,'X-Request-Id':request_id or secrets.token_hex(16)},timeout=5,**kwargs)
    def wait(self,task,session=None,url=B,owner=OWNER,deadline=10):
        session=session or requests.Session(); until=time.monotonic()+deadline
        while time.monotonic()<until:
            response=session.get(url+'/api/tasks/'+task,headers={'X-Queue-Owner':owner},timeout=3)
            self.assertEqual(response.status_code,200,response.text)
            value=response.json()
            if value['state'] not in ['waiting','running','recovering']: return response
            time.sleep(.05)
        self.fail('task did not finish')
    def test_all_real_http_stages(self):
        self.pause()
        session=requests.Session(); name='buffer-'+secrets.token_hex(4); password='Strong-pass-927!'
        registration=self.call(session,'POST','/api/auth/register',json=dict(username=name,password=password))
        self.assertEqual(registration.status_code,202,registration.text)
        self.assertNotIn('Set-Cookie',registration.headers)
        task=registration.json()['task_id']
        waiting=requests.get(B+'/api/tasks/'+task,headers={'X-Queue-Owner':OWNER},timeout=3)
        self.assertEqual(waiting.json()['state'],'waiting',waiting.text)
        unauthorized=requests.get(B+'/api/tasks/'+task,headers={'X-Queue-Owner':secrets.token_hex(32)},timeout=3)
        self.assertEqual(unauthorized.status_code,401)
        self.resume()
        registered=self.wait(task).json()
        self.assertEqual(registered['state'],'succeeded',registered)
        self.assertEqual(registered['response']['status'],201)
        self.pause()
        login=self.call(session,'POST','/api/auth/login',json=dict(username=name,password=password))
        self.assertEqual(login.status_code,202,login.text)
        self.assertNotIn('Set-Cookie',login.headers)
        self.assertFalse(session.cookies)
        self.resume()
        logged_in=self.wait(login.json()['task_id'],session)
        self.assertEqual(logged_in.json()['state'],'succeeded',logged_in.text)
        self.assertIn('chat_session',session.cookies.get_dict())
        self.assertIn('chat_device',session.cookies.get_dict())
        self.assertNotIn('cookies',logged_in.json()['response'])
        session.headers['Cookie']='; '.join(key+'='+value for key,value in session.cookies.get_dict().items())
        direct=self.call(session,'GET','/api/auth/me')
        self.assertEqual(direct.status_code,200,direct.text)
        self.pause()
        request_id=secrets.token_hex(16)
        queued=self.call(session,'POST','/api/rooms',json={'name':'shared-room'},request_id=request_id)
        self.assertEqual(queued.status_code,202,queued.text)
        self.resume()
        created=self.wait(queued.json()['task_id'],session)
        self.assertEqual(created.json()['state'],'succeeded',created.text)
        room=json.loads(created.json()['response']['body'])
        request_id=secrets.token_hex(16)
        first_direct=self.call(session,'POST','/api/rooms',json={'name':'shared-room'},request_id=request_id)
        self.assertEqual(first_direct.status_code,201,first_direct.text)
        room=first_direct.json()
        replay=self.call(session,'POST','/api/rooms',json={'name':'shared-room'},request_id=request_id,url=B)
        self.assertEqual(replay.status_code,201,replay.text)
        self.assertEqual(replay.json(),room)
        conflict=self.call(session,'POST','/api/rooms',json={'name':'changed'},request_id=request_id)
        self.assertEqual(conflict.status_code,409,conflict.text)
        self.pause()
        lost_permission=self.call(session,'POST',f'/api/rooms/{room["id"]}/invites',json={'username':'nobody'})
        # End the original session while waiting, using the business Redis as
        # the existing fault suite does; execution must authenticate afresh.
        store=redis.Redis(host='redis-proxy',password=os.environ['REDIS_PASSWORD'])
        import hashlib
        store.delete('session:'+hashlib.sha256(session.cookies.get('chat_session').encode()).hexdigest().upper())
        self.resume()
        denied=self.wait(lost_permission.json()['task_id'])
        self.assertEqual(denied.json()['state'],'failed',denied.text)
        self.assertEqual(denied.json()['response']['status'],401,denied.text)
        print('PASS real HTTP: queued register/login, final double Cookie, cross-instance query, SQL replay/conflict, permission revocation')
        # The shared bucket must not bypass the existing per-account login
        # guard. With no device cookie and all passwords wrong, exactly the
        # default five free attempts run; the rest retain explicit 429/backoff.
        self.pause()
        login_tasks=[]
        for _ in range(12):
            attempt=self.call(requests.Session(),'POST','/api/auth/login',
                json={'username':name,'password':'wrong-password-probe'})
            self.assertEqual(attempt.status_code,202,attempt.text)
            login_tasks.append(attempt.json()['task_id'])
        self.resume()
        login_outcomes=[self.wait(task).json() for task in login_tasks]
        self.assertTrue(all(task['state']=='failed' for task in login_outcomes),login_outcomes)
        statuses=[task['response']['status'] for task in login_outcomes]
        self.assertEqual(statuses.count(401),5,statuses)
        self.assertEqual(statuses.count(429),7,statuses)
        for task in login_outcomes:
            if task['response']['status']==429:
                error=json.loads(task['response']['body'])['error']
                self.assertEqual(error['code'],'too_many_attempts')
                self.assertTrue(error['retryable'])
                self.assertGreater(error['retry_after'],0)
        print('PASS queued login burst preserves default protection: five invalid credentials, seven explicit 429 with backoff',flush=True)
        if os.environ.get('BUFFER_EXTENDED_TESTS')!='1': return
        self.pause()
        expiring=self.call(session,'GET','/api/auth/me')
        self.assertEqual(expiring.status_code,202,expiring.text)
        started=time.monotonic()
        time.sleep(45.2)
        expired=self.wait(expiring.json()['task_id']).json()
        self.assertEqual(expired['state'],'expired',expired)
        self.assertGreaterEqual(time.monotonic()-started,45)
        print('PASS actual default 45-second wait expiry',flush=True)
        # Count capacity uses the production default, not a smaller test limit.
        def admit(_): return self.call(requests.Session(),'GET','/api/auth/me')
        with concurrent.futures.ThreadPoolExecutor(16) as pool:
            accepted=list(pool.map(admit,range(1024)))
        self.assertTrue(all(r.status_code==202 for r in accepted))
        self.assertEqual(admit(None).status_code,503)
        first=accepted[0].json()['task_id']
        self.assertEqual(requests.get(B+'/api/tasks/'+first,headers={'X-Queue-Owner':OWNER},timeout=3).json()['state'],'waiting')
        print('PASS actual default 1024 waiting limit preserves older tasks',flush=True)
        self.signal('queue-stop')
        unavailable=admit(None)
        self.assertEqual(unavailable.status_code,503,unavailable.text)
        query=requests.get(B+'/api/tasks/'+first,headers={'X-Queue-Owner':OWNER},timeout=5)
        self.assertEqual(query.status_code,503,query.text)
        self.signal('queue-start')
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                if self.ctrl(B,'status',{}).get('queue_ready'): break
            except requests.RequestException: pass
            time.sleep(.1)
        self.assertEqual(self.wait(first).json()['state'],'unknown')
        # After restart there is spare capacity. A new admission failing below
        # therefore proves real OOM handling rather than the 1024-item limit.
        old=admit(None)
        self.assertEqual(old.status_code,202,old.text)
        self.signal('memory-on-1')
        # noeviction preserves readable waiting records under memory pressure;
        # successful reads do not imply that a new write can be accepted.
        pressured=requests.get(B+'/api/tasks/'+old.json()['task_id'],headers={'X-Queue-Owner':OWNER},timeout=5)
        self.assertEqual(pressured.status_code,200,pressured.text)
        self.assertEqual(pressured.json()['state'],'waiting')
        self.assertEqual(admit(None).status_code,503)
        self.signal('memory-off-1')
        preserved=requests.get(B+'/api/tasks/'+old.json()['task_id'],headers={'X-Queue-Owner':OWNER},timeout=5)
        self.assertEqual(preserved.status_code,200,preserved.text)
        self.assertEqual(preserved.json()['state'],'waiting')
        print('PASS real Redis OOM/noeviction with spare capacity: new admission unavailable, old waiting task readable and preserved',flush=True)
        self.resume()
        drained=self.wait(old.json()['task_id']).json()
        self.assertEqual(drained['state'],'failed',drained)
        self.assertEqual(drained['response']['status'],401,drained)
        print('PASS queue Redis stop/restart: explicit unavailable then unknown, never fake success',flush=True)
        # New login gets a new intent ID. The expired original session remains
        # invalid, and no credential has been written to generic SQL memos.
        fresh=requests.Session()
        login=self.call(fresh,'POST','/api/auth/login',json=dict(username=name,password=password))
        self.assertEqual(login.status_code,200,login.text)
        fresh.headers['Cookie']='; '.join(key+'='+value for key,value in fresh.cookies.get_dict().items())
        self.pause()
        request_id='crash-probe-'+secrets.token_hex(16)
        queued=self.call(fresh,'POST','/api/rooms',json={'name':'commit-before-death'},request_id=request_id)
        self.assertEqual(queued.status_code,202,queued.text)
        self.resume([A])
        db=pymysql.connect(host='mysql',user='chat',password=os.environ['MYSQL_PASSWORD'],database='chat',autocommit=True)
        try:
            deadline=time.monotonic()+5
            committed=None
            while time.monotonic()<deadline:
                with db.cursor() as cursor:
                    cursor.execute('SELECT result FROM business_request_results WHERE request_id=%s AND result IS NOT NULL',(request_id,))
                    committed=cursor.fetchone()
                if committed: break
                time.sleep(.01)
            self.assertIsNotNone(committed,'test failed to observe a committed SQL result')
            original=json.loads(committed[0])
            state=requests.get(B+'/api/tasks/'+queued.json()['task_id'],headers={'X-Queue-Owner':OWNER},timeout=3).json()
            self.assertEqual(state['state'],'running',state)
            started=time.monotonic()
            Path('/fault-control/result-task-id').write_text(queued.json()['task_id'])
            # Redis Lua may finish a shrinking result write even above
            # maxmemory. Deny EVAL explicitly to force a real result-write
            # rejection; the independent OOM admission proof is above.
            self.signal('result-block')
            time.sleep(2.5) # the test-only result hold is 2s; finish is refused
            self.signal('inspect-result')
            observed=json.loads(Path('/fault-control/inspect-result.json').read_text())
            self.assertEqual(observed['state'],'running',observed)
            self.signal('kill-a')
            self.signal('result-unblock')
            self.resume([B])
            recovered=self.wait(queued.json()['task_id'],fresh,deadline=25).json()
            self.assertEqual(recovered['state'],'succeeded',recovered)
            self.assertEqual(json.loads(recovered['response']['body']),original)
            self.assertLess(time.monotonic()-started,30)
            with db.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM rooms WHERE name='commit-before-death'")
                self.assertEqual(cursor.fetchone()[0],1)
            print('PASS actual result-write rejection and SIGKILL after SQL commit: B recovered the original room exactly once within 30 seconds',flush=True)
        finally: db.close()

    def signal(self,name):
        path=Path('/fault-control')
        (path/name).touch()
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if (path/(name+'.done')).exists(): return
            time.sleep(.05)
        self.fail('host fault driver did not acknowledge '+name)


if __name__=='__main__': unittest.main(verbosity=2)
