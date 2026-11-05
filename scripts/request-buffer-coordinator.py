#!/usr/bin/env python3
"""Existing redis-master-watch's optional request-gate coordinator.

There is one watcher in the existing single redis-proxy container. No Docker
API, process isolation of backends, or second Sentinel cluster is involved.
A grant may only leave a renewal thread while holding the phase lock. Revoking
that phase therefore bounds every old grant by its already-created challenge
plus PERMIT seconds, including grants whose HTTP response is lost.
"""
import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import threading
import time
import uuid


def redis_command(host, port, password, *parts):
    def encode(values):
        values=[str(x).encode() for x in values]
        return b'*%d\r\n'%len(values)+b''.join(b'$%d\r\n'%len(v)+v+b'\r\n' for v in values)
    with socket.create_connection((host,port),.7) as sock:
        sock.settimeout(.7)
        with sock.makefile('rb') as file:
            def read():
                line=file.readline()
                if not line: raise ConnectionError('Redis response lost')
                tag,raw=line[:1],line[1:-2]
                if tag==b'-': raise ConnectionError('Redis command refused')
                if tag==b'+': return raw.decode()
                if tag==b':': return int(raw)
                count=int(raw)
                if count==-1: return None
                if tag==b'*': return [read() for _ in range(count)]
                value=file.read(count); file.read(2)
                return value.decode()
            sock.sendall(encode(['AUTH',password])); read()
            sock.sendall(encode(parts)); return read()


class Route:
    def __init__(self): self.password=os.environ['REDIS_PASSWORD']
    def master(self):
        def vote(host):
            try:
                value=redis_command(host,26379,self.password,'SENTINEL','get-master-addr-by-name','chat')
                return value[0] if value and value[0] in ('redis-1','redis-2') and value[1]=='6379' else None
            except (OSError,ValueError,ConnectionError): return None
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            votes=list(pool.map(vote,['sentinel-1','sentinel-2','sentinel-3']))
        for host in ('redis-1','redis-2'):
            if votes.count(host)>=2: return host
    def command(self, text):
        with socket.create_connection(('127.0.0.1',9998),.5) as sock:
            sock.settimeout(.5); sock.sendall((text+'\n').encode()); sock.shutdown(socket.SHUT_WR)
            output=b''
            while True:
                data=sock.recv(65536)
                if not data: return output.decode()
                output+=data
    def disable(self):
        self.command('disable server redis_master/master')
        self.command('shutdown sessions server redis_master/master')
    def address_matches(self,host):
        expected=socket.gethostbyname(host)
        lines=self.command('show servers state').splitlines()
        columns=None
        for line in lines:
            if line.startswith('#'): columns=line[1:].split()
            elif columns:
                row=dict(zip(columns,line.split()))
                if row.get('be_name')=='redis_master' and row.get('srv_name')=='master':
                    return row.get('srv_fqdn')==host and row.get('srv_addr')==expected
        return False
    def primary(self,host):
        try: return 'role:master\r\n' in redis_command(host,6379,self.password,'INFO','replication')
        except (OSError,ConnectionError): return False
    def ready(self,host):
        try: return self._ready(host)
        except (OSError,ConnectionError,StopIteration,ValueError): return False
    def _ready(self,host):
        if not self.address_matches(host): return False
        info=redis_command(host,6379,self.password,'INFO','replication')
        if 'role:master\r\n' not in info: return False
        # The proxy listener must really serve the expected master, not just
        # have accepted the set-FQDN command.
        proxy=redis_command('127.0.0.1',6379,self.password,'INFO','server')
        direct=redis_command(host,6379,self.password,'INFO','server')
        def runid(text): return next(x for x in text.splitlines() if x.startswith('run_id:'))
        return runid(proxy)==runid(direct)
    def point(self,host):
        self.command(f'set server redis_master/master fqdn {host}')
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                if self.address_matches(host): break
            except OSError: pass
            time.sleep(.1)
        else: raise TimeoutError('proxy address did not resolve within 10 seconds')
        self.command('shutdown sessions server redis_master/master')
        self.command('enable server redis_master/master')
        while time.monotonic()<deadline:
            try:
                if self.ready(host): return
            except (OSError,ConnectionError): pass
            time.sleep(.1)
        raise TimeoutError('new master/proxy was not ready within 10 seconds')
    def failover(self):
        for host in ('sentinel-1','sentinel-2','sentinel-3'):
            try: redis_command(host,26379,self.password,'PING')
            except (OSError,ConnectionError): continue
            # Once sent, a lost response is unknown. Never blindly ask a
            # different Sentinel to start a second manual promotion.
            return redis_command(host,26379,self.password,'SENTINEL','FAILOVER','chat')
        raise ConnectionError('no Sentinel available for controlled switch')


