FROM python:3.12-slim AS base
WORKDIR /app
COPY ai/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY proto/ai.proto ./proto/
COPY ai/chat_ai ./chat_ai
RUN mkdir -p chat_ai/gen && touch chat_ai/gen/__init__.py \
    && python -m grpc_tools.protoc -I proto --python_out=chat_ai/gen --grpc_python_out=chat_ai/gen proto/ai.proto \
    && sed -i 's/^import ai_pb2/from . import ai_pb2/' chat_ai/gen/ai_pb2_grpc.py
COPY ai/agents.toml ./

# test: unit tests, run while building.
FROM base AS test
COPY ai/requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements-test.txt
COPY ai/tests ./tests
COPY ai/pytest.ini ./
RUN python -m pytest -q tests

FROM base AS runtime
EXPOSE 50051
HEALTHCHECK --interval=3s --timeout=3s --retries=20 \
    CMD python -c "import socket; socket.create_connection(('127.0.0.1', 50051), 1)"
CMD ["python", "-m", "chat_ai"]
