"""Dictation formatting: spoken-symbol pre-pass, cleanup prompt, and the
content-preservation guard that keeps the LLM honest.

Pipeline:  ASR text -> prepass() -> LLM cleanup -> guard() -> restore_terminal()
           -> listify() -> fit_context() -> final text

The pre-pass is deterministic and only rewrites tokens that are unambiguous
when spoken ("new line", "open paren", "exclamation point"). Words that are
also ordinary English ("at", "dot", "dash", "hash", "period" mid-sentence)
are left for the LLM, which sees the surrounding sentence.

Lists get three layers: spoken markers ("bullet point", "number one") are
rewritten here; the LLM is asked to lay out anything the speaker enumerates
(needs, steps, options) one item per line; and listify() is a deterministic
fallback for the plainest shape ("I need eggs, milk, bread and cheese") so
that shape formats the same way whether or not the LLM pass ran.
"""

import difflib
import re

# ---------------------------------------------------------------- pre-pass

_I = re.IGNORECASE

# (pattern, replacement). Order matters: longer phrases first.
_RULES = [
    # line structure — swallow the punctuation the recognizer stuck around it
    (r"[,.]?\s*\b(?:new paragraph|start a new paragraph|next paragraph)\b[,.]?\s*", "\n\n"),
    (r"[,.]?\s*\b(?:new line|next line|line break)\b[,.]?\s*", "\n"),
    # terminal punctuation by name
    (r"[,.]?\s*\bexclamation (?:point|mark)\b[.,]?", "!"),
    (r"[,.]?\s*\bquestion mark\b[.,]?", "?"),
    (r"[,.]?\s*\bsemicolon\b[,.]?", ";"),
    (r"[,.]?\s*\bfull stop\b[.,]?", "."),
    # "comma"/"colon" only when the recognizer already heard a pause there
    # ("Mom Comma, the" / "list colon."): a bare mid-sentence "comma" stays
    (r"[,.]?\s*\bcomma\b\s*[,.]", ","),
    (r"[,.]?\s*\bcolon\b\s*[,.:]", ":"),
    # "period" only where a sentence could actually end: followed by
    # punctuation / end of text / a capitalised word. Keeps "a long period of".
    (r"[,.]?\s*\bperiod\b[.,]?(?=\s*(?:$|\n|(?-i:[A-Z])))", "."),
    (r"[,.]?\s*\bellipsis\b[.,]?", "…"),
    # brackets
    (r"\bopen (?:paren|parenthesis|parentheses)\b[,.]?\s*", "("),
    (r"[,.]?\s*\bclose (?:paren|parenthesis|parentheses)\b", ")"),
    (r"\bopen (?:square )?bracket\b[,.]?\s*", "["),
    (r"[,.]?\s*\bclose (?:square )?bracket\b", "]"),
    (r"\bopen (?:curly )?brace\b[,.]?\s*", "{"),
    (r"[,.]?\s*\bclose (?:curly )?brace\b", "}"),
    (r"\bopen angle bracket\b[,.]?\s*", "<"),
    (r"[,.]?\s*\bclose angle bracket\b", ">"),
    # paired quotes: "quote ... end quote" / "... unquote"
    (r"\b(?:open |begin )?quote\b[,.]?\s*(.+?)[,.]?\s*\b(?:end quote|close quote|unquote)\b", r'"\1"'),
    # symbols with an unambiguous spoken name
    (r"\s*\bat (?:sign|symbol)\b\s*", "@"),
    (r"\s*\bunderscore\b\s*", "_"),
    (r"\s*\bdash dash\b\s*", " --"),
    (r"\s*\bem[- ]?dash\b\s*", " — "),
    (r"\s*\b(?:forward )?slash\b\s*", "/"),
    (r"\s*\bback ?slash\b\s*", "\\\\"),
    (r"\s*\bpercent sign\b", "%"),
    (r"\s*\bdollar sign\b\s*", " $"),
    (r"\s*\bampersand\b\s*", " & "),
    (r"\s*\basterisk\b\s*", "*"),
    (r"\s*\bequals sign\b\s*", " = "),
    (r"\s*\bplus sign\b\s*", " + "),
    (r"\s*\btilde\b\s*", "~"),
    (r"\s*\bcaret\b\s*", "^"),
    (r"\s*\bvertical bar\b\s*", " | "),
    (r"\s*\bdegree (?:sign|symbol)\b", "°"),
    (r"\s*\bcopyright (?:sign|symbol)\b", " ©"),
    (r"\s*\btrademark (?:sign|symbol)\b", "™"),
    (r"\s*\bsmiley face\b[.,]?", " 🙂"),
    (r"\s*\bthumbs up\b[.,]?", " 👍"),
    (r"\s*\bheart emoji\b[.,]?", " ❤️"),
]
_COMPILED = [(re.compile(p, _I), r) for p, r in _RULES]

