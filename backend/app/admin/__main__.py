"""Start the API with the admin console on this PC and open it in your browser.

    uv run python -m app.admin            # http://127.0.0.1:9000/admin
    uv run python -m app.admin --port 9001

If the API is already running on that port, this just opens the console.
"""

import argparse
import threading
import urllib.request
import webbrowser

import uvicorn


def api_is_up(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as resp:
            return resp.status in (200, 503)
    except OSError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"

    if api_is_up(base):
        print(f"API already running: opening {base}/admin")
        webbrowser.open(f"{base}/admin")
        return
    if not args.no_browser:
        threading.Timer(2.0, webbrowser.open, args=[f"{base}/admin"]).start()
    print(f"Admin console: {base}/admin  (this PC only; Ctrl+C to stop)")
    # Loopback only: the console must never be reachable from the network.
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
