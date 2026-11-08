from chat_ai.host import parse_host_json


def test_parses_a_plain_decision():
    assert parse_host_json('{"speakers": ["分析者"], "mode": "speak", "end": false}') == {
        "speakers": ["分析者"], "mode": "speak", "end": False}


def test_parses_a_fenced_decision_ignoring_surrounding_text():
    text = '可以。\n```json\n{"speakers": ["审查者"], "mode": "broadcast", "end": true}\n```\n'
    assert parse_host_json(text) == {"speakers": ["审查者"], "mode": "broadcast", "end": True}


def test_defaults_mode_to_speak_and_end_to_false():
    assert parse_host_json('{"speakers": ["文案"]}') == {"speakers": ["文案"], "mode": "speak", "end": False}


def test_invalid_json_is_none():
    assert parse_host_json('not json at all') is None
    assert parse_host_json('{"speakers": )') is None


def test_missing_or_non_list_speakers_is_none():
    assert parse_host_json('{"mode": "speak", "end": false}') is None
    assert parse_host_json('{"speakers": "分析者"}') is None


def test_invalid_mode_is_none():
    assert parse_host_json('{"speakers": ["分析者"], "mode": "sing"}') is None