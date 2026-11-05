# deps: toolchain plus Drogon built from source with MySQL and Redis enabled.
FROM ubuntu:24.04 AS deps
ARG DROGON_VERSION=v1.9.13
COPY docker/drogon-mysql-disconnect.patch /tmp/
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates pkg-config \
        libjsoncpp-dev uuid-dev zlib1g-dev libssl-dev libc-ares-dev libbrotli-dev \
        libmariadb-dev libhiredis-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "${DROGON_VERSION}" --recurse-submodules --shallow-submodules \
        https://github.com/drogonframework/drogon.git /tmp/drogon \
    && git -C /tmp/drogon apply /tmp/drogon-mysql-disconnect.patch \
    && cmake -S /tmp/drogon -B /tmp/drogon/build \
        -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=20 \
        -DBUILD_CTL=OFF -DBUILD_EXAMPLES=OFF \
        -DBUILD_POSTGRESQL=OFF -DBUILD_SQLITE=OFF -DBUILD_MYSQL=ON -DBUILD_REDIS=ON \
    && cmake --build /tmp/drogon/build -j"$(nproc)" \
    && cmake --install /tmp/drogon/build \
    && rm -rf /tmp/drogon
# Fail the build if MySQL or Redis support was silently dropped.
RUN grep -q 'MySQL_lib' /usr/local/lib/cmake/Drogon/DrogonTargets.cmake \
    && grep -q 'Hiredis_lib' /usr/local/lib/cmake/Drogon/DrogonTargets.cmake \
    && echo 'Drogon built with MySQL and Redis support'
# gRPC for talking to the AI service. Ubuntu's packages, not a source build.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgrpc++-dev libprotobuf-dev protobuf-compiler protobuf-compiler-grpc \
    && rm -rf /var/lib/apt/lists/*

# dev: the image CLion's Docker toolchain builds and debugs in.
FROM deps AS dev
RUN apt-get update && apt-get install -y --no-install-recommends gdb \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace

# build: compiles the application.
FROM deps AS build
WORKDIR /src
COPY CMakeLists.txt ./
COPY proto ./proto
COPY src ./src
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build -j"$(nproc)"

# unit: building this target runs the unit tests.
FROM deps AS unit
WORKDIR /src
COPY CMakeLists.txt ./
COPY proto ./proto
COPY src ./src
COPY tests/unit ./tests/unit
COPY tests/buffering/component_probe.cc ./tests/buffering/component_probe.cc
COPY data ./data
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DCHAT_BUILD_TESTS=ON \
    && cmake --build build -j"$(nproc)" \
    && ctest --test-dir build --output-on-failure

# runtime: the deployed image.
FROM ubuntu:24.04 AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl libjsoncpp25 libuuid1 zlib1g libssl3t64 libc-ares2 libbrotli1 \
        libmariadb3 libhiredis1.1.0 libgrpc++1.51t64 libprotobuf32t64 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/build/chat-server /usr/local/bin/chat-server
COPY data/common-passwords.txt /usr/local/share/chat/common-passwords.txt
EXPOSE 8000
CMD ["chat-server"]