# "pick up the cleaning, scratch that, the cleaners are closed" -> the thought
# before the trigger goes, what follows stays. Retracts back to the previous
# sentence end; when the trigger opens a sentence, the previous sentence is
# the thought being retracted.
_SCRATCH = re.compile(r"\b(?:scratch that|strike that|delete that)\b[,.]?\s*", _I)
_SENTENCE_END = re.compile(r"[.!?\n]")


def _retract(text: str) -> str:
    while True:
        m = _SCRATCH.search(text)
        if not m:
            return text
        before = text[:m.start()].rstrip()
        # find the boundary of the clause being retracted
        ends = [x.end() for x in _SENTENCE_END.finditer(before)]
        if ends and ends[-1] == len(before):
            # trigger opened a sentence: retract the whole previous sentence
            ends = ends[:-1]
        cut = ends[-1] if ends else 0
        rest = text[m.end():]
        rest = rest[:1].upper() + rest[1:]
        text = (text[:cut].rstrip() + " " + rest).strip()


# ------------------------------------------------------------ list markers
#
# "bullet point eggs bullet point milk" -> "- eggs\n- milk"
# "number one, finish the report. Number two, send it." -> "1. ...\n2. ..."
# A marker only counts where a speaker would put one: "the number one
# priority" and "add a bullet point" are prose and stay.

_BULLET_MARK = re.compile(
    r"[,.;:]?\s*\b(?:bullet point|new bullet|next bullet|next item|next point)\b[,.:]?\s*", _I)
_NUMBER_MARK = re.compile(
    r"[,.;:]?\s*\bnumber (one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|\d{1,2})\b([,.:]?)\s*", _I)
_MARK_DETERMINER = re.compile(
    r"\b(?:a|an|the|each|every|another|this|that|per|as|add|make|with|into|my|our|your|his|her|their)\s*$", _I)
_DIGITS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}


def _after_determiner(text: str, m) -> bool:
    """'add a bullet point' — a determiner right before the marker, with no
    pause between, means the words are prose."""
    if m.group(0).lstrip()[:1] in ",.;:":
        return False
    return bool(_MARK_DETERMINER.search(text[:m.start()]))


def _list_markers(text: str) -> str:
    def bullet(m):
        if _after_determiner(text, m):
            return m.group(0)
        return "\n- "

    def number(m):
        head = text[:m.start()].rstrip()
        anchored = not head or head[-1] in ".,;:!?\n" or bool(m.group(2))
        if not anchored or _after_determiner(text, m):
            return m.group(0)
        n = m.group(1).lower()
        return "\n" + _DIGITS.get(n, n) + ". "

    text = _BULLET_MARK.sub(bullet, text)
    return _NUMBER_MARK.sub(number, text)


_ITEM_LINE = re.compile(r"^(?P<mark>[-*•] |\d{1,2}[.)] )(?P<body>.*)$")
_INLINE_NUM = re.compile(r"(?:(?<=^)|(?<=\s))(\d{1,2})\. (?=\S)")


def _inline_numbering(text: str) -> str:
    """The recognizer writes a counted-off list inline: 'are 1. Finish the
    report 2. Send it'. A run 1. 2. 3. … in order becomes one item per line;
    a stray 'Python 3. Restart' has no 1. before it and stays."""
    if "\n" in text:
        return text
    marks = list(_INLINE_NUM.finditer(text))
    if len(marks) < 2 or [int(m.group(1)) for m in marks] != list(range(1, len(marks) + 1)):
        return text
    out, last = [], 0
    for m in marks:
        out.append(text[last:m.start()].rstrip())
        last = m.start()
    out.append(text[last:])
    return "\n".join(x for x in out if x)


