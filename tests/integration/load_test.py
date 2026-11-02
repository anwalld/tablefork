"""Write load against the running stack. Not collected by pytest.

Usage: python load_test.py <rooms> <senders per room> <messages per sender>
"""
import asyncio
import json
import statistics
import sys
import time
import uuid

from websockets.asyncio.client import connect

from conftest import ORIGIN, PASSWORD, WS_URL, Client, unique_name


def make_user():
    client = Client()
    client.username = unique_name()
    client.post(
        "/api/auth/register",
        json={"username": client.username, "password": PASSWORD},
    )
    client.post(
        "/api/auth/login",
        json={"username": client.username, "password": PASSWORD},
    )
    return client


async def sender(client, room_id, count, latencies, errors):
    headers = {"Cookie": client.cookie_header()}
    async with connect(WS_URL, origin=ORIGIN, additional_headers=headers) as ws:
        for index in range(count):
            started = time.perf_counter()
            await ws.send(
                json.dumps(
                    {
                        "type": "send",
                        "room_id": room_id,
                        "client_msg_id": uuid.uuid4().hex,
                        "content": f"load {index}",
                    }
                )
            )
            reply = json.loads(await ws.recv())
            if reply["type"] == "ack":
                latencies.append(time.perf_counter() - started)
            else:
                errors.append(reply["error"]["code"])


async def main(rooms, senders, messages):
    jobs = []
    latencies, errors = [], []
    for _ in range(rooms):
        users = [make_user() for _ in range(senders)]
        room_id = users[0].post("/api/rooms", json={"name": "load"}).json()["id"]
        for guest in users[1:]:
            users[0].post(
                f"/api/rooms/{room_id}/invites", json={"username": guest.username}
            )
            guest.post(f"/api/rooms/{room_id}/join")
        jobs += [
            sender(user, room_id, messages, latencies, errors) for user in users
        ]
    started = time.perf_counter()
    await asyncio.gather(*jobs)
    elapsed = time.perf_counter() - started
    latencies.sort()
    print(
        f"rooms={rooms} senders={senders} acked={len(latencies)} "
        f"errors={len(errors)} rate={len(latencies) / elapsed:.0f}/s "
        f"p50={statistics.median(latencies) * 1000:.1f}ms "
        f"p99={latencies[int(len(latencies) * 0.99)] * 1000:.1f}ms"
    )


if __name__ == "__main__":
    asyncio.run(main(*map(int, sys.argv[1:4])))
