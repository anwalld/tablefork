"""Shared steps for the AI tests. The stack runs with AI_FAKE_PROVIDERS=1,
so keys starting with "fake-" are accepted and replies are
"<AI name>: <last message>"."""

# A key the tests also look for in the logs, which must never contain it.
MARKED_KEY = "fake-plaintext-marker-7f3a"


def store_key(client, provider, key="fake-key-1234"):
    return client.request("PUT", f"/api/keys/{provider}", json={"key": key})


def add_ai(client, room_id, slug):
    return client.post(f"/api/rooms/{room_id}/ai-members", json={"slug": slug})


def members(client, room_id):
    return client.get(f"/api/rooms/{room_id}/members").json()["members"]


def ai_slugs(client, room_id):
    return sorted(m["slug"] for m in members(client, room_id) if m["type"] == "ai")


def appoint(owner, room_id, user):
    return owner.request("PUT", f"/api/rooms/{room_id}/admins/{user.username}")


def ai_reply(socket, timeout=10):
    """The next stored message from an AI, skipping everything else."""
    while True:
        message = socket.expect("message", timeout=timeout)
        if message["member"]["type"] == "ai":
            return message


def expect_ai_event(socket, *types, timeout=10):
    return socket.expect(*types, timeout=timeout)