def _tidy_lists(text: str) -> str:
    """Capitalise items, drop their trailing period, end the lead-in line
    with a colon. Runs on anything that has list lines, whoever made them."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = _ITEM_LINE.match(line)
        if not m:
            continue
        body = m.group("body").strip()
        if body:
            body = body[0].upper() + body[1:]
        if body.endswith(".") and not body.endswith("..") and not re.search(r"[.!?]\s+\S", body):
            body = body[:-1]
        lines[i] = m.group("mark") + body
        prev = lines[i - 1].rstrip() if i else ""
        if prev and not _ITEM_LINE.match(prev):
            if prev.endswith(","):
                prev = prev[:-1] + ":"
            elif prev[-1] not in ".!?:":
                prev += ":"
            lines[i - 1] = prev
    return "\n".join(lines)


_TIDY = [
    (re.compile(r"\(\s+"), "("),
    (re.compile(r"\s+\)"), ")"),
    (re.compile(r"\[\s+"), "["),
    (re.compile(r"\s+\]"), "]"),
    (re.compile(r"[ \t]+([,.;:!?])"), r"\1"),
    # "at/home" -> "at /home": a preposition glued to a path start
    (re.compile(r"\b(at|in|to|into|under|from|of|on|see|open|edit|cd)/", _I), r"\1 /"),
    (re.compile(r"[ \t]{2,}"), " "),
    (re.compile(r"[ \t]*\n[ \t]*"), "\n"),
    (re.compile(r"\n{3,}"), "\n\n"),
]


def parse_dictionary(spec: str) -> tuple[list[str], list[tuple[re.Pattern, str]]]:
    """'Jonny, Keryx, Johnny=Jonny, Currics=Keryx' ->
    (["Jonny", "Keryx"], [(/\bjohnny\b/i, "Jonny"), (/\bcurrics\b/i, "Keryx")])
    Plain words go to the prompt; `heard=meant` pairs are fixed before the LLM."""
    words, aliases = [], []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" in item:
            heard, meant = (x.strip() for x in item.split("=", 1))
            if heard and meant:
                aliases.append((re.compile(r"\b" + re.escape(heard) + r"\b", _I), meant))
                if meant not in words:
                    words.append(meant)
        elif item not in words:
            words.append(item)
    return words, aliases


def prepass(text: str, aliases: list[tuple[re.Pattern, str]] = (), capitalize: bool = True) -> str:
    """Rewrite unambiguous spoken symbol names into symbols and apply the
    user's misheard-name aliases. ``capitalize=False`` leaves the first
    letter alone (mid-sentence insertion, terminal input)."""
    if not text:
        return text
    for rx, meant in aliases:
        text = rx.sub(meant, text)
    for rx, rep in _COMPILED:
        text = rx.sub(rep, text)
    text = _list_markers(text)
    text = _inline_numbering(text)
    text = _retract(text)
    for rx, rep in _TIDY:
        text = rx.sub(rep, text)
    text = _tidy_lists(text)
    # a sentence that now starts after a line break should be capitalised
    text = re.sub(r"(^|\n)([a-z])" if capitalize else r"(\n)([a-z])",
                  lambda m: m.group(1) + m.group(2).upper(), text)
    text = text.strip()
    if not capitalize and text and text[0].isupper() and not _keep_case(text.split()[0], ()):
        text = text[0].lower() + text[1:]   # recognizer's own sentence capital
    return text


# ----------------------------------------------------------------- prompt

_PROMPT = """You are a dictation formatter. Turn raw speech-to-text into clean written text.

