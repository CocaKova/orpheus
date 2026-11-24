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
ok, why = guard("Karina is out at Mexico today, remember?", "Out at Mexico today, remember?")
assert not ok and "leading" in why, why  # a clipped opening is a loss too
ok, _ = guard("Um, Karina is out at Mexico today.", "Karina is out at Mexico today.")
assert ok  # a dropped filler in front is not

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
# a list typed one entry per line (the phone's dictionary field takes line breaks)
words, aliases = parse_dictionary("Carix=Keryx\nTellaria=Talaria\r\nCharon;  Sharon=Charon\n\n")
assert words == ["Keryx", "Talaria", "Charon"], words
eq(prepass("I am working on Carix today, and Sharon and Tellaria are next.", aliases),
   "I am working on Keryx today, and Charon and Talaria are next.")
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

from formatting import restore_terminal

# --- the mark the speaker ended on survives the cleanup model
eq(restore_terminal("What time does the meeting start?", "What time does the meeting start"),
   "What time does the meeting start?")
eq(restore_terminal("That's amazing!", "That's amazing"), "That's amazing!")
eq(restore_terminal("Did the restart finish?", "Did the restart finish."),
   "Did the restart finish?")                                   # ? beats the model's period
eq(restore_terminal('He said "ship it"?', 'He said "ship it"'), 'He said "ship it"?')
eq(restore_terminal("Ship it.", "Ship it?"), "Ship it?")         # the model heard a question: its call
eq(restore_terminal("Ship it.", "Ship it."), "Ship it.")
eq(restore_terminal("Ship it", "Ship it"), "Ship it")            # nothing to restore
eq(restore_terminal("From the store I need eggs, milk and bread.",
                    "From the store I need:\n- Eggs\n- Milk\n- Bread"),
   "From the store I need:\n- Eggs\n- Milk\n- Bread")            # list items end bare
eq(restore_terminal("Here is what I need.", "Here is what I need:"),
   "Here is what I need:")                                       # a lead-in stays open
eq(restore_terminal("Hey?", ""), "")
# and the message style still drops a plain trailing period, but never a question
eq(fit_context(restore_terminal("Are you around?", "Are you around"), "", "message"),
   "Are you around?")
eq(fit_context(restore_terminal("I'm on my way.", "I'm on my way."), "", "message"),
   "I'm on my way")
print("terminal punctuation tests pass")

# --- the chat-app period convention is a preference
eq(fit_context("I'm on my way.", "", "message"), "I'm on my way")
eq(fit_context("I'm on my way.", "", "message", keep_period=True), "I'm on my way.")
eq(fit_context("Are you around?", "", "message", keep_period=True), "Are you around?")
eq(fit_context("git status.", "", "code", keep_period=True), "git status")   # code is unaffected
print("trailing period preference tests pass")

# --- spoken self-correction
# The correction is resolved by prepass(), not by the cleanup model: the text
# has to be right when the model is down, and the guard has to see the
# corrected text as the source of truth.
eq(prepass("Let's meet Tuesday, oh wait no, Wednesday at 3."),
   "Let's meet Wednesday at 3.")
eq(prepass("Grab eggs and milk, no, oat milk."), "Grab eggs and oat milk.")
eq(prepass("Send the invoice to John. Never mind that last part, send it to Mary."),
   "Send it to Mary.")
eq(prepass("Meet at five, no, six."), "Meet at six.")
eq(prepass("Book the red one, actually, the blue one."), "Book the blue one.")
eq(prepass("Send the invoice to John, no, to Mary."), "Send the invoice to Mary.")
eq(prepass("Let's meet Tuesday at 3, no, Wednesday at 4."),
   "Let's meet Wednesday at 4.")
eq(prepass("The meeting is Monday morning, no, Tuesday morning."),
   "The meeting is Tuesday morning.")
eq(prepass("Let's meet Tuesday, change that to Wednesday at 3."),
   "Let's meet Wednesday at 3.")
eq(prepass("Call the bank, get cash, never mind that last part."), "Call the bank")
eq(prepass("Call the bank, get cash, never mind that last part, get a receipt."),
   "Call the bank, get a receipt.")
eq(prepass("Book the flight for June 5th, no, June 6th at 4."),
   "Book the flight for June 6th at 4.")
eq(prepass("Meet at 3 on Tuesday, no, 4 on Wednesday."),
   "Meet at 4 on Wednesday.")
eq(prepass("Meet on Tuesday at 3, no, Wednesday at 4."),
   "Meet on Wednesday at 4.")
