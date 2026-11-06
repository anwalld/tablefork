"""Shared steps for the AI tests. The stack runs with AI_FAKE_PROVIDERS=1,
so keys starting with "fake-" are accepted and replies are
"<AI name>: <last message>"."""

# A key the tests also look for in the logs, which must never contain it.
MARKED_KEY = "fake-plaintext-marker-7f3a"


def store_key(client, provider, key="fake-key-1234"):
    return client.request("PUT", f"/api/keys/{provider}", json={"key": key})


def role_for(client, model):
    roles = getattr(client, '_private_ai_roles', {})
    if model not in roles:
        names = {'claude':'Claude','gpt':'GPT','gemini':'Gemini','deepseek':'DeepSeek'}
        result = client.post('/api/ai/roles', json={'model_slug':model,'name':names.get(model,model)})
        if result.status_code != 201:
            return None
        roles[model] = result.json()
        client._private_ai_roles = roles
    return roles[model]


def add_ai(client, room_id, slug):
    role = role_for(client, slug)
    return client.post(f"/api/rooms/{room_id}/ai-members", json={'member_id':role['id'],'persona':''} if role else {'slug':slug,'persona':''})


def members(client, room_id):
    return client.get(f"/api/rooms/{room_id}/members").json()["members"]


def ai_slugs(client, room_id):
    return sorted(m["model_slug"] for m in members(client, room_id) if m["type"] == "ai")


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
