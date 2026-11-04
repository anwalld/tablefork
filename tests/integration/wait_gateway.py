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
        if response.status_code == 200:
            print("HTTPS gateway ready with certificate verification")
            break
    except requests.RequestException:
        pass
    time.sleep(0.1)
else:
    raise SystemExit("HTTPS gateway did not become ready within 30 seconds")
