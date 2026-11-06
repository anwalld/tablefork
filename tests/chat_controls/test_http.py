"""Exercise real HTTP/WS handlers and SQL state in disposable services."""
import concurrent.futures
import json
import os
import time
import unittest
import uuid

import pymysql
import requests
from websockets.sync.client import connect

BASE = 'http://app:8000'
PASSWORD = 'correct-horse-battery'


class ChatControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for _ in range(100):
            try:
                if requests.get(BASE + '/health', timeout=2).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            raise AssertionError('app did not become healthy')
        cls.db = pymysql.connect(host='mysql', user='chat', password=os.environ['MYSQL_PASSWORD'], database='chat', autocommit=True)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def user(self):
        session = requests.Session()
        name = 'u' + uuid.uuid4().hex[:12]
        self.assertEqual(session.post(BASE + '/api/auth/register', json={'username': name, 'password': PASSWORD}).status_code, 201)
        self.assertEqual(session.post(BASE + '/api/auth/login', json={'username': name, 'password': PASSWORD}).status_code, 200)
        info = session.get(BASE + '/api/auth/me').json()
        return session, name, info['member_id']

    def room(self, owner):
        response = owner.post(BASE + '/api/rooms', json={'name': 'attention room'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    def join(self, owner, guest, name, room):
        self.assertEqual(owner.post(BASE + f'/api/rooms/{room}/invites', json={'username': name}).status_code, 200)
        self.assertEqual(guest.post(BASE + f'/api/rooms/{room}/join').status_code, 200)

    def send(self, session, room, text, message_id=None):
        cookie = '; '.join(f'{k}={v}' for k, v in session.cookies.items())
        with connect('ws://app:8000/ws', origin='https://localhost:8443', additional_headers={'Cookie': cookie}) as socket:
            message_id = message_id or uuid.uuid4().hex
            socket.send(json.dumps({'type': 'send', 'room_id': room, 'client_msg_id': message_id, 'content': text}))
            result = json.loads(socket.recv(timeout=5))
            self.assertEqual(result['type'], 'ack', result)
            return result['seq']

    def summary(self, session, room):
        return next(r for r in session.get(BASE + '/api/rooms').json()['rooms'] if r['id'] == room)

    def test_upgrade_baseline(self):
        with self.db.cursor() as c:
            c.execute("SELECT read_seq FROM room_members WHERE room_id=9000001 AND member_id=9000001")
            self.assertEqual(c.fetchone()[0], 1)

    def test_unread_mentions_monotonic_and_rejoin(self):
        owner, _, _ = self.user()
        guest, name, member = self.user()
        room = self.room(owner)
        self.send(owner, room, 'before join')
        self.join(owner, guest, name, room)
        self.assertEqual(self.summary(guest, room)['unread_count'], 0)
        mid = uuid.uuid4().hex
        seq = self.send(owner, room, f'@{name} hello', mid)
        self.send(owner, room, f'@{name} hello', mid)
        self.send(guest, room, 'my own message')
        state = self.summary(guest, room)
        self.assertEqual(state['unread_count'], 1)
        self.assertEqual(state['mention_seq'], seq)
        self.assertEqual(guest.put(BASE + f'/api/rooms/{room}/read', json={'seq': seq}).status_code, 200)
        self.assertEqual(guest.put(BASE + f'/api/rooms/{room}/read', json={'seq': 0}).status_code, 200)
        self.assertEqual(self.summary(guest, room)['unread_count'], 0)
        self.assertIsNone(self.summary(guest, room)['mention_seq'])
        high = self.send(owner, room, 'concurrent read')
        cookies = guest.cookies.get_dict()
        def mark(seq):
            return requests.put(BASE + f'/api/rooms/{room}/read', cookies=cookies, json={'seq': seq}, timeout=5).status_code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(mark, [0, high, seq, high])), [200] * 4)
        self.assertEqual(self.summary(guest, room)['read_seq'], high)
        self.assertEqual(self.summary(guest, room)['unread_count'], 0)
        self.assertEqual(guest.post(BASE + f'/api/rooms/{room}/leave').status_code, 200)
        self.send(owner, room, 'while absent')
        self.join(owner, guest, name, room)
        self.assertEqual(self.summary(guest, room)['unread_count'], 0)
        outsider, _, _ = self.user()
        self.assertEqual(outsider.put(BASE + f'/api/rooms/{room}/read', json={'seq': 999}).status_code, 404)

    def test_search_filters_literals_and_permissions(self):
        owner, _, member = self.user()
        room = self.room(owner)
        self.send(owner, room, 'literal %_needle')
        self.send(owner, room, 'second needle')
        path = BASE + f'/api/rooms/{room}/search'
        result = owner.get(path, params={'q': '%_needle', 'sender': member}).json()
        self.assertEqual(len(result['messages']), 1)
        result = owner.get(path, params={'q': 'needle', 'limit': 1}).json()
        self.assertEqual(result['messages'][0]['content'], 'second needle')
        earlier = owner.get(path, params={'q': 'needle', 'before': result['next_before']}).json()
        self.assertEqual(len(earlier['messages']), 1)
        self.assertEqual(owner.get(path, params={'from': 20, 'to': 10}).status_code, 400)
        outsider, _, _ = self.user()
        self.assertEqual(outsider.get(path).status_code, 404)

    def test_stop_partial_permissions_pause_and_duplicate(self):
        owner, _, _ = self.user()
        payer, name, member = self.user()
        other, other_name, _ = self.user()
        room = self.room(owner)
        self.join(owner, payer, name, room)
        self.join(owner, other, other_name, room)
        with self.db.cursor() as c:
            c.execute("SELECT member_id FROM ai_profiles ORDER BY member_id LIMIT 1")
            ai = c.fetchone()[0]
            c.execute("INSERT INTO room_members (room_id, member_id) VALUES (%s,%s)", (room, ai))
            generation = uuid.uuid4().hex
            c.execute("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content) VALUES (%s,%s,%s,%s,'partial reply')", (generation, room, ai, member))
        stop = BASE + f'/api/rooms/{room}/ai-generations/{generation}/stop'
        self.assertEqual(other.post(stop).status_code, 403)
        self.assertEqual(payer.post(stop).status_code, 200)
        self.assertEqual(payer.post(stop).status_code, 200)
        messages = owner.get(BASE + f'/api/rooms/{room}/messages').json()['messages']
        stopped = [m for m in messages if m.get('generation_id') == generation]
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]['content'], 'partial reply')
        self.assertTrue(stopped[0]['stopped'])
        empty = uuid.uuid4().hex
        with self.db.cursor() as c:
            c.execute("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content) VALUES (%s,%s,%s,%s,'')", (empty, room, ai, member))
        self.assertEqual(payer.post(BASE + f'/api/rooms/{room}/ai-generations/{empty}/stop').status_code, 200)
        self.assertEqual(len(owner.get(BASE + f'/api/rooms/{room}/messages').json()['messages']), 1)
        state = BASE + f'/api/rooms/{room}/ai-state'
        self.assertEqual(payer.put(state, json={'paused': True}).status_code, 403)
        self.assertEqual(owner.put(state, json={'paused': True}).status_code, 200)
        self.assertTrue(owner.get(state).json()['paused'])
        self.assertEqual(owner.put(state, json={'paused': False}).status_code, 200)
        self.assertFalse(owner.get(state).json()['paused'])

    def test_unstorable_stopped_snapshots_do_not_block_room_controls(self):
        owner, _, member = self.user()
        room = self.room(owner)
        orphan, blank = uuid.uuid4().hex, uuid.uuid4().hex
        with self.db.cursor() as c:
            c.execute("SELECT member_id FROM ai_profiles ORDER BY member_id LIMIT 1")
            ai = c.fetchone()[0]
            # The AI is not (or no longer) a room member, so its stopped
            # snapshot can never become a message.
            c.execute("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content,status,stopped) "
                      "VALUES (%s,%s,%s,%s,'left mid reply','stopped',TRUE)", (orphan, room, ai, member))
            c.execute("INSERT INTO room_members (room_id, member_id) VALUES (%s,%s)", (room, ai))
            c.execute("INSERT INTO ai_generations (generation_id,room_id,member_id,payer_member_id,content) "
                      "VALUES (%s,%s,%s,%s,' \n')", (blank, room, ai, member))
            c.execute("DELETE FROM room_members WHERE room_id=%s AND member_id=%s", (room, ai))
        state = BASE + f'/api/rooms/{room}/ai-state'
        self.assertEqual(owner.put(state, json={'paused': True}).status_code, 200)
        self.assertEqual(owner.put(state, json={'paused': False}).status_code, 200)
        with self.db.cursor() as c:
            c.execute("SELECT status FROM ai_generations WHERE generation_id IN (%s,%s)", (orphan, blank))
            self.assertEqual([row[0] for row in c.fetchall()], ['saved', 'saved'])
        self.assertEqual(owner.get(BASE + f'/api/rooms/{room}/messages').json()['messages'], [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
