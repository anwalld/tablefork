from conftest import PASSWORD, Client, error_code, unique_name


def register(client, username, password=PASSWORD, **extra):
    payload = {"username": username, "password": password, **extra}
    return client.post("/api/auth/register", json=payload)


def recovery_question(client, username):
    return client.post("/api/auth/password-recovery/question", json={"username": username})


def reset(client, username, answer, password, password_confirm=None):
    return client.post(
        "/api/auth/password-recovery/reset",
        json={
            "username": username,
            "answer": answer,
            "password": password,
            "password_confirm": password_confirm if password_confirm is not None else password,
        },
    )


def login(client, username, password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_register_with_security_question(anon):
    name = unique_name()
    response = register(
        anon,
        name,
        security_question="你的第一只宠物叫什么？",
        security_answer="fluffy",
        password_confirm=PASSWORD,
    )
    assert response.status_code == 201


def test_register_password_mismatch(anon):
    response = register(anon, unique_name(), password_confirm="wrong-password")
    assert response.status_code == 400
    assert error_code(response) == "password_mismatch"


def test_register_half_supplied_security_question(anon):
    response = register(anon, unique_name(), security_question="你的第一只宠物叫什么？")
    assert response.status_code == 400
    assert error_code(response) == "invalid_argument"


def test_recovery_question_and_reset(anon):
    name = unique_name()
    register(
        anon,
        name,
        security_question="你的第一只宠物叫什么？",
        security_answer="fluffy",
        password_confirm=PASSWORD,
    )
    question = recovery_question(anon, name)
    assert question.status_code == 200
    assert question.json()["question"] == "你的第一只宠物叫什么？"

    new_password = "brand-new-password-123"
    assert reset(anon, name, "fluffy", new_password).status_code == 200
    assert login(anon, name, new_password).status_code == 200
    assert login(anon, name, PASSWORD).status_code == 401


def test_recovery_question_unknown_user_is_empty(anon):
    question = recovery_question(anon, unique_name())
    assert question.status_code == 200
    assert question.json()["question"] == ""


def test_recovery_wrong_answer(anon):
    name = unique_name()
    register(
        anon,
        name,
        security_question="你的第一只宠物叫什么？",
        security_answer="fluffy",
        password_confirm=PASSWORD,
    )
    response = reset(anon, name, "wrong-answer", "brand-new-password-123")
    assert response.status_code == 401
    assert error_code(response) == "invalid_answer"


def test_recovery_invalidates_existing_sessions(anon):
    name = unique_name()
    register(
        anon,
        name,
        security_question="你的第一只宠物叫什么？",
        security_answer="fluffy",
        password_confirm=PASSWORD,
    )
    session = Client()
    assert login(session, name).status_code == 200

    new_password = "brand-new-password-123"
    assert reset(anon, name, "fluffy", new_password).status_code == 200

    # The session from before the reset must be gone.
    assert session.get("/api/auth/me").status_code == 401
    assert login(session, name, new_password).status_code == 200


def test_recovery_reset_rejects_peer_password_as_new_password(anon):
    name = unique_name()
    register(
        anon,
        name,
        security_question="你的第一只宠物叫什么？",
        security_answer="fluffy",
        password_confirm=PASSWORD,
    )
    # New password cannot be the username.
    response = reset(anon, name, "fluffy", name)
    assert response.status_code == 400
    assert error_code(response) == "weak_password"