eq(prepass("Email a@b.com, no, c@d.com."), "Email c@d.com.")
eq(prepass("Use https://old.com, no, https://new.com."), "Use https://new.com.")
eq(prepass("Send it to John, no, to Mary."), "Send it to Mary.")
eq(prepass("Meet at 3, no, at 4."), "Meet at 4.")
print("self-correction basics pass")

# ordinary speech that happens to contain a cue word is not a correction
eq(prepass("No, I don't think so."), "No, I don't think so.")
eq(prepass("Wait for me at the door."), "Wait for me at the door.")
eq(prepass("I mean it."), "I mean it.")
eq(prepass("There is no one here."), "There is no one here.")
eq(prepass("He said no, and I believed him."), "He said no, and I believed him.")
eq(prepass("There's no, like, real evidence."), "There's no, like, real evidence.")
eq(prepass("I'm sorry, no, I can't."), "I'm sorry, no, I can't.")
eq(prepass("I need you to make that call."), "I need you to make that call.")
eq(prepass("Can you change that tire?"), "Can you change that tire?")
eq(prepass("Actually, the meeting is at 3."), "Actually, the meeting is at 3.")
eq(prepass("I'm sorry, I didn't hear you."), "I'm sorry, I didn't hear you.")
eq(prepass("No, not at all."), "No, not at all.")
eq(prepass("There's no, uh, reason to worry."), "There's no, uh, reason to worry.")
eq(prepass("I'm sorry, sometimes I forget."), "I'm sorry, sometimes I forget.")
eq(prepass("He said no, then left."), "He said no, then left.")
eq(prepass("I said no, not maybe."), "I said no, not maybe.")
eq(prepass("It's fine, actually, I like it."), "It's fine, actually, I like it.")
eq(prepass("I can wait, no problem."), "I can wait, no problem.")
eq(prepass("Well, no, that's not right."), "Well, no, that's not right.")
print("self-correction non-corrections pass")

# more than one correction in a single dictation
eq(prepass("Let's meet Tuesday, oh wait no, Wednesday at 3. Actually, make that 4."),
   "Let's meet Wednesday at 4.")
eq(prepass("Call Sarah. No, call Mark. Meet at five, no, six."),
   "Call Mark. Meet at six.")
eq(prepass("Send it Monday, no, Tuesday, no, Wednesday."), "Send it Wednesday.")
print("multiple self-corrections pass")

# the recognizer does not always put a pause before the cue
eq(prepass("Let's meet Tuesday oh wait no Wednesday at 3"),
   "Let's meet Wednesday at 3")
eq(prepass("Grab eggs and milk no oat milk"), "Grab eggs and oat milk")
eq(prepass("Meet at five no six"), "Meet at six")
eq(prepass("Call Sarah no call Mark"), "Call Mark")
print("unpunctuated self-corrections pass")

# the offline path: prepass output is already the final answer, and the guard
# must not treat the retracted words as content the cleanup model lost
pre = prepass("Let's meet Tuesday, oh wait no, Wednesday at 3.")
eq(pre, "Let's meet Wednesday at 3.")
ok, why = guard(pre, "Let's meet Wednesday at 3.")
assert ok, why

# the server path with the cleanup model unavailable: the correction already
# happened in prepass, so the fallback text is the intended text.
import asyncio
import httpx
import orpheus_server as srv


class _CleanupDown:
    async def post(self, *a, **kw):
        raise httpx.ConnectError("cleanup model is down")

    async def get(self, *a, **kw):
        raise httpx.ConnectError("cleanup model is down")


srv.http_client = _CleanupDown()
out, note = asyncio.run(srv.cleanup_pass(pre))
eq(out, "Let's meet Wednesday at 3.")
assert "fallback" in note, note
print("offline self-correction pass")