class Backends:
    def __init__(self): self.token=os.environ['BUFFER_CONTROL_TOKEN']
    def post(self,host,action,body):
        connection=http.client.HTTPConnection(host,8000,timeout=.5)
        try:
            connection.request('POST','/internal/buffer/'+action,json.dumps(body),
                               {'Content-Type':'application/json','Authorization':'Bearer '+self.token})
            response=connection.getresponse()
            if response.status!=200: raise ConnectionError('backend control refused')
            return json.loads(response.read())
        finally: connection.close()
    def healthy(self,host):
        connection=http.client.HTTPConnection(host,8000,timeout=.5)
        try:
            connection.request('GET','/health')
            response=connection.getresponse(); response.read()
            return response.status==200
        except OSError: return False
        finally: connection.close()


class Coordinator:
    def __init__(self,route,backends,hosts,permit=5,renew=1,clock=time.monotonic,sleep=time.sleep):
        self.route,self.backends,self.hosts=route,backends,hosts
        self.permit,self.renew,self.clock,self.sleep=permit,renew,clock,sleep
        self.epoch=uuid.uuid4().hex
        self.revision=0
        self.lock=threading.Lock()
        self.active={}  # immutable backend boot/revision grants in this phase
        self.cutoff=self.clock()  # previous coordinator died before this boot
        self.paused=True
        self.verified_at=float("-inf")
        self.queue_incarnation=None
        self.pending_switch=None
        self.stop=threading.Event()
    def revoke(self):
        with self.lock:
            self.active={}
            self.cutoff=self.clock()
            self.paused=True
            self.verified_at=float("-inf")
    def info(self,host):
        try: return self.backends.post(host,'status',{})
        except (OSError,ValueError,ConnectionError): return None
    def all_info(self):
        with concurrent.futures.ThreadPoolExecutor(len(self.hosts)) as pool:
            return dict(zip(self.hosts,pool.map(self.info,self.hosts)))
    def fence(self,host,info,revision):
        if not info or not info.get('queue_ready'): return None
        body={'boot':info['boot'],'epoch':self.epoch,'revision':revision}
        try:
            if info['epoch']!=self.epoch:
                fenced=self.backends.post(host,'fence',{**body,'expected_epoch':info['epoch']})
                if not fenced.get('ok'): return None
            elif not self.backends.post(host,'pause',body).get('ok'): return None
            return body
        except (OSError,ValueError,ConnectionError): return None
    def queue_mode(self,infos,paused):
        for host,info in infos.items():
            if not info or not info.get('queue_ready'): continue
            queue=info.get('queue',{})
            if not queue.get('incarnation'): continue
            body={'boot':info['boot'],'epoch':self.epoch,'revision':int(queue['revision'])+1,
                  'expected_epoch':queue['epoch'],'paused':paused}
            try:
                if self.backends.post(host,'queue',body).get('ok'):
                    self.queue_incarnation=queue['incarnation']
                    return True
            except (OSError,ValueError,ConnectionError): pass
        return False
    def pause(self):
        self.revoke()
        self.revision+=1
        infos=self.all_info()
        self.queue_mode(infos,True)
        with concurrent.futures.ThreadPoolExecutor(len(self.hosts)) as pool:
            acknowledgements=list(pool.map(lambda pair:self.fence(pair[0],pair[1],self.revision),infos.items()))
        return all(acknowledgements)
    def resume(self,master):
        # With a missing ACK, every pre-revoke grant used a nonce created no
        # later than cutoff. Receipt does not restart a backend's lease clock.
        remaining=self.cutoff+self.permit-self.clock()
        if remaining>0: self.sleep(remaining)
        if self.route.master()!=master or not self.route.ready(master): return False
        self.revision+=1
        infos=self.all_info()
        if not self.queue_mode(infos,False): return False
        healthy={}
        for host,info in infos.items():
            if self.backends.healthy(host):
                grant=self.fence(host,info,self.revision)
                if grant: healthy[host]=grant
        if not healthy: return False
        with self.lock:
            self.active=healthy; self.paused=False; self.verified_at=self.clock()
        return True
    def refresh(self):
        infos=self.all_info()
        for host,info in infos.items():
            with self.lock: previous=self.active.get(host)
            if not info or not info.get('queue_ready'): continue
            if info.get('queue',{}).get('incarnation')!=self.queue_incarnation:
                return False
            if previous and previous['boot']==info['boot']: continue
            self.revision+=1
            grant=self.fence(host,info,self.revision)
            if grant and self.backends.healthy(host):
                with self.lock: self.active[host]=grant
        return True
    def renew_once(self,host):
        with self.lock:
            grant=self.active.get(host)
            fresh=self.clock()<self.verified_at+2
        if not fresh or not grant or not self.backends.healthy(host): return
        try:
            challenge=self.backends.post(host,'challenge',grant)
            if not challenge.get('ok'): return
            # A delayed challenge response cannot cross a pause/restart phase.
            with self.lock:
                if self.active.get(host) is not grant or self.clock()>=self.verified_at+2: return
                self.backends.post(host,'grant',{**grant,'nonce':challenge['nonce'],'mode':'draining'})
        except (OSError,ValueError,ConnectionError): return
    def renew_loop(self,host):
        while not self.stop.is_set():
            self.renew_once(host)
            self.stop.wait(self.renew)
    def controlled(self,current):
        # Nothing asks Sentinel to promote before every pause ACK arrives.
        if not self.pause():
            if self.route.master()==current and self.route.ready(current): self.resume(current)
            return 'cancelled: missing pause acknowledgement'
        self.route.disable()
        self.route.failover()
        self.pending_switch=current
        return 'started'
    def run(self):
        for host in self.hosts: threading.Thread(target=self.renew_loop,args=(host,),daemon=True).start()
        current=None
        deadline=self.clock()+3
        while True:
            try: self.route.disable(); break
            except OSError:
                if self.clock()>=deadline: raise
                self.sleep(.1)
        self.pause()
        while not self.stop.is_set():
            next_master=self.route.master()
            request=Path('/tmp/redis-switch-request')
            if request.exists() and not self.paused and current:
                request_id=request.read_text().strip(); request.unlink()
                outcome=self.controlled(current)
                Path('/tmp/redis-switch-result').write_text(request_id+' '+outcome)
            if self.pending_switch and next_master==self.pending_switch:
                self.route.disable()
                self.stop.wait(.5)
                continue
            if next_master and self.pending_switch:
                self.pending_switch=None
            if next_master is None:
                if not self.paused: self.pause()
                self.route.disable()
            elif current!=next_master or self.paused:
                if not self.paused: self.pause()
                # Sentinel can still name the stopped old primary before it
                # promotes a new one. Keep the route closed and keep voting;
                # do not re-resolve/reopen the dead container in a restart loop.
                if not self.route.primary(next_master):
                    self.stop.wait(.5)
                    continue
                # A usable new route must stay open while clients reconnect.
                # Re-draining it on every failed health probe causes livelock.
                if current!=next_master or not self.route.ready(next_master):
                    self.route.disable()
                    self.route.point(next_master)
                    current=next_master
                if self.resume(next_master):
                    print('redis buffered route ready: '+next_master,flush=True)
            elif not self.route.ready(current) or not self.refresh():
                self.pause(); self.route.disable()
            else:
                with self.lock: self.verified_at=self.clock()
            self.stop.wait(.5)


