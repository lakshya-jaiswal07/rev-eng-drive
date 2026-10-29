"""FastAPI surface: the Drive websocket plus a little metadata."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import settings
from .drive.runtime import DriveRuntime
from .providers import build_provider
from .schemas import ErrorFrame, ServerFrame, Stage, StageFrame

app = FastAPI(
    title="Interject Drive",
    description="An in-car voice assistant you can talk over.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) + ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    # Same precedence as providers.build_provider().
    if settings.use_gemini:
        provider, model = "gemini", settings.gemini_model
    elif settings.use_real_llm:
        provider, model = "openai-compatible", settings.llm_model
    else:
        provider, model = "local-deterministic", "deterministic"
    backup = settings.llm_fallback_model if provider == "openai-compatible" and settings.llm_fallback_api_key else None
    return {"status": "ok", "provider": provider, "model": model, "fallback_model": backup}


@app.websocket("/ws/drive")
async def drive_socket(socket: WebSocket) -> None:
    """The in-car voice assistant."""
    await socket.accept()
    send_lock = asyncio.Lock()
    closed = False

    async def emit(frame: ServerFrame) -> None:
        nonlocal closed
        if closed:
            return
        try:
            async with send_lock:
                await socket.send_text(frame.model_dump_json())
        except (WebSocketDisconnect, RuntimeError):
            closed = True

    provider = build_provider()  # only consulted for questions outside driving
    runtime = DriveRuntime(emit, provider=provider)
    await socket.send_text(json.dumps({"t": "ready", "mode": "drive", "provider": provider.name,
                                       "origin": runtime.origin.label}))
    await runtime.start()
    try:
        while True:
            raw = await socket.receive_text()
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                await emit(ErrorFrame(message="Malformed frame ignored."))
                continue
            kind = event.get("t")
            if kind == "interrupt":  # handled first: a barge-in never waits behind a turn
                heard = event.get("heard_chars")
                await runtime.interrupt(int(heard) if isinstance(heard, (int, float)) else None)
            elif kind == "partial":
                await runtime.on_partial(str(event.get("text", "")))
            elif kind == "final":
                await runtime.on_final(str(event.get("text", "")), modality=str(event.get("modality", "voice")))
            elif kind == "resume":
                await runtime.on_final("go on", modality="text")
            elif kind == "ping":
                await emit(StageFrame(stage=Stage.IDLE, detail="pong"))
            else:
                await emit(ErrorFrame(message=f"Unknown frame {kind!r}."))
    except WebSocketDisconnect:
        pass
    finally:
        closed = True
        await runtime.shutdown()


# -- the built web app (a deployment) --------------------------------------------
# In development Vite serves the pages and proxies /api and /ws here. Deployed,
# this process serves the built app too (``npm run build`` -> web/dist), so
# pages, API and sockets share one address. Registered last: every /api and
# /ws route above wins.
def web_dist() -> Path:
    return Path(os.environ.get("WEB_DIST") or Path(__file__).resolve().parents[2] / "web" / "dist")


@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
async def web_app(path: str) -> FileResponse:
    root = web_dist().resolve()
    index = root / "index.html"
    if path == "api" or path.startswith(("api/", "ws/")) or path == "ws" or not index.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    target = (root / path).resolve()
    if path and target.is_file() and target.is_relative_to(root):
        # file names under assets/ carry a content hash: safe to cache for good
        immutable = target.parent.name == "assets"
        return FileResponse(target, headers={"Cache-Control": "public, max-age=31536000, immutable"} if immutable else None)
    # any other path is a page of the app
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
