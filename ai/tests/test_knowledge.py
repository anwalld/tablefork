import fakeredis

from chat_ai import knowledge
from chat_ai.engine import Engine
from chat_ai.providers.factory import ProviderFactory

from test_engine import MemoryKeys, settings


def test_logs_are_cut_on_lines_with_overlap():
    lines = [f"14:{i:02d} event number {i} happened here" for i in range(60)]
    chunks = knowledge.chunk_document("log", "\n".join(lines), size=300, overlap=100)
    assert chunks[0][0].startswith("第 1-")
    assert all(location.startswith("第 ") for location, _ in chunks)
    # Consecutive chunks share a line, and together they cover everything.
    first_end = int(chunks[0][0].split("-")[1].split()[0])
    second_start = int(chunks[1][0].split()[1].split("-")[0])
    assert second_start <= first_end
    assert "event number 59" in chunks[-1][1]
    assert all(len(content) <= 300 for _, content in chunks)


def test_markdown_is_cut_by_heading_path():
    text = "intro\n# 部署\n用 compose\n## 数据库\n用 MySQL 8\n# 测试\n跑 test.sh"
    chunks = knowledge.chunk_document("markdown", text)
    assert [location for location, _ in chunks] == ["开头", "部署", "部署 > 数据库", "测试"]
    assert "MySQL 8" in dict(chunks)["部署 > 数据库"]


def test_hits_carry_name_and_position_and_fit_the_budget():
    hits = [{"name": "app.log", "location": "第 3-9 行", "content": "a" * 100},
            {"name": "app.log", "location": "第 10-20 行", "content": "b" * 1000}]
    picked = knowledge.pick_hits(hits, 400)
    note = knowledge.knowledge_note(picked)
    assert "仅供参考" in note.content
    assert "[资料：app.log · 第 3-9 行]" in note.content
    assert "b" * 50 not in note.content
    assert knowledge.knowledge_note([]) is None


class MemoryVectors:
    def __init__(self):
        self.rows, self.fail = {}, False

    async def upsert(self, rows):
        for chunk_id, document_id, room_id, vector in rows:
            self.rows[chunk_id] = (document_id, room_id, vector)

    async def search(self, room_ids, vector, top_k):
        if self.fail:
            raise ConnectionError("vector database down")
        scored = sorted(((sum(a * b for a, b in zip(vector, v)), c)
                         for c, (d, r, v) in self.rows.items() if r in room_ids), reverse=True)
        return [c for _, c in scored[:top_k]]

    async def delete_document(self, document_id):
        self.rows = {c: row for c, row in self.rows.items() if row[0] != document_id}


class Documents:
    """In-memory knowledge_documents/knowledge_chunks."""

    def __init__(self, monkeypatch):
        self.docs, self.chunks = {}, {}

        async def documents_to_process(pool, limit=5):
            return [(i, d["status"]) for i, d in self.docs.items() if d["status"] in ("pending", "deleted")]
        async def load_document(pool, document_id):
            return dict(self.docs[document_id]) if document_id in self.docs else None
        async def replace_chunks(pool, document, chunks):
            ids = []
            for index, (location, content) in enumerate(chunks):
                chunk_id = len(self.chunks) + 1
                self.chunks[chunk_id] = {"doc": document["id"], "location": location, "content": content}
                ids.append(chunk_id)
            return ids
        async def set_status(pool, document_id, status, reason=None, chunk_count=None):
            if self.docs[document_id]["status"] != "deleted":
                self.docs[document_id].update(status=status, reason=reason)
        async def drop_document(pool, document_id):
            self.docs.pop(document_id, None)
        async def has_ready_documents(pool, room_ids):
            return any(d["status"] == "ready" and d["room_id"] in room_ids for d in self.docs.values())
        async def load_hits(pool, chunk_ids):
            return [{"name": self.docs[self.chunks[c]["doc"]]["name"], "location": self.chunks[c]["location"],
                     "content": self.chunks[c]["content"]}
                    for c in chunk_ids if self.docs.get(self.chunks[c]["doc"], {}).get("status") == "ready"]

        for name, fn in list(locals().items()):
            if callable(fn) and name != "self":
                monkeypatch.setattr(knowledge, name, fn)

    def add(self, document_id, content, name="app.log", kind="log", room_id=1, user_id=1):
        self.docs[document_id] = {"id": document_id, "room_id": room_id, "member_id": 10, "user_id": user_id,
                                  "name": name, "kind": kind, "content": content, "status": "pending"}


