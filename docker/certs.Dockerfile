FROM alpine:3.20
RUN apk add --no-cache openssl
COPY scripts/gen-certs.sh /usr/local/bin/gen-certs
ENTRYPOINT ["sh", "/usr/local/bin/gen-certs"]
