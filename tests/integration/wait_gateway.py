"""Wait for the complete TLS/proxy path, beyond the L4 TCP healthcheck."""
import os
import time

import requests

base = os.environ.get("BASE_URL", "https://gateway")
ca = os.environ.get("CA_BUNDLE", "/certs/ca.crt")
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        response = requests.get(base + "/health", verify=ca, timeout=3)
        if response.status_code == 200 and os.environ.get("BUFFER_CONTROL_TOKEN"):
            for instance in (1, 2):
                control = requests.post(f"http://app-{instance}:8000/internal/buffer/status", json={},
                    headers={"Authorization": "Bearer " + os.environ["BUFFER_CONTROL_TOKEN"]}, timeout=2)
                status = control.json()
                if control.status_code != 200 or status.get("mode") == "paused" or status.get("queue", {}).get("mode") != "direct":
                    break
            else:
                print("HTTPS gateway and request permits ready")
                break
            time.sleep(.1)
            continue
        if response.status_code == 200:
            print("HTTPS gateway ready with certificate verification")
            break
    except requests.RequestException:
        pass
    time.sleep(0.1)
else:
    raise SystemExit("HTTPS gateway did not become ready within 30 seconds")
