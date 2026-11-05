FROM haproxy:3.0.5-alpine
USER root
RUN apk add --no-cache python3
