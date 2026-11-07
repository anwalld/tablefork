# Build the main unit image first, then pass its tag as BUILD_IMAGE.
ARG BUILD_IMAGE=cppaiserver-unit
FROM ${BUILD_IMAGE}
ARG PATCHED=1
COPY docker/drogon-redis-disconnect.patch /probe/redis.patch
# Instrument hiredis, Trantor and Drogon together. Application-only ASan
# would leave the released-context reads inside hiredis uninstrumented.
RUN git clone --depth 1 --branch v1.2.0 https://github.com/redis/hiredis.git /tmp/hiredis \
    && cmake -S /tmp/hiredis -B /tmp/hiredis-build -DCMAKE_INSTALL_PREFIX=/opt/redis-probe \
       -DCMAKE_BUILD_TYPE=Debug -DCMAKE_C_FLAGS='-fsanitize=address,undefined -fno-omit-frame-pointer' \
       -DENABLE_SSL=OFF -DDISABLE_TESTS=ON \
    && cmake --build /tmp/hiredis-build -j4 && cmake --install /tmp/hiredis-build
RUN git clone --depth 1 --branch v1.9.13 --recurse-submodules --shallow-submodules \
       https://github.com/drogonframework/drogon.git /tmp/drogon \
    && test "$(git -C /tmp/drogon rev-parse HEAD)" = 4c5430757ea5451a7c38fbbef4b4bef7dbb47f2f \
    && if [ "$PATCHED" = 1 ]; then git -C /tmp/drogon apply /probe/redis.patch; fi \
    && cmake -S /tmp/drogon -B /tmp/drogon-build -DCMAKE_INSTALL_PREFIX=/opt/redis-probe \
       -DCMAKE_PREFIX_PATH=/opt/redis-probe -DHIREDIS_LIBRARY=/opt/redis-probe/lib/libhiredisd.so \
       -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_STANDARD=20 \
       -DCMAKE_C_FLAGS='-fsanitize=address,undefined -fno-omit-frame-pointer' \
       -DCMAKE_CXX_FLAGS='-fsanitize=address,undefined -fno-omit-frame-pointer' \
       -DBUILD_CTL=OFF -DBUILD_EXAMPLES=OFF -DBUILD_TESTING=OFF \
       -DBUILD_POSTGRESQL=OFF -DBUILD_SQLITE=OFF -DBUILD_MYSQL=OFF -DBUILD_REDIS=ON \
    && cmake --build /tmp/drogon-build -j4 && cmake --install /tmp/drogon-build
COPY tests/redis_disconnect /probe/source
RUN cmake -S /probe/source -B /probe/build -DCMAKE_PREFIX_PATH=/opt/redis-probe \
       -DDROGON_SOURCE=/tmp/drogon -DHIREDIS_LIBRARY=/opt/redis-probe/lib/libhiredisd.so \
       -DCMAKE_CXX_FLAGS='-fsanitize=address,undefined -fno-omit-frame-pointer' \
    && cmake --build /probe/build -j4
ENV LD_LIBRARY_PATH=/opt/redis-probe/lib
ENV ASAN_OPTIONS=detect_leaks=0:halt_on_error=1
ENTRYPOINT ["/probe/build/redis-late-send"]
