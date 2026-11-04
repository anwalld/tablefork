# Redis nodes keep their original image; only Sentinel uses these two guards.
FROM redis:7.4.5 AS build
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev make libssl-dev patch \
    && rm -rf /var/lib/apt/lists/*
ADD --checksum=sha256:00bb280528f5d7934bec8ab309b8125088c209131e10609cb1563b91365633bb \
    https://download.redis.io/releases/redis-7.4.5.tar.gz /tmp/redis.tar.gz
WORKDIR /src
RUN tar -xzf /tmp/redis.tar.gz --strip-components=1
COPY ignore-self.patch /tmp/ignore-self.patch
RUN patch -p1 < /tmp/ignore-self.patch \
    && make -j2 BUILD_TLS=yes OPTIMIZATION=-O2 redis-server

FROM redis:7.4.5
COPY --from=build /src/src/redis-server /usr/local/bin/redis-server
