"""
Process launcher for hosting (e.g. Railway).

Runs BOTH the Telegram bot and the Streamlit dashboard in one container, and
restarts either one if it crashes. Streamlit binds to the platform-provided
$PORT so the dashboard is reachable at your public URL; the bot needs no port
(it long-polls Telegram outbound).

Locally you don't need this — run `python bot.py` and `streamlit run dashboard.py`
in separate terminals. This file is for the cloud.
"""

import os
import subprocess
import sys
import threading
import time


def _supervise(name: str, cmd: list[str]):
    """Run a command forever, restarting it a few seconds after any exit."""
    while True:
        print(f"[launcher] starting {name}: {' '.join(cmd)}", flush=True)
        try:
            subprocess.run(cmd)
        except Exception as exc:  # noqa: BLE001
            print(f"[launcher] {name} raised {exc!r}", flush=True)
        print(f"[launcher] {name} exited — restarting in 5s", flush=True)
        time.sleep(5)


def main():
    # Bot runs in a background thread (it blocks on its own long-poll loop).
    threading.Thread(
        target=_supervise,
        args=("bot", [sys.executable, "bot.py"]),
        daemon=True,
    ).start()

    # Streamlit runs in the foreground as the container's main web process.
    port = os.environ.get("PORT", "8501")
    dashboard_cmd = [
        sys.executable, "-m", "streamlit", "run", "dashboard.py",
        "--server.port", port,
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false",
    ]
    _supervise("dashboard", dashboard_cmd)


if __name__ == "__main__":
    main()
