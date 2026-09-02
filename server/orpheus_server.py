"""Orpheus — local dictation engine.

OpenAI-compatible speech-to-text server wrapping NVIDIA Parakeet TDT,
with a deterministic spoken-symbol pre-pass ("open paren", "new line",
"exclamation point" -> symbols) and an optional LLM cleanup pass (fillers,
punctuation, casing, code/paths/numbers, personal dictionary) via any
OpenAI-compatible chat endpoint running on the same box. A content guard
rejects LLM output that dropped what the speaker said and falls back to the
pre-passed text.

    POST /v1/audio/transcriptions   multipart: file=<audio> [clean=true|false]
                                    [context_before=<text left of cursor>]
                                    [context_after=<text right of cursor>]
                                    [app=<package/app id>] [style=auto|prose|message|email|code]
                                    [prompt=<extra names/terms, OpenAI-style spelling hint>]
    GET  /v1/models
    GET  /healthz

Environment:
    ORPHEUS_MODEL          NeMo model name  (default nvidia/parakeet-tdt-0.6b-v2)
    ORPHEUS_PORT           listen port      (default 8123)
    ORPHEUS_CLEAN_DEFAULT  run cleanup pass unless request overrides (default true)
    ORPHEUS_CLEAN_URL      OpenAI-compatible base URL for cleanup
                         (default http://127.0.0.1:8000/v1)
    ORPHEUS_CLEAN_MODEL    cleanup model id (default: auto-discover via /models)
    ORPHEUS_CLEAN_TEMP     cleanup sampling temperature (default 0.2)
    ORPHEUS_DICTIONARY     comma-separated names/terms the LLM should spell
                         correctly; "heard=meant" pairs are fixed before the
                         LLM ("Jonny, Keryx, Johnny=Jonny, Currics=Keryx")
    ORPHEUS_APP_STYLES     "pkg=style, pkg=style" additions to the built-in
                         app -> style map (message / email / code / prose)
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
import formatting
from fastapi import FastAPI, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

VERSION = "1.1.0"

MODEL_NAME = os.environ.get("ORPHEUS_MODEL", "nvidia/parakeet-tdt-0.6b-v2")
CLEAN_DEFAULT = os.environ.get("ORPHEUS_CLEAN_DEFAULT", "true").lower() == "true"
CLEAN_URL = os.environ.get("ORPHEUS_CLEAN_URL", "http://127.0.0.1:8000/v1")
CLEAN_MODEL = os.environ.get("ORPHEUS_CLEAN_MODEL", "")
CLEAN_TEMP = float(os.environ.get("ORPHEUS_CLEAN_TEMP", "0.2"))
DICTIONARY = os.environ.get("ORPHEUS_DICTIONARY", "")
API_KEY = os.environ.get("ORPHEUS_API_KEY", "")
MAX_UPLOAD_BYTES = int(os.environ.get("ORPHEUS_MAX_UPLOAD_MB", "64")) * 1024 * 1024
LOG_TEXT = os.environ.get("ORPHEUS_LOG_TEXT", "false").lower() == "true"

CLEAN_PROMPT = formatting.build_prompt(DICTIONARY)
DICT_WORDS, DICT_ALIASES = formatting.parse_dictionary(DICTIONARY)
APP_STYLES = formatting.parse_app_styles(os.environ.get("ORPHEUS_APP_STYLES", ""))

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


async def cleanup_pass(text: str, hints: str = "") -> tuple[str, str]:
    """Polish pre-passed dictation via the local LLM.

    ``hints`` are the STYLE / CONTEXT lines from formatting.request_hints,
    sent ahead of the dictation. Returns (text, guard_note). Falls back to
    the input on any error, and when the model's answer fails the content
    guard twice.
    """
    if not text.strip():
        return text, ""
    body = {
        "model": CLEAN_MODEL or await discover_clean_model(),
        "messages": [
            {"role": "system", "content": CLEAN_PROMPT},
            {"role": "user", "content": hints + text},
        ],
        "temperature": CLEAN_TEMP,
        "max_tokens": max(256, len(text)),
        "chat_template_kwargs": {"enable_thinking": False},
    }
    note = ""
    try:
        for attempt in (1, 2):
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
            if not cleaned:
                note = "empty answer"
                continue
            ok, why = formatting.guard(text, cleaned)
            if ok:
                return cleaned, ""
            note = why
            log.warning("cleanup attempt %d rejected (%s)", attempt, why)
        log.warning("cleanup rejected twice, returning pre-passed text")
        return text, f"fallback: {note}"
    except Exception as e:
        log.warning("cleanup pass failed, returning pre-passed text: %s", e)
        return text, f"fallback: {e.__class__.__name__}"


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
        "dictionary": bool(DICTIONARY),
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
    context_before: str = Form(""),  # text left of the cursor in the target field
    context_after: str = Form(""),   # text right of the cursor
    app: str = Form(""),             # target app package / id
    style: str = Form(""),           # auto | prose | message | email | code
    prompt: str = Form(""),          # OpenAI-style spelling hint: extra names/terms
    authorization: str = Header(""),
):
    require_key(authorization)
    if asr_model is None:
        raise HTTPException(503, "model still loading")

    do_clean = CLEAN_DEFAULT if clean == "" else clean.lower() == "true"
    style = formatting.style_for(app, style, APP_STYLES)
    extra_words, extra_aliases = formatting.parse_dictionary(prompt[:2000])
    context_before = context_before[-2000:]
    context_after = context_after[:500]
    hints = formatting.request_hints(context_before, context_after, style, ", ".join(extra_words))

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
        capitalize = style != "code" and not formatting.mid_sentence(context_before)
        pre = formatting.prepass(raw, list(DICT_ALIASES) + extra_aliases, capitalize)
        guard_note = ""
        if do_clean:
            text, guard_note = await cleanup_pass(pre, hints)
        else:
            text = pre
        text = formatting.fit_context(text, context_before, style, DICT_WORDS + extra_words)
        t_total = time.time() - t0
        # transcripts are private — log content only when explicitly asked to
        shown = repr(text[:80]) if LOG_TEXT else f"{len(text.split())} words"
        log.info("asr %.2fs total %.2fs clean=%s style=%s%s%s: %s", t_asr, t_total, do_clean,
                 style, f" app={app}" if app else "",
                 f" ({guard_note})" if guard_note else "", shown)
    finally:
        os.unlink(src.name)
        if wav:
            os.unlink(wav)

    if response_format == "text":
        return PlainTextResponse(text)
    payload = {"text": text}
    if response_format == "verbose_json":
        payload.update({"raw_text": raw, "pre_text": pre, "asr_seconds": round(t_asr, 3),
                        "total_seconds": round(t_total, 3), "cleaned": do_clean,
                        "guard": guard_note, "style": style})
    return JSONResponse(payload)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("ORPHEUS_PORT", "8123")))