RULES
1. Preserve every content word the speaker said. Never drop, summarize, expand, or answer. If unsure, keep it.
2. Remove fillers (um, uh, you know, like, "so" at sentence start), stutters and false starts. Apply self-corrections: "five, no, six" -> "six". After "scratch that" / "actually" / "I mean", KEEP the words that follow and delete the phrase before it.
3. Use normal sentence case. Undo Title Case the recognizer invented. Fix punctuation. Keep line breaks that are already in the text. Keep the mark each sentence ends on: a question keeps its "?", an exclamation keeps its "!". Never end a question with a period.
4. Spoken symbol names that are still words become symbols: "dot"->".", "at"->"@" only inside an email address, "hashtag word"->#word (joined), "dash"->"-", "hyphen"->"-", "colon"->":", "comma"->",", "equals"->"=", "plus"->"+", "star"->"*", "hash"->"#".
5. Spoken code, paths, commands and addresses become literal: "get user (user id)"->get_user(user_id); "jonny at example dot com"->jonny@example.com; "npm --version" stays; "/home/jon" stays.
6. Numbers: prices, percents, times, dates, versions become digits ($25.50, 10%, 6pm, 09-01, v2.6.2). Small counts in prose stay words.
7. LISTS. When the speaker enumerates a set of things — items they need, tasks, steps, options, names — lay it out as a list: the lead-in on its own line ending with a colon, then one item per line. Use "- " bullets for unordered items; use "1." "2." numbering when the speaker counted them ("first... second...", "one... two...", "number one...", "step one..."). Each item starts with a capital and has no trailing period. Do this for three or more items, or whenever the speaker counted. Two things joined by "and" stay a sentence, and items that sit inside a longer sentence (more sentence follows them) stay inline. Keep list lines that are already in the text.
8. Output ONLY the formatted text. No quotes around it, no commentary, no preamble.
9. A request may start with STYLE / CONTEXT lines before "DICTATION:". Obey them, never repeat the context, and format only the dictation.
{dictionary}
EXAMPLES
in: um so send the invoice to sarah at acme dot com comma and cc me
out: Send the invoice to sarah@acme.com, and cc me.
in: the config lives at /etc/hermes dot yaml
Restart the gateway after
out: The config lives at /etc/hermes.yaml
Restart the gateway after.
in: "ship it" he said dash no hesitation
out: "Ship it," he said - no hesitation.
in: hashtag local peer is live at eight tonight!
out: #localpeer is live at 8 tonight!
in: pick up the dry cleaning, scratch that, the cleaners are closed on Mondays
out: The cleaners are closed on Mondays.
in: my top goals this week are one finish the report two send the presentation three book the flights
out: My top goals this week are:
1. Finish the report
2. Send the presentation
3. Book the flights
in: okay so from the store I need eggs milk bread and um cheese
out: From the store I need:
- Eggs
- Milk
- Bread
- Cheese
in: first back up the database then run the migration and finally restart the gateway
out: 1. Back up the database
2. Run the migration
3. Restart the gateway
in: grab me a coffee and a bagel on your way in
out: Grab me a coffee and a bagel on your way in.
in: we tested eggs, milk and bread and all three were fine
out: We tested eggs, milk and bread and all three were fine.
in: STYLE: chat message — relaxed; a single short sentence gets no trailing period.
CONTEXT: the text is inserted right after this existing text (do not repeat or continue it):
«Running late,»
It lands mid-sentence: start in lowercase unless the first word is a name, "I", or an acronym.
DICTATION:
grab me a coffee please
out: grab me a coffee please
in: STYLE: terminal/code — output exactly what should be typed. No sentence capitalization, no trailing period.
DICTATION:
Git commit dash m quote fix the nav end quote
out: git commit -m "fix the nav"
"""


def build_prompt(dictionary: str) -> str:
    words, _ = parse_dictionary(dictionary)
    block = ""
    if words:
        block = ("\nDICTIONARY — the speaker uses these names and terms; when the "
                 "recognizer produced a sound-alike, use this spelling: "
                 + ", ".join(words) + "\n")
    return _PROMPT.format(dictionary=block)


# ------------------------------------------------------------------ guard

_FILLERS = {
    "um", "uh", "uhm", "hmm", "mm", "er", "ah", "like", "you", "know", "so",
    "actually", "basically", "literally", "okay", "ok", "well", "i", "mean",
    "kind", "sort", "of", "just", "right", "yeah", "no", "scratch", "that",
}
_NUMBER_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "billion", "half", "quarter", "dollars", "dollar",
    "cents", "cent", "percent", "oclock", "am", "pm", "point", "and",
    # counting words the model may turn into list numbering
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
    "eighth", "ninth", "tenth", "next", "then", "finally", "lastly", "also",
    "number", "step", "item", "bullet",
}
# spoken names the LLM is allowed to turn into symbols (rule 4)
_SYMBOL_WORDS = {
    "dot", "at", "hashtag", "hash", "dash", "hyphen", "colon", "comma",
    "equals", "plus", "star", "minus", "the", "a", "an", "to", "is", "are",
}
_SKIP = _FILLERS | _NUMBER_WORDS | _SYMBOL_WORDS
_WORD = re.compile(r"[a-z0-9]+")


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _SKIP and len(w) > 1]


def _respelled(word: str, have: set[str]) -> bool:
    """True if some output token is a close spelling of `word`: the model is
    allowed to fix a misheard name (Johnny -> Jonny), a plural, a tense."""
    for cand in have:
        if abs(len(cand) - len(word)) <= 2 and cand[0] == word[0] \
                and difflib.SequenceMatcher(None, word, cand).ratio() >= 0.75:
            return True
    return False


def guard(source: str, cleaned: str) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means the LLM lost real content.

    Two failure shapes are caught: bulk loss (many content words missing)
    and truncation (the dictation's last content word is gone — the most
    common way a chat model "cleans" a trailing sentence out of existence).
    """
    src = _content_words(source)
    if not src:
        return True, ""
    have = set(_WORD.findall(cleaned.lower()))
    # a source word counts as kept if it survives whole or as a prefix/suffix
    # of a joined token (get_user -> "get","user"; localpeer <- "local","peer")
    joined = cleaned.lower()
    missing = [w for w in src if w not in have and w not in joined
               and not _respelled(w, have)]
    if src[-1] in missing:
        return False, f"trailing word dropped: {src[-1]!r}"
    limit = max(3, int(len(src) * 0.15))
    if len(missing) > limit:
        return False, f"{len(missing)}/{len(src)} content words dropped"
    return True, ""