# --- ambiguity floor (added 11-24)
# Losing the speaker's words is worse than leaving a correction unresolved.
# A cue word only rewrites the text when the second part is a clean parallel
# swap; ordinary speech that contains "no", "wait", "sorry", "actually",
# "I mean", "never mind" or "scratch that" must come through untouched, so the
# cleanup model still sees the original words and can repair them.
_ORDINARY = [
    "It costs five dollars, no more, no less.",
    "He was like, no, wait, stop.",
    "Put the box on the table, no, under the table.",
    "Thanks, never mind, I found it.",
    "Never mind the weather, we're going anyway.",
    "Red paint, no, blue.",
    "There's no way we make it.",
    "No wonder you're tired.",
    "It was no big deal.",
    "He gave me no answer.",
    "No more, no less.",
    "Put it at 3, no more.",
    "It costs 3, no less.",
    "It's no more than five minutes.",
    "There's no need to wait.",
    "Wait, what?",
    "Wait, 5 minutes.",
    "Wait a minute, that's not right.",
    "No, wait, don't tell me.",
    "Wait, no, that's not what I meant.",
    "Sorry to bother you.",
    "Sorry about that.",
    "Sorry, I'm late.",
    "Sorry, wait, what did you say?",
    "Actually, I disagree.",
    "Actually, no, I'll pass.",
    "It actually works.",
    "You should actually read the docs.",
    "It's not bad, actually, it's quite good.",
    "At 3, actually 4 people showed up.",
    "I mean, it's fine.",
    "I mean, no, that's not it.",
    "You know what I mean.",
    "I mean what I say.",
    "Never mind that noise.",
    "Never mind that.",
    "Never mind, I found it.",
    "Never mind that, I found it.",
    "I was looking for my keys. Never mind that, I found them.",
    "Scratch that itch.",
    "I need to scratch that itch before we go.",
    "Scratch that idea.",
    "Delete that thought.",
    "Delete that file.",
    "Ignore that warning.",
    "Forget that noise.",
    "Forget it, let's go.",
    "Strike that match.",
    "Cancel that subscription.",
    "We need five, no six is fine.",
    "There were five, no, six people.",
    "He was at the store, no, the park was closed.",
    "Put the box on the table, no, the shelf is full.",
    "Send it to John, no, to Mary because she's closer.",
    "It's at 3, no doubt.",
    "It's at 3, no problem.",
]
for s in _ORDINARY:
    eq(prepass(s), s)
print("ordinary cue words survive the pre-pass")

# a dictation that is only a retraction keeps its words
eq(prepass("Never mind."), "Never mind.")
eq(prepass("Never mind that last part."), "Never mind that last part.")
eq(prepass("Scratch that."), "Scratch that.")
eq(prepass("Forget everything."), "Forget everything.")

# the clean swaps still resolve, including every cue family
eq(prepass("Meet at 3, wait no, 4."), "Meet at 4.")
eq(prepass("Tuesday, oh wait no, Wednesday."), "Wednesday.")
eq(prepass("The call is at 3, sorry, 4."), "The call is at 4.")
eq(prepass("Meet at 3, I mean 4."), "Meet at 4.")
eq(prepass("It costs five dollars, no, six dollars."), "It costs six dollars.")
eq(prepass("Bring the red folder, no, the blue folder."), "Bring the blue folder.")
eq(prepass("Use the top shelf, no, the bottom shelf."), "Use the bottom shelf.")
eq(prepass("Book the one on the left, no, the one on the right."),
   "Book the one on the right.")
eq(prepass("The red one is better, no, the blue one is cheaper."),
   "The blue one is cheaper.")
eq(prepass("Call me at 555-1234, no, 555-5678."), "Call me at 555-5678.")
print("clean parallel swaps still resolve")

# the offline pipeline: an ambiguous correction is left as spoken, not
# garbled, and the guard accepts the pre-passed text unchanged
srv.http_client = _CleanupDown()
for s in ("Red paint, no, blue.", "Put the box on the table, no, under the table.",
          "It costs five dollars, no more, no less.", "Never mind."):
    pre = prepass(s)
    eq(pre, s)
    ok, why = guard(pre, pre)
    assert ok, (s, why)
    out, note = asyncio.run(srv.cleanup_pass(pre))
    eq(out, s)
    assert "fallback" in note, note
print("ambiguous corrections survive the offline pipeline")

# with the model up, a correction the pre-pass left alone can still be
# repaired: the guard allows the retracted word before the cue to go, but a
# clipped opening with nothing before the cue is still a failure
ok, _ = guard("Red paint, no, blue.", "Blue paint.")
assert ok
ok, why = guard("Red paint, no, blue.", "Paint.")
assert not ok and "trailing" in why, why
ok, why = guard("No, call Mark.", "Mark.")
assert not ok and "leading" in why, why
ok, _ = guard("Coffee, no, tea.", "Tea.")   # one-word swap: the retracted word may go
assert ok
print("guard allows the repair, not the loss")

# --- corrections at scale (added 11-24, round three)
# Round two stopped the pre-pass from eating ordinary speech, and overcorrected:
# the guard then threw away the model's correct repairs, and two corrections
# in one sentence collapsed the text between them. Each correction now reaches
# back over its own clause only, and the guard accepts the loss a cue licenses.
eq(prepass("Grab the mail at two, no, three, and get lunch from the taco place, I mean the burger place."),
   "Grab the mail at three, and get lunch from the burger place.")
