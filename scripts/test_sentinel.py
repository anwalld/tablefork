#!/usr/bin/env python3
"""Run real Sentinel identity regressions without network or business volumes."""
import argparse
import json
import subprocess
import time
import uuid


def run(image, expect_vulnerable=False):
    name = "cppai-sentinel-identity-" + uuid.uuid4().hex[:12]

    def docker(*args, input=None):
        return subprocess.check_output(["docker", *args], input=input, text=True,
                                       stderr=subprocess.STDOUT, timeout=30).strip()

    def cli(port, *args):
        reply = docker("exec", name, "redis-cli", "--json", "-p", str(port), *args)
        return None if args[0] == "SHUTDOWN" and not reply else json.loads(reply)

    def write(path, value):
        docker("exec", "-i", name, "sh", "-c", 'cat > "$1"', "sh", path, input=value)

    ids = [str(i) * 40 for i in (1, 2, 3)]
    ports = [26379, 26380, 26381]

    def config(index, corrupt=False):
        port = ports[index]
        text = (f"port {port}\nbind 127.0.0.1\ndir /data\n"
                "sentinel monitor chat 127.0.0.1 6379 2\n"
                "sentinel down-after-milliseconds chat 60000\n"
                "sentinel config-epoch chat 7\nsentinel current-epoch 11\n"
                "sentinel leader-epoch chat 5\n")
        if corrupt:
            # Put the stale self entry before MYID to exercise post-load cleanup.
            text += f"sentinel known-sentinel chat 127.0.0.1 {port} {ids[index]}\n"
            text += f"sentinel known-sentinel chat 127.0.0.1 {port} {ids[(index + 1) % 3]}\n"
        return text + f"sentinel myid {ids[index]}\n"

    def start(index):
        docker("exec", name, "redis-sentinel", f"/data/s{index}.conf", "--daemonize", "yes",
               "--logfile", f"/data/s{index}.log", "--pidfile", f"/data/s{index}.pid")
        assert cli(ports[index], "PING") == "PONG"

    def peers(port):
        return cli(port, "SENTINEL", "SENTINELS", "chat")

    def peer_ids(port):
        return {row["runid"] for row in peers(port)}

    def healthy():
        for index, port in enumerate(ports):
            actual = peer_ids(port)
            assert ids[index] not in actual, f"self peer at {port}: {actual}"
            if actual != set(ids) - {ids[index]}:
                return False
            if any("disconnected" in row["flags"] or "s_down" in row["flags"]
                   for row in peers(port)):
                return False
        return True

    def wait_healthy():
        deadline = time.monotonic() + 30
        stable_since = None
        while time.monotonic() < deadline:
            # Address switching reconnects links. Require a stable three-second
            # window within the same bound; a self ID fails immediately.
            if healthy():
                stable_since = stable_since or time.monotonic()
                if time.monotonic() - stable_since >= 3:
                    break
            else:
                stable_since = None
            time.sleep(0.1)
        else:
            raise AssertionError("three Sentinels did not stabilize with exactly two real peers")
        for index, port in enumerate(ports):
            assert cli(port, "SENTINEL", "MYID") == ids[index]
            assert cli(port, "SENTINEL", "MASTER", "chat")["config-epoch"] == "7"
            assert cli(port, "SENTINEL", "CKQUORUM", "chat").startswith("OK 3 usable")

    try:
        docker("run", "-d", "--name", name, "--network", "none", "--entrypoint", "sleep", image, "infinity")
        docker("exec", name, "redis-server", "--port", "6379", "--bind", "127.0.0.1", "--daemonize", "yes")
        write("/data/s0.conf", config(0))
        start(0)
        hello = f"127.0.0.1,{ports[0]},{ids[0]},11,chat,127.0.0.1,6379,7"
        assert cli(ports[0], "PUBLISH", "__sentinel__:hello", hello) == 1
        if expect_vulnerable:
            assert ids[0] in peer_ids(ports[0]), "baseline no longer reproduces direct self HELLO"
            print("baseline reproduced: direct HELLO registers MYID as a peer", flush=True)
            # Isolate the naturally generated HELLO path from the direct injection.
            cli(ports[0], "SHUTDOWN", "NOSAVE")
            write("/data/s0.conf", config(0, corrupt=True).replace(
                f"sentinel known-sentinel chat 127.0.0.1 {ports[0]} {ids[0]}\n", ""))
            start(0)
            deadline = time.monotonic() + 10
            while ids[0] not in peer_ids(ports[0]):
                assert time.monotonic() < deadline, "baseline stale-address self connection not reproduced"
                time.sleep(0.1)
            print("baseline reproduced: stale peer address loops HELLO back and persists self", flush=True)
            return
        assert not peer_ids(ports[0]), "direct own HELLO must be ignored"
        foreign = hello.replace(ids[0], ids[1]).replace(str(ports[0]), str(ports[1]), 1)
        assert cli(ports[0], "PUBLISH", "__sentinel__:hello", foreign) == 1
        assert peer_ids(ports[0]) == {ids[1]}, "foreign HELLO must still be accepted"
        cli(ports[0], "SHUTDOWN", "NOSAVE")
        for index in range(3):
            write(f"/data/s{index}.conf", config(index, corrupt=True))
        start(0)
        assert ids[0] not in peer_ids(ports[0]), "persisted own peer must be removed on startup"
        saved = docker("exec", name, "cat", "/data/s0.conf")
        assert f" {ids[0]}" not in "\n".join(line for line in saved.splitlines()
                                                  if "known-sentinel" in line)
        assert "sentinel current-epoch 11" in saved, "do not reset election state"
        assert "sentinel leader-epoch chat 5" in saved, "do not reset votes"
        start(1)
        start(2)
        wait_healthy()
        print("guard passed: own/foreign HELLO, persisted self cleanup, exactly two peers", flush=True)
        for port in ports:
            cli(port, "SHUTDOWN", "NOSAVE")
        # Reuse each old listener address for another persistent Sentinel ID.
        ports[:] = ports[1:] + ports[:1]
        for index in range(3):
            path = f"/data/s{index}.conf"
            saved = docker("exec", name, "cat", path)
            saved = "\n".join(f"port {ports[index]}" if line.startswith("port ") else line
                              for line in saved.splitlines()) + "\n"
            write(path, saved)
        for index in range(3):
            start(index)
        wait_healthy()
        print("guard passed: reused addresses + restart preserve IDs/epochs/quorum, no self peers", flush=True)
    finally:
        docker("rm", "-f", name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="cppaiserver-sentinel:7.4.5-self-guard")
    parser.add_argument("--expect-vulnerable", action="store_true")
    options = parser.parse_args()
    run(options.image, options.expect_vulnerable)
