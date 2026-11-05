"""Real login intent replay check; no provider/model calls."""
import json
import os
import time
import uuid
import requests
import redis
import pymysql

base='http://app-1:8000'
owner=uuid.uuid4().hex+uuid.uuid4().hex
session=requests.Session()
session.headers['X-Queue-Owner']=owner

def terminal(response):
    if response.status_code!=202: return response.status_code,response.json()
    task_id=response.json()['task_id']; deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        query=session.get(base+'/api/tasks/'+task_id,timeout=5)
        assert query.status_code==200,query.text
        task=query.json()
        if task['state'] in ['waiting','running','recovering']: time.sleep(.1); continue
        assert task['state']=='succeeded',task
        return task['response']['status'],json.loads(task['response']['body'])
    raise AssertionError('intent probe exceeded its bounded deadline')

username='intent-'+uuid.uuid4().hex[:10]
body=dict(username=username,password='correct-horse-battery')
registration=session.post(base+'/api/auth/register',json=body,headers={'X-Request-Id':uuid.uuid4().hex},timeout=5)
assert terminal(registration)[0]==201
request_id=uuid.uuid4().hex
first=session.post(base+'/api/auth/login',json=body,headers={'X-Request-Id':request_id},timeout=5)
status,data=terminal(first)
assert status==200,(status,data)
store=redis.Redis(host='redis-proxy',password=os.environ['REDIS_PASSWORD'])
key='user_sessions:'+str(data['id'])
count=store.scard(key)
assert count==1,count
repeated=session.post(base+'/api/auth/login',json=body,headers={'X-Request-Id':request_id},timeout=5)
if first.status_code==202:
    assert repeated.status_code==202,repeated.text
    assert repeated.json()['task_id']==first.json()['task_id']
    assert terminal(repeated)[0]==200
else:
    assert repeated.status_code==503,repeated.text
    assert repeated.json()['error']['code']=='result_unknown',repeated.text
    assert repeated.json()['error']['retryable'] is False
assert store.scard(key)==count,'replay created another Redis session'
conflict=session.post(base+'/api/auth/login',json={**body,'password':'different-password-123'},headers={'X-Request-Id':request_id},timeout=5)
assert conflict.status_code==409,conflict.text
connection=pymysql.connect(host='mysql',user='chat',password=os.environ['MYSQL_PASSWORD'],database='chat')
try:
    with connection.cursor() as cursor:
        cursor.execute('SELECT kind, result FROM business_request_results WHERE request_id=%s',(request_id,))
        assert cursor.fetchall()==(('external',None),)
finally: connection.close(); store.close(); session.close()
print('PASS: repeated login intent created no second session; changed parameters rejected; SQL stores no response credential')
