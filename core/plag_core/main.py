"""Entry point: `python -m plag_core` (the desktop shell sets PLAG_PORT, PLAG_TOKEN, PLAG_PARENT_PID)."""

import logging
import os
import secrets


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    port = int(os.environ.get("PLAG_PORT") or 8765)
    if not os.environ.get("PLAG_TOKEN"):
        # standalone dev run: make an ephemeral token so the API is never open
        os.environ["PLAG_TOKEN"] = secrets.token_hex(32)
        print(f"PLAG core dev token: {os.environ['PLAG_TOKEN']}", flush=True)

    import uvicorn

    from .api import app

    print(f"PLAG core listening on 127.0.0.1:{port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", ws_ping_interval=20, ws_ping_timeout=20)