eq(prepass("Cancel the order for the blue chairs, let me rephrase that, cancel the order for the red chairs."),
   "Cancel the order for the red chairs.")
eq(prepass("Book the flight for Monday, let me rephrase that, book it for Tuesday."),
   "Book it for Tuesday.")
eq(prepass("Send the invoice to John, no, to Mary, and cc Sarah, no, cc Dana."),
   "Send the invoice to Mary, and cc Dana.")
eq(prepass("Take the red folder, no, the blue folder, and leave it in the drawer, I mean the cabinet."),
   "Take the blue folder, and leave it in the cabinet.")
eq(prepass("Book the flight for Monday, no, Tuesday, and call the hotel at three, no, four."),
   "Book the flight for Tuesday, and call the hotel at four.")
eq(prepass("Book the flight for Monday, no, Tuesday, no, Wednesday, no, Thursday."),
   "Book the flight for Thursday.")
eq(prepass("Book the one on the left, no, the one on the right."),
   "Book the one on the right.")      # the restart anchors at the phrase, not the last "the"
# a value swap must not take the noun after it with it: "three pizzas", not
# "three". The pre-pass is not sure, so it leaves the cue for the model.
eq(prepass("Order two pizzas, no, three, and add salad, actually, add bread."),
   "Order two pizzas, no, three, and add bread.")
# the replacement phrase, not the rest of the dictation, is what evidence and
# cuts are measured against: "to Mary" is a one-word swap even when a second
# correction follows it
eq(prepass("Meet Sarah at 5, sorry, 6, and call Dave, wait no, call Mike."),
   "Meet Sarah at 6, and call Mike.")
print("multi-correction pre-pass pass")

# the property behind "never garbled": the pre-pass may drop a retracted word,
# but it may never invent, duplicate or reorder one. Every output word has to
# appear in the input, in order.
import re as _re
def _toks(s):
    return _re.findall(r"[a-z0-9]+", s.lower())
def _subseq(small, big):
    it = iter(big)
    return all(any(x == y for y in it) for x in small)
_GARBLE = [
    "Grab the mail at two, no, three, and get lunch from the taco place, I mean the burger place.",
    "Cancel the order for the blue chairs, let me rephrase that, cancel the order for the red chairs.",
    "Book the flight for Monday, no, Tuesday, and call the hotel at three, no, four.",
    "Send the invoice to John, no, to Mary, and cc Sarah, no, cc Dana.",
    "Take the red folder, no, the blue folder, and leave it in the drawer, I mean the cabinet.",
    "The meeting is Monday at 3, no, Tuesday at 4, and lunch is at noon, no, at one.",
    "Pick up the kids at 3, actually 4, and drop the package at the post office, no, the FedEx store.",
    "Order two pizzas, no, three, and add salad, actually, add bread.",
    "Book it for 3 people, no, 4, and reserve the room for 2 hours, no, 3.",
    "Set the alarm for 6, no, 7, and leave at 8, no, 9.",
    "Call the bank, no, the credit union, and pay the bill, no, the rent.",
    "Meet at the office at three, no, four, and bring the laptop, I mean the tablet.",
    "Book the flight for Monday. Never mind, just book it for Tuesday. Book the hotel for Tuesday, no, Wednesday.",
    "Book the flight for Monday, no, Tuesday. Mail the invoice to John, no, to Mary.",
    "I'll take the red one, sorry, the blue one, and ship it to the office, I mean to the shop.",
    "Put the box on the table, no, under the table.",
    "He was at the store, no, the park was closed.",
    "There were five, no, six people.",
    "No, I don't think so. Wait for me at the door. I mean it.",
    "Call the bank, get cash, never mind that last part, get a receipt.",
]
for s in _GARBLE:
    out = prepass(s)
    assert out.strip(), s
    assert _subseq(_toks(out), _toks(s)), (s, out)
    w = _re.findall(r"[a-z]+", out.lower())
    assert not [x for x, y in zip(w, w[1:]) if x == y and len(x) > 2], (s, out)
print("multi-correction output is never garbled")

