"""Word timings from a NeMo hypothesis, in OpenAI's verbose_json shape.

Asked for with ``timestamp_granularities[]=word`` and ``response_format=verbose_json``; every
other request takes the plain path, so dictation never pays for them. The words are the raw ASR
words (before the formatting pre-pass and the cleanup pass), which is what a client measuring
speech wants.
"""

from __future__ import annotations

# Parakeet / FastConformer: 10 ms feature hop x 8x subsampling = one encoder frame per 80 ms.
# Only used when a NeMo version reports frame offsets without seconds.
FRAME_SECONDS = 0.08


def word_timings(timestamp: dict | None, frame_seconds: float = FRAME_SECONDS) -> list[dict]:
    """``hyp.timestamp`` -> ``[{"word", "start", "end"}]`` in seconds, in spoken order."""
    if not timestamp:
        return []
    out = []
    for w in timestamp.get("word") or []:
        word = str(w.get("word") or "").strip()
        if not word:
            continue
        start, end = w.get("start"), w.get("end")
        if start is None and w.get("start_offset") is not None:
            start = w["start_offset"] * frame_seconds
        if end is None and w.get("end_offset") is not None:
            end = w["end_offset"] * frame_seconds
        if start is None or end is None:
            continue
        start = float(start)
        out.append({"word": word, "start": round(start, 3), "end": round(max(float(end), start), 3)})
    return out
