"""Plain-python checks for timestamps.word_timings (run: python test_timestamps.py)."""

from timestamps import word_timings

# NeMo 2.x reports seconds directly
got = word_timings({"word": [
    {"word": "Um,", "start_offset": 3, "end_offset": 5, "start": 0.24, "end": 0.4},
    {"word": "hello", "start_offset": 6, "end_offset": 9, "start": 0.48, "end": 0.72},
]})
assert got == [{"word": "Um,", "start": 0.24, "end": 0.4}, {"word": "hello", "start": 0.48, "end": 0.72}], got

# offsets only: 80 ms frames
got = word_timings({"word": [{"word": "plain", "start_offset": 10, "end_offset": 15}]})
assert got == [{"word": "plain", "start": 0.8, "end": 1.2}], got

# empty, missing and malformed entries are dropped, never raised
assert word_timings(None) == []
assert word_timings({}) == []
assert word_timings({"word": [{"word": "", "start": 0, "end": 1}, {"word": "x"}]}) == []

# an end before its start is clamped, not reversed
assert word_timings({"word": [{"word": "a", "start": 1.0, "end": 0.9}]}) == [{"word": "a", "start": 1.0, "end": 1.0}]

print("timestamps: ok")