# ------------------------------------------------------- terminal punctuation

_TERMINALS = ".!?…"
_CLOSERS = "\"'’”)]}»"
# punctuation a sentence can legitimately trail off on: a lead-in colon, a
# clause the caller will continue. Nothing to restore after these.
_OPEN_PUNCT = ":;,-–—*_`/"


def _mark_at(text: str) -> tuple[int, str]:
    """Index and value of the last character that isn't a closing quote or
    bracket ('he said "ship it"?' -> the '?'). (-1, "") for empty text."""
    body = text.rstrip()
    i = len(body) - 1
    while i >= 0 and body[i] in _CLOSERS:
        i -= 1
    return (i, body[i]) if i >= 0 else (-1, "")


def restore_terminal(source: str, cleaned: str) -> str:
    """Put back the mark the speaker ended on when the cleanup model dropped or
    downgraded it.

    The model is told to keep terminal punctuation, and mostly does — but under
    the relaxed message style it will now and then strip a question mark along
    with the trailing period it was allowed to drop, which reads as a different
    sentence. The speaker's own mark wins: it is restored when the model ended
    on nothing, and a "?" or "!" is restored over a period the model settled
    for. A period is never forced over the model's "?" or "!" — hearing the
    question is exactly the judgement the model is there to make.
    """
    if not source.strip() or not cleaned.strip():
        return cleaned
    _, src_mark = _mark_at(source)
    if src_mark not in _TERMINALS:
        return cleaned
    body = cleaned.rstrip()
    if _ITEM_LINE.match(body.rsplit("\n", 1)[-1]):
        return cleaned                      # list items carry no terminal mark
    i, out_mark = _mark_at(body)
    if out_mark == src_mark:
        return cleaned
    if out_mark in _TERMINALS:
        if src_mark not in "?!":
            return cleaned                  # the model's mark is the better one
        return body[:i] + src_mark + body[i + 1:]
    if out_mark in _OPEN_PUNCT:
        return cleaned                      # deliberately unfinished
    return body + src_mark


# --------------------------------------------------------------- context
#
# The client may tell us where the text is going: the app package, the text
# around the cursor and/or an explicit style. Deterministic bits live here;
# the LLM gets the same facts as STYLE / CONTEXT lines ahead of the dictation.

STYLES = ("auto", "prose", "message", "email", "code")
CONTEXT_CHARS = 240

