#!/usr/bin/env python3
"""Start both local demo processes, monitor failures, and clean up on Ctrl+C."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def port_available(port):
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main():
    if not all(port_available(port) for port in (8000, 8501)):
        print(
            "Port 8000 or 8501 is in use. Stop the existing demo first, or use the separate-process commands in README."
        )
        return 1
    processes = []

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        api = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
                "--no-access-log",
            ],
            cwd=ROOT,
        )
        processes.append(api)
        for _ in range(80):
            if api.poll() is not None:
                raise RuntimeError("Backend failed to start; see its error above.")
            try:
                with urlopen("http://127.0.0.1:8000/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (URLError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError("Backend did not become ready.")
        env = {**os.environ, "API_URL": "http://127.0.0.1:8000"}
        ui = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                "ui/streamlit_app.py",
                "--server.address",
                "127.0.0.1",
                "--server.port",
                "8501",
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            cwd=ROOT,
            env=env,
        )
        processes.append(ui)
        print(
            "\nSupportDesk AI: http://127.0.0.1:8501\nAPI explorer: http://127.0.0.1:8000/docs\nPress Ctrl+C to stop both processes.\n",
            flush=True,
        )
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError("One demo process exited; stopping the other.")
    except KeyboardInterrupt:
        print("\nStopping demo.")
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
