"""Run: python test_formatting.py  (no pytest needed)"""
from formatting import prepass, guard, build_prompt

def eq(got, want):
    assert got == want, f"\n got: {got!r}\nwant: {want!r}"

# --- prepass
eq(prepass("call get user open paren user id close paren. New line, thanks exclamation point."),
   "Call get user (user id)\nThanks!")
eq(prepass("First item, new line, second item, new paragraph. Um, meet at six. Question mark."),
   "First item\nSecond item\n\nUm, meet at six?")
eq(prepass("Open the file at slash home slash jon slash workspace."), "Open the file at /home/jon/workspace.")
eq(prepass("Run npm dash dash version."), "Run npm --version.")
eq(prepass("Hey Mom Comma, the show. Semicolon, I'll send it. Smiley Face."), "Hey Mom, the show; I'll send it. 🙂")
eq(prepass("quote this is a test end quote"), '"this is a test"')
eq(prepass("local peer underscore test"), "Local peer_test")
eq(prepass("over a long period of time it grew. Period."), "Over a long period of time it grew.")
eq(prepass("email me at sign jon"), "Email me@jon")           # unambiguous "at sign" only
eq(prepass("meet me at the park"), "Meet me at the park")       # bare "at" untouched
eq(prepass("it costs ten percent sign more"), "It costs ten% more")
eq(prepass(""), "")

# --- guard
ok, _ = guard("Send it to Jonny, then call get user (user id)\nThanks!", "Send it to Jonny, then call get_user(user_id).\nThanks!")
assert ok
ok, why = guard("Send it to Jonny, then call get user (user id)\nThanks!", "Send it to Jonny, then call get_user(user_id).")
assert not ok and "trailing" in why, why
ok, _ = guard("Um, so I think we should meet at five, no, six.", "I think we should meet at 6.")
assert ok  # fillers, numbers and self-correction are allowed to vanish
ok, why = guard("the quick brown fox jumps over the lazy dog near the river bank today", "the fox today")
assert not ok and "content words" in why, why
ok, _ = guard("hashtag local peer is live tonight", "#localpeer is live tonight")
assert ok  # joined tokens count as kept

# --- prompt
assert "DICTIONARY" not in build_prompt("")
assert "Keryx" in build_prompt("Jonny, Keryx")
print("base formatting tests pass")

# --- respellings and pause-anchored comma/colon (added after live run 09-01)
ok, _ = guard("Talk soon. Dash Johnny.", "Talk soon.\n- Jonny")
assert ok, "dictionary respelling must not count as a dropped word"
ok, _ = guard("closed on Mondays.", "closed on Monday.")
assert ok
ok, why = guard("Talk soon. Dash Johnny.", "Talk soon.")
assert not ok, "a genuinely dropped trailing word is still caught"
eq(prepass("Hey Mom Comma, the show is on."), "Hey Mom, the show is on.")
eq(prepass("Grocery list colon. First eggs."), "Grocery list: First eggs.")
eq(prepass("put a comma between the names"), "Put a comma between the names")
print("respelling + comma/colon tests pass")

# --- scratch that + dictionary aliases
from formatting import parse_dictionary
eq(prepass("Also pick up the dry cleaning. Scratch that the dry cleaning is closed on Mondays."),
   "The dry cleaning is closed on Mondays.")
eq(prepass("Also pick up the dry cleaning, scratch that, the cleaners are closed on Mondays."),
   "The cleaners are closed on Mondays.")
eq(prepass("Meet at five. Scratch that. Meet at six."), "Meet at six.")
words, aliases = parse_dictionary("Jonny, Keryx, Johnny=Jonny, Currics=Keryx, ,")
assert words == ["Jonny", "Keryx"], words
eq(prepass("Dash Johnny. See docs dot currics dot app.", aliases), "Dash Jonny. See docs dot Keryx dot app.")
assert "Johnny" not in build_prompt("Johnny=Jonny") and "Jonny" in build_prompt("Johnny=Jonny")
print("scratch-that + alias tests pass")