_APP_STYLES = {
    # chat: relaxed, a lone short sentence keeps no trailing period
    "com.whatsapp": "message", "com.whatsapp.w4b": "message",
    "org.thoughtcrime.securesms": "message", "org.telegram.messenger": "message",
    "com.google.android.apps.messaging": "message",
    "com.samsung.android.messaging": "message", "com.discord": "message",
    "com.Slack": "message", "com.facebook.orca": "message",
    "com.instagram.android": "message", "com.snapchat.android": "message",
    "com.microsoft.teams": "message", "im.vector.app": "message",
    "com.beeper.android": "message", "com.cocakova.keryx": "message",
    # email: full sentences, paragraphs kept
    "com.google.android.gm": "email", "com.microsoft.office.outlook": "email",
    "com.samsung.android.email.provider": "email", "me.proton.android.mail": "email",
    "com.fsck.k9": "email", "net.thunderbird.android": "email",
    # terminals: literal input, no sentence dressing
    "com.cocakova.charon": "code", "com.termux": "code",
    "com.server.auditor.ssh.client": "code", "com.sonelli.juicessh": "code",
}

_STYLE_LINES = {
    "message": ("STYLE: chat message — relaxed; a single short statement gets no trailing period. "
                "A question still ends with \"?\" and an exclamation with \"!\"."),
    "email": "STYLE: email — full sentences, keep paragraph breaks.",
    "code": ("STYLE: terminal/code — output exactly what should be typed: commands, paths, "
             "flags, identifiers. No sentence capitalization, no trailing period, digits for numbers."),
}


def parse_app_styles(spec: str) -> dict[str, str]:
    """'com.foo=message, com.bar=code' -> {pkg: style}; unknown styles ignored."""
    out = {}
    for item in spec.split(","):
        if "=" in item:
            pkg, style = (x.strip() for x in item.split("=", 1))
            if pkg and style in STYLES:
                out[pkg] = style
    return out


def style_for(app: str, style: str = "", overrides: dict[str, str] | None = None) -> str:
    """Explicit style wins; otherwise map the app package; otherwise prose."""
    style = (style or "").strip().lower()
    if style in STYLES and style != "auto":
        return style
    app = (app or "").strip()
    return (overrides or {}).get(app) or _APP_STYLES.get(app) or "prose"


def _ends_sentence(before: str) -> bool:
    s = before.rstrip(" \t")
    return not s or s.endswith("\n") or s[-1] in ".!?"


def mid_sentence(before: str) -> bool:
    """True when the cursor sits inside an unfinished sentence."""
    return bool(before.strip()) and not _ends_sentence(before)


def _keep_case(word: str, dictionary_words) -> bool:
    """Words whose capital is meaning, not sentence position."""
    w = word.strip("\"'([{")
    if not w:
        return False
    if w == "I" or w.startswith("I'"):
        return True
    if any(c.isupper() for c in w[1:]):      # acronym / CamelCase / McName
        return True
    return any(w.lower() == d.lower() for d in dictionary_words)


def request_hints(before: str, after: str, style: str, extra_terms: str = "") -> str:
    """STYLE / CONTEXT lines the LLM sees ahead of the dictation ('' when none)."""
    parts = []
    line = _STYLE_LINES.get(style)
    if line:
        parts.append(line)
    if before.strip():
        parts.append("CONTEXT: the text is inserted right after this existing text "
                     "(do not repeat or continue it):\n«" + before[-CONTEXT_CHARS:] + "»")
        if mid_sentence(before):
            parts.append("It lands mid-sentence: start in lowercase unless the first word "
                         "is a name, \"I\", or an acronym.")
    if after.strip():
        parts.append("Text after the insertion point: «" + after[:80] + "»")
    if extra_terms.strip():
        parts.append("EXTRA TERMS the speaker uses: " + extra_terms.strip())
    if not parts:
        return ""
    return "\n".join(parts) + "\nDICTATION:\n"


