"""同步 WebSocket 首次 sendall 完成后再读，避免 TLS 1.3 tickets 与首写并发。"""
import ssl
import threading

_event_init_lock = threading.Lock()


class WriteFirstTLSSocket(ssl.SSLSocket):
    # SSLContext 创建 SSLSocket 时不调用普通的 __init__。
    _first_write_complete = None
    _transport_closed = False

    def _write_complete(self):
        done = self._first_write_complete
        if done is None:
            with _event_init_lock:
                if self._first_write_complete is None:
                    self._first_write_complete = threading.Event()
                done = self._first_write_complete
        return done

    def sendall(self, data, flags=0):
        try:
            return super().sendall(data, flags)
        finally:
            self._write_complete().set()

    def _wait_for_write(self):
        self._write_complete().wait()
        if self._transport_closed:
            raise OSError("TLS socket closed before receive")

    def recv(self, buflen=1024, flags=0):
        self._wait_for_write()
        return super().recv(buflen, flags)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self._wait_for_write()
        return super().recv_into(buffer, nbytes, flags)

    def shutdown(self, how):
        self._transport_closed = True
        try:
            return super().shutdown(how)
        finally:
            self._write_complete().set()

    def close(self):
        self._transport_closed = True
        try:
            return super().close()
        finally:
            self._write_complete().set()


def sync_tls_context(cafile=None):
    # 仅供 websockets.sync 的 sendall/recv 传输；不改变证书或协议设置。
    context = ssl.create_default_context(cafile=cafile)
    context.sslsocket_class = WriteFirstTLSSocket
    return context
