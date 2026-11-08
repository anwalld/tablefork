#!/bin/sh
# 服务器证书。两种模式：
#   CERT_MODE=local（默认）：本地 CA 签发；已有 CA 始终保留，只按需续签服务器证书。
#     PUBLIC_HOSTS 可以加入别人访问用的域名或 IPv4（逗号分隔），访问者需导入 ca.crt。
#   CERT_MODE=provided：使用放在证书目录里的 server.crt（完整证书链）和 server.key，
#     例如 Let's Encrypt 签发的正式证书；只做校验，从不生成或覆盖。
set -eu
umask 077
cert_dir=${CERT_DIR:-/certs}
mkdir -p "$cert_dir"
cd "$cert_dir"
mode=${CERT_MODE:-local}
if [ "$mode" = provided ]; then
    if [ ! -f server.crt ] || [ ! -f server.key ]; then
        echo "CERT_MODE=provided 需要在 $cert_dir 放入 server.crt 和 server.key" >&2
        exit 1
    fi
    if [ "$(openssl x509 -in server.crt -noout -pubkey | openssl sha256)" != \
         "$(openssl pkey -in server.key -pubout | openssl sha256)" ]; then
        echo 'server.crt 和 server.key 不匹配' >&2
        exit 1
    fi
    if ! openssl x509 -in server.crt -checkend 0 -noout >/dev/null; then
        echo 'server.crt 已过期' >&2
        exit 1
    fi
    chmod 600 server.key
    chmod 644 server.crt
    echo '使用提供的证书'
    exit 0
elif [ "$mode" != local ]; then
    echo "未知的 CERT_MODE：$mode（可选 local、provided）" >&2
    exit 1
fi
expected_san='DNS:localhost, DNS:gateway, IP Address:127.0.0.1, IP Address:0:0:0:0:0:0:0:1'
san_ext='DNS:localhost,DNS:gateway,IP:127.0.0.1,IP:::1'
for host in $(printf '%s' "${PUBLIC_HOSTS:-}" | tr ',' ' '); do
    if printf '%s' "$host" | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then
        san_ext="$san_ext,IP:$host"
        expected_san="$expected_san, IP Address:$host"
    elif printf '%s' "$host" | grep -Eq '^[A-Za-z0-9.-]+$'; then
        san_ext="$san_ext,DNS:$host"
        expected_san="$expected_san, DNS:$host"
    else
        echo "PUBLIC_HOSTS 里的 $host 不是域名或 IPv4 地址" >&2
        exit 1
    fi
done
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
    cat > "$work/extensions" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature
extendedKeyUsage=serverAuth
subjectAltName=$san_ext
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
