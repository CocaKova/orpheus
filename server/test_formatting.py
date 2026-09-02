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

# --- context / style
from formatting import style_for, mid_sentence, request_hints, fit_context, parse_app_styles
eq(style_for("com.whatsapp"), "message")
eq(style_for("com.cocakova.charon"), "code")
eq(style_for("com.unknown.app"), "prose")
eq(style_for("com.whatsapp", "email"), "email")            # explicit wins
eq(style_for("com.foo", "", parse_app_styles("com.foo=code, junk, com.bar=nope")), "code")
assert mid_sentence("Running late,")
assert mid_sentence("I think that")
assert not mid_sentence("Done.")
assert not mid_sentence("Line one\n")
assert not mid_sentence("")
assert not mid_sentence("   ")
eq(request_hints("", "", "prose"), "")
h = request_hints("Running late,", "", "message")
assert h.startswith("STYLE: chat message") and "«Running late,»" in h and "mid-sentence" in h and h.endswith("DICTATION:\n"), h
assert "EXTRA TERMS" in request_hints("", "", "prose", "Keryx, Spire")
eq(fit_context("Grab me a coffee please.", "Running late,", "message"), "grab me a coffee please")
eq(fit_context("I'll be there.", "Running late,", "message"), "I'll be there")
eq(fit_context("Keryx is live.", "Also,", "prose", ["Keryx"]), "Keryx is live.")
eq(fit_context("Keryx is live.", "Also,", "prose"), "keryx is live.")   # unknown name, mid-sentence
eq(fit_context("NASA called.", "and then", "prose"), "NASA called.")
eq(fit_context("See you at 6. Bring the docs.", "", "message"), "See you at 6. Bring the docs.")  # two sentences keep the period
eq(fit_context("Git status.", "", "code"), "git status")
eq(fit_context("Ls -la\ncd /tmp.", "", "code"), "ls -la\ncd /tmp.")   # multi-line: period untouched
eq(prepass("git status.", capitalize=False), "git status.")
eq(prepass("i think so", capitalize=False), "i think so")
eq(prepass("I think so", capitalize=False), "I think so")
print("context ok")

# --- lists: spoken markers, tidy, deterministic fallback
from formatting import listify
eq(prepass("I need the following. Bullet point eggs, bullet point milk, bullet point a loaf of bread."),
   "I need the following:\n- Eggs\n- Milk\n- A loaf of bread")
eq(prepass("Things to pack, bullet point charger, bullet point passport."),
   "Things to pack:\n- Charger\n- Passport")
eq(prepass("Number one, finish the report. Number two, send it. Number three, book the flights."),
   "1. Finish the report\n2. Send it\n3. Book the flights")
eq(prepass("Goals for the week. Number one, ship it. Number two, rest."),
   "Goals for the week:\n1. Ship it\n2. Rest")
eq(prepass("add a bullet point here and the number one priority is sleep"),
   "Add a bullet point here and the number one priority is sleep")   # prose stays prose
eq(prepass("she wore number seven and bullet points are ugly"), "She wore number seven and bullet points are ugly")
eq(prepass("bullet point one, bullet point two"), "- One\n- Two")     # no lead-in: no colon invented
eq(listify("Okay so from the store I need eggs, milk, bread and cheese."),
   "Okay so from the store I need:\n- Eggs\n- Milk\n- Bread\n- Cheese")
eq(listify("Grab me a coffee, a bagel and the paper."), "Grab me:\n- A coffee\n- A bagel\n- The paper")
eq(listify("To-do list for tomorrow: call the bank, renew the plates, pick up the dry cleaning."),
   "To-do list for tomorrow:\n- Call the bank\n- Renew the plates\n- Pick up the dry cleaning")
eq(listify("For the trip we'll need sunscreen, hats, salt and pepper, and towels."),
   "For the trip we'll need:\n- Sunscreen\n- Hats\n- Salt and pepper\n- Towels")   # inner "and" kept
eq(listify("I need eggs and milk."), "I need eggs and milk.")                        # two things = sentence
eq(listify("I need you to call mom, walk the dog, and buy milk."), "I need you to call mom, walk the dog, and buy milk.")
eq(listify("We tested eggs, milk and bread and all three were fine."), "We tested eggs, milk and bread and all three were fine.")
eq(listify("I need eggs, milk, bread. Also call mom."), "I need eggs, milk, bread. Also call mom.")   # two sentences
eq(listify("When you get home, take out the trash, feed the cat."), "When you get home, take out the trash, feed the cat.")
eq(listify("- Eggs\n- Milk"), "- Eggs\n- Milk")                                       # already a list
eq(listify(""), "")
ok, _ = guard("first back up the database then run the migration and finally restart the gateway",
              "1. Back up the database\n2. Run the migration\n3. Restart the gateway")
assert ok, "sequence words may become numbering"
ok, _ = guard("step one open the app step two tap the orb step three speak step four stop",
              "1. Open the app\n2. Tap the orb\n3. Speak\n4. Stop")
assert ok
print("list tests pass")

# --- recognizer-written inline numbering
eq(prepass("My top goals this week are 1. Finish the report 2. Send the presentation 3. Book the flights."),
   "My top goals this week are:\n1. Finish the report\n2. Send the presentation\n3. Book the flights")
eq(prepass("1. Back up 2. Migrate 3. Restart."), "1. Back up\n2. Migrate\n3. Restart")
eq(prepass("We moved to Python 3. Restart the gateway after."), "We moved to Python 3. Restart the gateway after.")
eq(prepass("Chapter 2. Then chapter 4. Done."), "Chapter 2. Then chapter 4. Done.")   # not a 1-2-3 run
print("inline numbering tests pass")
