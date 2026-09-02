"""Dictation formatting: spoken-symbol pre-pass, cleanup prompt, and the
content-preservation guard that keeps the LLM honest.

Pipeline:  ASR text -> prepass() -> LLM cleanup -> guard() -> final text

The pre-pass is deterministic and only rewrites tokens that are unambiguous
when spoken ("new line", "open paren", "exclamation point"). Words that are
also ordinary English ("at", "dot", "dash", "hash", "period" mid-sentence)
are left for the LLM, which sees the surrounding sentence.
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
    text = _retract(text)
    for rx, rep in _TIDY:
        text = rx.sub(rep, text)
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
3. Use normal sentence case. Undo Title Case the recognizer invented. Fix punctuation. Keep line breaks that are already in the text.
4. Spoken symbol names that are still words become symbols: "dot"->".", "at"->"@" only inside an email address, "hashtag word"->#word (joined), "dash"->"-", "hyphen"->"-", "colon"->":", "comma"->",", "equals"->"=", "plus"->"+", "star"->"*", "hash"->"#".
5. Spoken code, paths, commands and addresses become literal: "get user (user id)"->get_user(user_id); "jonny at example dot com"->jonny@example.com; "npm --version" stays; "/home/jon" stays.
6. Numbers: prices, percents, times, dates, versions become digits ($25.50, 10%, 6pm, 09-01, v2.6.2). Small counts in prose stay words.
7. Sequence words ("first... second..." / "one... two...") on separate thoughts become a list, one item per line.
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
    "message": "STYLE: chat message — relaxed; a single short sentence gets no trailing period.",
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