def fit_context(text: str, before: str, style: str, dictionary_words=()) -> str:
    """Deterministic finish after the LLM: casing at a mid-sentence cursor and
    trailing-period conventions the model may have ignored."""
    if not text:
        return text
    first = text.split()[0] if text.split() else ""
    if (mid_sentence(before) or style == "code") and text[0].isupper() \
            and not _keep_case(first, dictionary_words):
        text = text[0].lower() + text[1:]
    if style == "code":
        if "\n" not in text and text.endswith(".") and not text.endswith(".."):
            text = text[:-1]
    elif style == "message":
        body = text.rstrip()
        # one short sentence, nothing else -> no trailing period
        if body.endswith(".") and not body.endswith("..") \
                and not re.search(r"[.!?]\s+\S", body) and "\n" not in body:
            text = body[:-1]
    return text


# ----------------------------------------------------------------- listify
#
# Deterministic fallback for the plainest spoken list: a lead-in that
# announces things ("I need", "grab me", "to-do list") followed by three or
# more short comma-separated items. The LLM handles every other shape;
# this one is common enough that it should come out the same every time,
# LLM or not.

_LIST_LEAD = re.compile(
    r"^(?P<lead>(?:(?:okay|ok|so|um|uh|alright|right)[,.]?\s+)*"
    r"(?:"
    # "from the store I need", "for the trip we'll need": a short clause may lead
    r"(?:[a-z' ]{0,30}?\s)?(?:i|we|you)(?:'ll| will| also| still)? need(?: to (?:get|grab|buy|pick up|do))?"
    r"|(?:things|stuff|what) (?:i|we|you) need(?: to (?:get|grab|buy|do))?"
    r"|(?:things|stuff) to (?:do|get|buy|grab)"
    r"|(?:my |our |the )?(?:to[- ]?do|shopping|grocery|packing|reading) list(?: is| for [a-z]+)?"
    r"|(?:please )?(?:grab|get|buy|bring)(?: me| us)?"
    r"|(?:please )?pick up"
    r"|remind me to (?:get|grab|buy|pick up)"
    r"|don't forget"
    r")"
    r"(?:\s+(?:from|at|for|on|before|after|tomorrow|today|tonight)\b[^,:]{0,40}?)?"
    r"(?: the following| these(?: things| items)?| a few things| some (?:things|stuff|items))?"
    r")(?:\s*:|,)?\s+(?P<items>.+?)\s*$", _I)
_ITEM_SPLIT = re.compile(r"\s*[,;]\s*")
_ITEM_LEAD = re.compile(r"^(?:and|or|also|plus|then)\s+", _I)
_BAD_ITEM_START = re.compile(
    r"^(?:you|me|us|them|him|her|it|that|this|these|those|to|if|when|because|but|which|who)\b", _I)
MIN_LIST_ITEMS = 3
MAX_ITEM_WORDS = 6


def _split_items(items: str) -> list[str]:
    parts = [p for p in _ITEM_SPLIT.split(items) if p.strip()]
    if not parts:
        return []
    # "bread and cheese" at the end -> two items; "salt and pepper" mid-list stays one
    last = parts[-1]
    m = re.match(r"^(?P<a>.+?)\s+(?:and|or)\s+(?P<b>.+)$", last, _I)
    if m and len(parts) >= 2 and not _ITEM_LEAD.match(last):
        parts[-1:] = [m.group("a"), m.group("b")]
    out = []
    for p in parts:
        p = _ITEM_LEAD.sub("", p.strip()).strip()
        out.append(p)
    return out


def listify(text: str) -> str:
    """'I need eggs, milk, bread and cheese.' ->
    'I need:\n- Eggs\n- Milk\n- Bread\n- Cheese'. Anything else returns unchanged."""
    if not text or "\n" in text:
        return text
    body = text.strip()
    if body.endswith((".", "!")):
        body = body[:-1]
    if re.search(r"[.!?]\s+\S", body):      # more than one sentence: not a bare list
        return text
    m = _LIST_LEAD.match(body)
    if not m:
        return text
    items = _split_items(m.group("items"))
    if len(items) < MIN_LIST_ITEMS:
        return text
    if any(not it or len(it.split()) > MAX_ITEM_WORDS or re.search(r"[.!?:]", it) for it in items):
        return text
    if _BAD_ITEM_START.match(items[0]):
        return text
    lead = m.group("lead").strip()
    lead = lead[0].upper() + lead[1:]
    return _tidy_lists(lead + ":\n" + "\n".join("- " + it for it in items))