# the guard: a cue licenses exactly the words it replaces, so the model's
# correct repairs pass and a model that drops uncorrected words still fails
for src, cleaned in [
    ("Book the flight for Monday. Never mind, just book it for Tuesday.",
     "Book the flight for Tuesday."),                       # drops Monday, the cue, "it"
    ("Book the flight for Monday, let me rephrase that, book it for Tuesday.",
     "Book it for Tuesday."),                               # rephrase retracts the clause
    ("Grab the mail at two, no, three, and get lunch from the taco place, I mean the burger place.",
     "Grab the mail at three, and get lunch from the burger place."),
    ("Cancel the order for the blue chairs, let me rephrase that, cancel the order for the red chairs.",
     "Cancel the order for the red chairs."),
    ("Grab eggs and milk, no, oat milk.", "Grab eggs and oat milk."),
    ("Send the invoice to John, no, to Mary.", "Send the invoice to Mary."),
]:
    ok, why = guard(src, cleaned)
    assert ok, (src, cleaned, why)
for src, cleaned, want in [
    ("Book the flight for Monday. Never mind, just book it for Tuesday.", "Tuesday.", "content words"),
    ("Book the flight for Monday, let me rephrase that, book it for Tuesday.", "Tuesday.", "content words"),
    ("Book the flight for Monday. Never mind, just book it for Tuesday.",
     "Book the flight for Monday.", "trailing"),          # cut the corrected clause off
    ("Grab eggs and milk, no, oat milk.", "Grab eggs.", "trailing"),
    ("Cancel the order for the blue chairs, let me rephrase that, cancel the order for the red chairs.",
     "Red chairs.", "content words"),
]:
    ok, why = guard(src, cleaned)
    assert not ok and want in why, (src, cleaned, why)
print("guard accepts licensed loss, rejects the rest")

# and the offline pipeline returns the corrected sentence itself, LLM down
srv.http_client = _CleanupDown()
for src, want in [
    ("Grab the mail at two, no, three, and get lunch from the taco place, I mean the burger place.",
     "Grab the mail at three, and get lunch from the burger place."),
    ("Cancel the order for the blue chairs, let me rephrase that, cancel the order for the red chairs.",
     "Cancel the order for the red chairs."),
    ("Book the flight for Monday, let me rephrase that, book it for Tuesday.",
     "Book it for Tuesday."),
]:
    pre = prepass(src)
    eq(pre, want)
    ok, why = guard(pre, pre)
    assert ok, (src, why)
    out, note = asyncio.run(srv.cleanup_pass(pre))
    eq(out, want)
    assert "fallback" in note, note
print("multi-correction survives the offline pipeline")

# a cue word with nothing corrected around it is meaning, not filler: the guard
# rejects a cleanup that drops it, and still allows it to go inside a real repair
for src, cleaned, want_ok in [
    ("Actually, the movie was better than I expected.", "The movie was better than I expected.", False),
    ("Actually, the movie was better than I expected.", "Actually, the movie was better than I expected.", True),
    ("It is fine, actually, I like it.", "It is fine, I like it.", False),
    ("Thanks, never mind, I found it.", "Thanks, I found it.", False),
    ("I mean, it is not a big deal.", "It is not a big deal.", False),
    ("Um, actually, can you send it?", "Actually, can you send it?", True),
    ("Call me tomorrow, actually, call me tonight.", "Call me tonight.", True),
    ("Add bread, cheese, and apples. Actually, not apples, pears.", "Add bread, cheese, and pears.", True),
    ("Reserve the van for Friday morning, um, sorry, Saturday morning, and text Luis the address.",
     "Reserve the van for Saturday morning, and text Luis the address.", True),
    ("Tell Sarah, oh I mean Sophie, that the report is done.", "Tell Sophie that the report is done.", True),
    ("Send the draft to the whole team. Never mind, just send it to Dave.", "Send the draft to Dave.", True),
    ("Sorry I missed your call, I was driving.", "Sorry I missed your call, I was driving.", True),
]:
    ok, why = guard(src, cleaned)
    assert ok == want_ok, (src, cleaned, why)
    if not ok:
        assert why.startswith("discourse word dropped"), why
for src in ["Actually, the movie was better than I expected.", "It's fine, actually, I like it.",
            "Thanks, never mind, I found it.", "I mean, it's not a big deal."]:
    eq(prepass(src), src)
print("discourse words survive the guard")

# a hesitation-only clause is not what a hard cue takes back
eq(prepass("I'll be there by noon, uh, scratch that, by one."), "By one.")
eq(prepass("Meet at noon, um, scratch that, at one."), "At one.")
eq(prepass("I'll be there by noon, uh, by one."), "I'll be there by noon, uh, by one.")
print("hesitation clauses are skipped")
