#!/bin/sh
# 本地开发证书；已有 CA 始终保留，只按需续签服务器证书。
set -eu
umask 077
cert_dir=${CERT_DIR:-/certs}
mkdir -p "$cert_dir"
cd "$cert_dir"
expected_san='DNS:localhost, DNS:gateway, IP Address:127.0.0.1, IP Address:0:0:0:0:0:0:0:1'
if { [ -f ca.key ] && [ ! -f ca.crt ]; } || { [ ! -f ca.key ] && [ -f ca.crt ]; }; then
    echo 'CA 文件不完整，拒绝替换已有 CA' >&2
    exit 1
fi
if [ ! -f ca.key ]; then
    openssl ecparam -name prime256v1 -genkey -noout -out ca.key
    openssl req -new -x509 -sha256 -key ca.key -out ca.crt -days 3650 \
        -subj '/CN=CppAiServer Local CA' \
        -addext 'basicConstraints=critical,CA:TRUE' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign'
fi
renew=0
if [ ! -f server.key ] || [ ! -f server.crt ]; then
    renew=1
elif ! openssl x509 -in server.crt -checkend 2592000 -noout >/dev/null; then
    renew=1
elif [ "$(openssl x509 -in server.crt -noout -ext subjectAltName | tail -n 1 | sed 's/^ *//')" != "$expected_san" ]; then
    renew=1
elif ! openssl verify -CAfile ca.crt server.crt >/dev/null; then
    renew=1
fi
if [ "$renew" = 1 ]; then
    work=$(mktemp -d .renew.XXXXXX)
    trap 'rm -rf "$work"' EXIT HUP INT TERM
    openssl ecparam -name prime256v1 -genkey -noout -out "$work/server.key"
    openssl req -new -sha256 -key "$work/server.key" -out "$work/server.csr" -subj '/CN=localhost'
    cat > "$work/extensions" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,DNS:gateway,IP:127.0.0.1,IP:::1
EOF
    openssl x509 -req -sha256 -in "$work/server.csr" -CA ca.crt -CAkey ca.key \
        -set_serial "0x$(openssl rand -hex 16)" -out "$work/server.crt" -days 825 \
        -extfile "$work/extensions"
    mv "$work/server.key" server.key
    mv "$work/server.crt" server.crt
fi
chmod 600 ca.key server.key
# CA 供测试容器和宿主机导入，服务器证书供 Nginx 读取。
chmod 644 ca.crt server.crt