def make_engine(keys):
    actions = []
    engine = Engine(settings(knowledge_enabled=True, pgvector_dsn="x", embedding_dimensions=64),
                    fakeredis.FakeAsyncRedis(decode_responses=True), object(), MemoryKeys(keys), {},
                    ProviderFactory(2, fake=True), actions.append)
    engine.vectors = MemoryVectors()
    return engine, actions


LOG = "\n".join(["09:00 service started", "14:01 request ok",
                 "14:02 NullPointerException in OrderService", "14:03 retry succeeded"])


async def test_import_without_an_embedding_key_stays_not_ready(monkeypatch):
    docs = Documents(monkeypatch)
    docs.add(1, LOG)
    engine, actions = make_engine({})
    await engine.sweep_knowledge()
    assert docs.docs[1]["status"] == "not_ready"
    notices = [a.notice for a in actions if a.WhichOneof("body") == "notice"]
    assert notices[0].notify_member_id == 10 and notices[0].code == "embedding_key_required"
    assert "OpenAI" in notices[0].message


async def test_imported_log_is_searchable_with_its_source(monkeypatch):
    docs = Documents(monkeypatch)
    docs.add(1, LOG)
    engine, _ = make_engine({(1, "openai"): "fake-o"})
    await engine.sweep_knowledge()
    assert docs.docs[1]["status"] == "ready"
    note = await engine._knowledge_note(1, 0, 1, "14:02 前后发生了什么 NullPointerException")
    assert "[资料：app.log · 第 1-4 行]" in note.content
    assert "NullPointerException" in note.content
    # Another room does not see this room's documents.
    assert await engine._knowledge_note(2, 0, 1, "NullPointerException") is None


async def test_breakout_searches_its_parent_documents(monkeypatch):
    docs = Documents(monkeypatch)
    docs.add(1, LOG, room_id=7)
    engine, _ = make_engine({(1, "openai"): "fake-o"})
    await engine.sweep_knowledge()
    assert await engine._knowledge_note(8, 7, 1, "NullPointerException") is not None


async def test_search_failure_means_no_knowledge(monkeypatch):
    docs = Documents(monkeypatch)
    docs.add(1, LOG)
    engine, _ = make_engine({(1, "openai"): "fake-o"})
    await engine.sweep_knowledge()
    engine.vectors.fail = True
    assert await engine._knowledge_note(1, 0, 1, "NullPointerException") is None


async def test_deleted_documents_lose_their_vectors_and_row(monkeypatch):
    docs = Documents(monkeypatch)
    docs.add(1, LOG)
    engine, _ = make_engine({(1, "openai"): "fake-o"})
    await engine.sweep_knowledge()
    assert engine.vectors.rows
    docs.docs[1]["status"] = "deleted"           # what the chat server does
    assert await engine._knowledge_note(1, 0, 1, "NullPointerException") is None
    await engine.sweep_knowledge()
    assert engine.vectors.rows == {} and 1 not in docs.docs


async def test_embedding_failure_marks_the_document_failed(monkeypatch):
    docs = Documents(monkeypatch)
    docs.add(1, "#fail this text")
    engine, _ = make_engine({(1, "openai"): "fake-o"})
    await engine.sweep_knowledge()
    assert docs.docs[1]["status"] == "failed"
