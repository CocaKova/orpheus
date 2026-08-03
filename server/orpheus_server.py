"""Orpheus — local dictation engine.

OpenAI-compatible speech-to-text server wrapping NVIDIA Parakeet TDT,
with an optional LLM cleanup pass (filler removal, punctuation) via any
OpenAI-compatible chat endpoint running on the same box.

    POST /v1/audio/transcriptions   multipart: file=<audio> [clean=true|false]
    GET  /v1/models
    GET  /healthz

Environment:
    ORPHEUS_MODEL          NeMo model name  (default nvidia/parakeet-tdt-0.6b-v2)
    ORPHEUS_PORT           listen port      (default 8123)
    ORPHEUS_CLEAN_DEFAULT  run cleanup pass unless request overrides (default true)
    ORPHEUS_CLEAN_URL      OpenAI-compatible base URL for cleanup
                         (default http://127.0.0.1:8000/v1)
    ORPHEUS_CLEAN_MODEL    cleanup model id (default: auto-discover via /models)
    ORPHEUS_API_KEY        if set, /v1/* requires "Authorization: Bearer <key>"
    ORPHEUS_MAX_UPLOAD_MB  reject uploads larger than this (default 64)
    ORPHEUS_LOG_TEXT       log transcript previews (default false: word count only)
"""

import asyncio
import hmac
import logging
import os
import subprocess
import tempfile
import time

import httpx
from fastapi import FastAPI, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

VERSION = "1.0.0"

MODEL_NAME = os.environ.get("ORPHEUS_MODEL", "nvidia/parakeet-tdt-0.6b-v2")
CLEAN_DEFAULT = os.environ.get("ORPHEUS_CLEAN_DEFAULT", "true").lower() == "true"
CLEAN_URL = os.environ.get("ORPHEUS_CLEAN_URL", "http://127.0.0.1:8000/v1")
CLEAN_MODEL = os.environ.get("ORPHEUS_CLEAN_MODEL", "")
API_KEY = os.environ.get("ORPHEUS_API_KEY", "")
MAX_UPLOAD_BYTES = int(os.environ.get("ORPHEUS_MAX_UPLOAD_MB", "64")) * 1024 * 1024
LOG_TEXT = os.environ.get("ORPHEUS_LOG_TEXT", "false").lower() == "true"

CLEAN_PROMPT = (
    "You clean up raw speech-to-text dictation. Remove filler words (um, uh, "
    "you know, like), false starts, and stutters. Fix punctuation and "
    "capitalization. Keep the speaker's words and meaning exactly — do not "
    "summarize, expand, or answer questions in the text. If the speaker "
    "self-corrects ('meet at five, no, six'), keep only the correction. "
    "Output only the cleaned text, nothing else."
)

log = logging.getLogger("orpheus")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="orpheus", version=VERSION)
asr_model = None
gpu_lock = asyncio.Lock()
http_client: httpx.AsyncClient | None = None


@app.on_event("startup")
def load_model():
    global asr_model
    import nemo.collections.asr as nemo_asr

    t0 = time.time()
    asr_model = nemo_asr.models.ASRModel.from_pretrained(MODEL_NAME)
    asr_model.eval()
    log.info("loaded %s in %.1fs", MODEL_NAME, time.time() - t0)


@app.on_event("startup")
async def make_http_client():
    global http_client
    http_client = httpx.AsyncClient()


def require_key(authorization: str):
    if not API_KEY:
        return
    token = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token.encode(), API_KEY.encode()):
        raise HTTPException(401, "bad or missing API key")


def to_wav16k(src_path: str) -> str:
    dst = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    dst.close()
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-ac", "1", "-ar", "16000",
             "-f", "wav", dst.name],
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        os.unlink(dst.name)
        raise HTTPException(408, "audio decode timed out")
    if proc.returncode != 0:
        os.unlink(dst.name)
        raise HTTPException(400, f"audio decode failed: {proc.stderr[-300:].decode(errors='replace')}")
    return dst.name


def transcribe_sync(wav_path: str) -> str:
    out = asr_model.transcribe([wav_path], verbose=False)
    hyp = out[0]
    return hyp.text if hasattr(hyp, "text") else str(hyp)


async def cleanup_pass(text: str) -> str:
    """Polish raw dictation via the local LLM. Falls back to raw text on any error."""
    if not text.strip():
        return text
    body = {
        "model": CLEAN_MODEL or await discover_clean_model(),
        "messages": [
            {"role": "system", "content": CLEAN_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.6,
        "max_tokens": max(256, len(text)),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    try:
        r = await http_client.post(f"{CLEAN_URL}/chat/completions", json=body, timeout=30.0)
        if r.status_code == 400:
            # some backends (e.g. Mistral tokenizer mode) reject chat_template_kwargs
            body.pop("chat_template_kwargs", None)
            r = await http_client.post(f"{CLEAN_URL}/chat/completions", json=body, timeout=30.0)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        # some reasoning parsers route no-think output into the
        # reasoning field and leave content empty
        cleaned = (msg.get("content") or msg.get("reasoning") or "").strip()
        return cleaned or text
    except Exception as e:
        log.warning("cleanup pass failed, returning raw text: %s", e)
        return text


_clean_model_cache = ""


async def discover_clean_model() -> str:
    global _clean_model_cache
    if _clean_model_cache:
        return _clean_model_cache
    r = await http_client.get(f"{CLEAN_URL}/models", timeout=5.0)
    r.raise_for_status()
    _clean_model_cache = r.json()["data"][0]["id"]
    return _clean_model_cache


@app.get("/healthz")
async def healthz():
    import torch
    return {
        "status": "ok" if asr_model is not None else "loading",
        "version": VERSION,
        "model": MODEL_NAME,
        "cuda": torch.cuda.is_available(),
        "clean_url": CLEAN_URL,
    }


@app.get("/v1/models")
async def models(authorization: str = Header("")):
    require_key(authorization)
    return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile,
    model: str = Form(""),          # accepted for OpenAI compatibility, ignored
    response_format: str = Form("json"),
    clean: str = Form(""),
    authorization: str = Header(""),
):
    require_key(authorization)
    if asr_model is None:
        raise HTTPException(503, "model still loading")

    do_clean = CLEAN_DEFAULT if clean == "" else clean.lower() == "true"

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"audio larger than {MAX_UPLOAD_BYTES >> 20} MiB")
    src = tempfile.NamedTemporaryFile(suffix=os.path.splitext(file.filename or "")[1] or ".bin", delete=False)
    src.write(data)
    src.close()
    wav = None
    t0 = time.time()
    try:
        wav = await asyncio.to_thread(to_wav16k, src.name)
        async with gpu_lock:
            raw = await asyncio.to_thread(transcribe_sync, wav)
        t_asr = time.time() - t0
        text = await cleanup_pass(raw) if do_clean else raw
        t_total = time.time() - t0
        # transcripts are private — log content only when explicitly asked to
        shown = repr(text[:80]) if LOG_TEXT else f"{len(text.split())} words"
        log.info("asr %.2fs total %.2fs clean=%s: %s", t_asr, t_total, do_clean, shown)
    finally:
        os.unlink(src.name)
        if wav:
            os.unlink(wav)

    if response_format == "text":
        return PlainTextResponse(text)
    payload = {"text": text}
    if response_format == "verbose_json":
        payload.update({"raw_text": raw, "asr_seconds": round(t_asr, 3),
                        "total_seconds": round(t_total, 3), "cleaned": do_clean})
    return JSONResponse(payload)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("ORPHEUS_PORT", "8123")))