def request_switch():
    request_id=uuid.uuid4().hex
    fd=os.open('/tmp/redis-switch-request',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream: stream.write(request_id)
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        try:
            result=Path('/tmp/redis-switch-result').read_text()
            if result.startswith(request_id+' '):
                print(result[len(request_id)+1:]); return 0 if result.endswith('started') else 1
        except FileNotFoundError: pass
        time.sleep(.1)
    print('switch request outcome unknown; inspect watcher logs')
    return 1

if __name__=='__main__':
    import sys
    if len(sys.argv)>1 and sys.argv[1]=='request': sys.exit(request_switch())
    permit=int(os.environ.get('BUFFER_PERMIT_MS','5000'))/1000
    coordinator=Coordinator(Route(),Backends(),os.environ.get('BUFFER_BACKENDS','app-1 app-2').split(),
                            permit=permit,renew=float(os.environ.get('BUFFER_RENEW_SECONDS','1')))
    def terminate(*_): coordinator.stop.set()
    signal.signal(signal.SIGTERM,terminate); signal.signal(signal.SIGINT,terminate)
    try: coordinator.run()
    finally:
        coordinator.revoke()
        # Docker restart reacts to PID 1 exit, not only an unhealthy watcher.
        os.kill(1,signal.SIGTERM)
