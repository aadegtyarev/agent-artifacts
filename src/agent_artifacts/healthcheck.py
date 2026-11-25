"""Probe the local app with the public authority required in Workspace mode."""

import http.client
import os
from urllib.parse import urlsplit


def main() -> None:
    public_url = os.environ.get("AA_PUBLIC_URL", "http://127.0.0.1:8090")
    authority = urlsplit(public_url).netloc
    if not authority:
        raise ValueError("AA_PUBLIC_URL must include an authority")
    conn = http.client.HTTPConnection("127.0.0.1", 8090, timeout=3)
    try:
        conn.request("GET", "/health", headers={"Host": authority})
        if conn.getresponse().status != 200:
            raise RuntimeError("health check failed")
    finally:
        conn.close()
    print("ok")


if __name__ == "__main__":
    main()
