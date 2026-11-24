"""Dictation formatting: spoken-symbol pre-pass, self-correction, cleanup
prompt, and the content-preservation guard that keeps the LLM honest.

Pipeline:  ASR text -> prepass() -> LLM cleanup -> guard() -> restore_terminal()
           -> listify() -> fit_context() -> final text

The pre-pass is deterministic and only rewrites tokens that are unambiguous
when spoken ("new line", "open paren", "exclamation point"). Words that are
also ordinary English ("at", "dot", "dash", "hash", "period" mid-sentence)
are left for the LLM, which sees the surrounding sentence. It also resolves
spoken self-corrections ("Tuesday, oh wait no, Wednesday", "milk, no, oat
milk", "never mind that last part") so the text is right without the LLM,
but only on a clean parallel swap: losing the speaker's words is worse than
leaving a correction unresolved, so ambiguous cues are left as spoken.
Several corrections in one sentence each reach back over their own clause,
so the text between them survives. Cue words that are ordinary speech
("No, I don't think so", "wait for me", "I mean it") are left alone.

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

# ------------------------------------------------------- self-correction
#
# "Tuesday, oh wait no, Wednesday", "milk, no, oat milk", "never mind that
# last part": the thought before the cue goes, what follows stays. The cue
# words are ordinary English too ("No, I don't think so", "wait for me", "I
# mean it"), so a cue only fires on a clean parallel swap: the replacement
# repeats the clause opening, reuses the same anchor, swaps a value of the
# same shape, or shares a tail whose changed slot is right before it. A pause
# or a hook word alone is not enough ("Red paint, no, blue", "five dollars,
# no more, no less"), and a phrase rewrite ("on the table, no, under the
# table") is left for the LLM. Losing the speaker's words is worse than
# leaving a correction unresolved. This runs in prepass, before the LLM: the
# correction has to be right when the cleanup model is down, and the content
# guard must compare the model's answer against the corrected text rather
# than the retracted one.
#
# Two families of cue:
#   hard     scratch that / never mind that last part / let me rephrase that:
#            drop the thought before the cue; bare "never mind" only does so
#            with an explicit object
#   replace  no / wait / actually / sorry / I mean / make that: drop just the
#            piece of the thought the replacement supersedes
# A replacement is spliced at the smallest natural boundary: at a shared
# preposition or determiner ("to John, no, to Mary"), around a shared noun
# or verb ("eggs and milk, no, oat milk"), at the date or number being
# changed ("meet Tuesday at 3, no, Wednesday at 4"), or the last word.
#
# A cue reaches back over its own clause only, and its evidence and cut are
# measured against the replacement phrase (up to the next cue, pause or
# sentence end), never the rest of the dictation. That is what keeps several
# corrections in one sentence from collapsing: "grab the mail at two, no,
# three, and get lunch from the taco place, I mean the burger place" ->
# "grab the mail at three, and get lunch from the burger place", with the
# first correction still resolved. A swap that would delete a word the
# replacement does not carry ("Order two pizzas, no, three" -> "Order three")
# is left as spoken for the model.
#
# The guard forgives what a leftover cue licenses - the cue words and the span
# they replace - so a correct repair is not read as content loss; words
# outside those spans are still required.
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")
_CLAUSE_SEP = re.compile(r"[,;:—–](?=\s|$)")
_WORD_RX = re.compile(r"[A-Za-z0-9]+(?:[-'’.:/][A-Za-z0-9]+)*")

_CUE = re.compile(
    r"\b(?P<cue>"
    r"never\s*mind(?:\s+(?:that\s+)?(?:last\s+part|everything|that|this|it))?"
    r"|forget\s+(?:it|that|everything|about\s+it)"
    r"|disregard\s+(?:that|it|this)"
    r"|(?:scratch|strike|delete|ignore|cancel)\s+(?:that|it|this)"
    r"|(?:oh\s+)?(?:wait|hold\s+on)(?:\s+(?:a\s+(?:second|minute|moment)|one\s+second))?\b"
    r"|i\s+mean\b"
    r"|or\s+rather\b"
    r"|(?:make|change)\s+(?:that|it)\b"
    r"|on\s+second\s+thought\b"
    r"|let\s+me\s+rephrase(?:\s+(?:that|it|this))?\b"
    r"|correction\b"
    r"|actually\b"
    r"|sorry\b"
    r"|rather\b"
    r"|no\b)"
    r"(?P<chain>(?:\s*[,;:]?\s*(?:oh\s+)?(?:wait|no|actually|sorry|i\s+mean"
    r"|(?:make|change)\s+(?:that|it)|or\s+rather)\b)*)"
    r"\s*(?P<pause>[,;:.!?…—–-])?\s*", _I)

# Bare "never mind" is mostly discourse ("Thanks, never mind, I found it";
# "Never mind the weather"). It retracts only with an explicit object that
# points back at the text ("never mind that last part"). The verb + object
# forms ("scratch that itch", "delete that file") are qualified further in
# _cue_kind: no pause and a bare noun after the cue means ordinary speech.
_HARD_ALWAYS = re.compile(
    r"\bnever\s*mind\s+(?:that\s+)?last\s+part\b"
    r"|\bnever\s*mind\s+everything\b", _I)
_HARD_OBJECT = re.compile(
    r"\bforget\s+(?:that|everything|about\s+it)\b"
    r"|\bdisregard\s+(?:that|it|this)\b"
    r"|\blet\s+me\s+rephrase\s+(?:that|it|this)\b"
    r"|\b(?:scratch|strike|delete|ignore|cancel)\s+(?:that|it|this)\b", _I)
_STRONG = re.compile(r"\b(?:make|change)\s+(?:that|it)\b|\bor\s+rather\b"
                     r"|\bcorrection\b|\bon\s+second\s+thought\b", _I)

_PREP = frozenset("at in on to for with from by of into under over as "
                  "before after around until since during".split())
_DET = frozenset("a an the this that these those my your our their his her its".split())
_POSS = _DET | frozenset(("whose",))
_CONJ = frozenset(("and", "or", "but", "so", "then", "plus", "also"))
_ANCHORS = _PREP | _DET
_FUNCTION = _PREP | _DET | _CONJ | frozenset("i we you it he she they not do cannot dont".split())
_KEEP_BEFORE = _ANCHORS | _CONJ | frozenset(
    "like want need have has had love hate prefer use buy get take call send meet "
    "book order is are was were be been do does did can will would should could "
    "make made think said say tell ask give put keep".split())
_TIME_LINK = frozenset(
    "at on in by for from until before after around this next coming early late "
    "up about to of the a an".split())
_DAYS = frozenset(
    "monday mon tuesday tue tues wednesday wed thursday thu thurs friday fri "
    "saturday sat sunday sun today tomorrow tonight yesterday".split())
_MONTHS = frozenset(
    "january jan february feb march mar april apr may june jun july jul august aug "
    "september sep sept october oct november nov december dec".split())
_NUMBER_WORDS = frozenset(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty "
    "sixty seventy eighty ninety hundred thousand million billion "
    "first second third fourth fifth sixth seventh eighth ninth tenth "
    "eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth "
    "eighteenth nineteenth twentieth thirtieth".split())
_TIME_OF_DAY = frozenset("morning afternoon evening night noon midnight".split())
_BLOCK = {
    "wait": frozenset(
        "for at on in until to a an the up with and just here there i we you it "
        "he she they what who how this that then not problem rush minute".split()),
    "no": frozenset(
        "one way problem idea thanks comment doubt time matter kidding but so like "
        "i we you it they he she there this that and or sir ma am offense worries "
        "luck chance need reason excuse point question answer sense choice "
        "thank thanks please then not never just because when before after while "
        "unless yet still eventually twice once again either neither later soon "
        "now maybe perhaps probably definitely certainly honestly frankly "
        "seriously uh um er erm hmm".split()),
    "mean": frozenset("it you that what well business by exactly really this".split()),
    "sorry": frozenset("for about to that i if but what again excuse this "
                       "sometimes so just then when because".split()),
}
# a retraction cue before a fresh clause retracts; before a bare noun it is
# usually a verb and object ("scratch that itch").
_CLAUSE_START = _FUNCTION | frozenset(
    "please just and but so then also now okay ok alright let lets why how "
    "what where when who which if because".split())


def _starts_clause(text: str) -> bool:
    """Does `text` open a fresh clause? A retraction cue before a new clause
    retracts; the same cue before a bare noun is usually a verb and object
    ("scratch that itch")."""
    s = text.lstrip(" \t\"'(")
    if not s:
        return False
    if s[0].isupper():
        return True
    return _first_word(s) in _CLAUSE_START


def _first_word(text: str) -> str:
    m = _WORD_RX.search(text)
    return m.group(0).lower() if m else ""


def _rest_has_content(rest: str) -> bool:
    return bool(_WORD_RX.search(rest))


def _cue_kind(cue: str, chain: str = "", rest: str = "",
              post_pause: bool = False) -> str:
    if _HARD_ALWAYS.search(cue):
        return "hard"
    if _HARD_OBJECT.search(cue):
        # "Scratch that, the cleaners are closed" retracts; "scratch that
        # itch" does not. A pause, an empty tail, or a clause after the cue
        # is the evidence that the speaker meant the retraction.
        if post_pause or not rest.strip() or _starts_clause(rest):
            return "hard"
        return "soft"
    if _STRONG.search(cue) or chain.strip():
        return "replace"
    return "soft"


def _blocked(cue: str, rest: str) -> bool:
    low = cue.lower()
    first = _first_word(rest)
    if "'" in first:
        first = first.split("'")[0]
    if ("wait" in low or "hold on" in low) and first in _BLOCK["wait"]:
        return True
    if re.search(r"\bno\b", low) and first in _BLOCK["no"]:
        return True
    if re.search(r"\bi\s+mean\b", low) and first in _BLOCK["mean"]:
        return True
    if "sorry" in low and first in _BLOCK["sorry"]:
        return True
    return False


def _region_bounds(out: str) -> tuple[int, int]:
    """Span of the thought a cue can retract: the current clause mid-sentence,
    or the sentence that just ended when the cue opens a new one.

    A clause, not everything since the sentence began. With two corrections in
    one sentence the second must reach back over its own span only: "Grab the
    mail at two, no, three, and get lunch from the taco place, I mean the
    burger place" has to keep "Grab the mail at three, and get lunch from".
    The comma right before the cue is the cue's own pause (consumed by the
    cue match), so the clause starts after the previous separator.
    """
    body = out.rstrip()
    if not body:
        return 0, 0
    ends = [m.end() for m in _SENTENCE_END.finditer(body)]
    if ends and ends[-1] == len(body):
        start = ends[-2] if len(ends) > 1 else 0
    else:
        start = ends[-1] if ends else 0
    clause = body[start:].rstrip(" ,;:—–-")
    seps = list(_CLAUSE_SEP.finditer(clause))
    if seps:
        start += seps[-1].end()
        # A clause that is only a hesitation ("noon, uh, scratch that") is not
        # the thought being taken back; the one before it is.
        if _HESITATION_ONLY.fullmatch(body[start:]):
            inner, _ = _region_bounds(body[:start].rstrip(" ,;:—–-"))
            return inner, len(body)
    return start, len(body)


_HESITATION_ONLY = re.compile(r"[\s,;:—–-]*(?:(?:um+|uh+|uhm|hmm+|mm+|er+|ah+)[\s,;:—–-]*)+", _I)


def _clause_lead(region: str) -> tuple[int, str]:
    """Start and word of the region's first non-conjunction token: the word a
    restated clause has to repeat ("and get lunch from ..." -> "get")."""
    words = list(_WORD_RX.finditer(region))
    if not words:
        return 0, ""
    i = 0
    while i < len(words) - 1 and words[i].group(0).lower() in _CONJ:
        i += 1
    return words[i].start(), words[i].group(0).lower()


def _is_number_token(token: str) -> bool:
    t = token.lower()
    return t in _NUMBER_WORDS or any(c.isdigit() for c in t)


def _value_category(first: str) -> str | None:
    if not first:
        return None
    if first in _DAYS or first in _MONTHS:
        return "day"
    if _is_number_token(first):
        return "number"
    return None


def _last_value(region: str, category: str):
    best = None
    for m in _WORD_RX.finditer(region):
        t = m.group(0).lower()
        if category == "day" and (t in _DAYS or t in _MONTHS):
            best = m
        elif category == "number" and _is_number_token(t):
            best = m
    return best


def _expr_start(region: str, value_start: int) -> int:
    """Walk left from a date/number to the start of the time phrase
    ('at 3 on Tuesday' -> the 'at')."""
    words = list(_WORD_RX.finditer(region))
    idx = 0
    for i, m in enumerate(words):
        if m.start() >= value_start:
            idx = i
            break
    while idx > 0:
        prev = words[idx - 1].group(0).lower()
        if prev in _TIME_LINK or _is_number_token(prev) \
                or prev in _DAYS or prev in _MONTHS or prev in _TIME_OF_DAY:
            idx -= 1
        else:
            break
    return words[idx].start()


def _number_run_start(region: str, value_start: int) -> int:
    words = list(_WORD_RX.finditer(region))
    idx = 0
    for i, m in enumerate(words):
        if m.start() >= value_start:
            idx = i
            break
    while idx > 0 and _is_number_token(words[idx - 1].group(0)):
        idx -= 1
    return words[idx].start()


def _rich_time_expr(region: str, value) -> bool:
    has_day = any(m.group(0).lower() in _DAYS or m.group(0).lower() in _MONTHS
                  for m in _WORD_RX.finditer(region))
    has_number = any(_is_number_token(m.group(0)) for m in _WORD_RX.finditer(region))
    return has_day and has_number


def _shared_suffix_len(region: str, rest: str) -> int:
    left = [m.group(0).lower() for m in _WORD_RX.finditer(region)]
    right = [m.group(0).lower() for m in _WORD_RX.finditer(rest)]
    n = 0
    while n < len(left) and n < len(right) and left[-1 - n] == right[-1 - n]:
        n += 1
    return n


def _rest_has_day(text: str) -> bool:
    return any(m.group(0).lower() in _DAYS or m.group(0).lower() in _MONTHS
               for m in _WORD_RX.finditer(text))


def _last_chunk_start(text: str) -> int:
    """Start of the last space-delimited item, ignoring trailing punctuation.
    Keeps exotic tokens (emails, URLs, #codes, filenames) whole."""
    stripped = text.rstrip(" \t,;:—–-.!?…")
    if not stripped:
        return 0
    i = max(stripped.rfind(" "), stripped.rfind("\n"), stripped.rfind("\t"))
    return 0 if i < 0 else i + 1


def _with_lead(region: str, start: int) -> int:
    """Include a leading # or @ that is glued to the token at `start`."""
    while start > 0 and region[start - 1] in "#@":
        start -= 1
    return start


def _keep_prep_end(region: str, idx: int) -> int:
    """If the time phrase starts at a preposition and the replacement does not
    bring its own, keep the preposition: 'at 3 on Tuesday, no, 4 on Wednesday'
    -> 'at 4 on Wednesday'."""
    m = _WORD_RX.match(region, idx)
    if m and m.group(0).lower() in _TIME_LINK:
        return m.end()
    return idx


def _has_number_before(region: str, idx: int) -> bool:
    return any(_is_number_token(m.group(0))
               for m in _WORD_RX.finditer(region) if m.start() < idx)


def _nearest_anchor(region: str, anchor: str) -> int | None:
    """Position of the occurrence of `anchor` closest to the cue."""
    pos = None
    for m in _WORD_RX.finditer(region):
        if m.group(0).lower() == anchor:
            pos = m.start()
    return pos


def _anchor_pos(region: str, rest: str, anchor: str) -> int | None:
    """Where a replacement that repeats `anchor` attaches in `region`: the
    occurrence whose tail has the same length as the replacement's tail,
    nearest the cue.

    "Book the one on the left, no, the one on the right" attaches at the
    first "the" (the phrase restarts); "grab the mail at three, and get lunch
    from the taco place, I mean the burger place" attaches at "the taco place"
    (the nearest "the"), because that is the one whose tail matches. Choosing
    a fixed occurrence made one of the two wrong.
    """
    rwords = list(_WORD_RX.finditer(rest))
    if len(rwords) < 2 or rwords[0].group(0).lower() != anchor:
        return None
    lwords = list(_WORD_RX.finditer(region))
    need = len(rwords) - 1
    hits = [i for i, m in enumerate(lwords)
            if m.group(0).lower() == anchor and len(lwords) - i - 1 == need]
    return lwords[hits[-1]].start() if hits else None


def _replace_cut(region: str, rest: str) -> int:
    """Where, inside `region`, the thought the replacement supersedes starts."""
    rest = _replacement(rest)
    first = _first_word(rest)
    # "call Sarah, no, call Mark" restarts the thought: drop the whole clause.
    lead_start, lead = _clause_lead(region)
    if first and first == lead and not _is_function_word(first):
        return lead_start
    # a replacement that keeps the same preposition/determiner replaces the
    # phrase after it: "to John, no, to Mary" -> keep the first "to".
    if first in _ANCHORS:
        pos = _anchor_pos(region, rest, first)
        if pos is None:
            pos = _nearest_anchor(region, first)
        if pos is not None:
            return pos
    # dates and numbers replace the whole value, or the whole time phrase
    # when the replacement brings its own date.
    category = _value_category(first)
    if category:
        value = _last_value(region, category)
        if value:
            if first in _PREP:
                return _expr_start(region, value.start())
            if category == "number" and _rest_has_day(rest):
                return _keep_prep_end(region, _expr_start(region, value.start()))
            if category == "day":
                if _has_number_before(region, value.start()):
                    return _with_lead(region, _expr_start(region, value.start()))
                return _with_lead(region, value.start())
            if category == "number":
                return _with_lead(region, _number_run_start(region, value.start()))
            return _with_lead(region, value.start())
    # A replacement that shares a trailing word is replacing the head or the
    # modifier around it: "eggs and milk, no, oat milk" keeps "and";
    # "Monday morning, no, Tuesday morning" drops "Monday".
    shared = _shared_suffix_len(region, rest)
    if shared:
        words = list(_WORD_RX.finditer(region))
        p_idx = len(words) - shared
        if p_idx > 0:
            prev = words[p_idx - 1].group(0).lower()
            r_extra = len(list(_WORD_RX.finditer(rest))) - shared
            if prev in _CONJ or prev in _KEEP_BEFORE or r_extra < 1:
                return words[p_idx].start()
            return words[p_idx - 1].start()
        return words[0].start()
    # nothing structural to go on: the last item is the thing being fixed
    return _last_chunk_start(region)


def _day_month(token: str) -> bool:
    t = token.lower()
    return t in _DAYS or t in _MONTHS


_CUE_WORDS = frozenset("no wait actually sorry mean i rather or".split())
_VALUE_UNITS = frozenset(
    "dollars dollar cents cent percent bucks oclock am pm hundred thousand "
    "million billion the and".split())


def _replacement(rest: str) -> str:
    """The phrase a correction puts in place of the retracted one: the words
    after the cue up to the next cue, pause or sentence end.

    Evidence and cuts have to judge *this*, not the rest of the dictation.
    "Send the invoice to John, no, to Mary, and cc Sarah, no, cc Dana" is a
    clean one-word swap ("to John" -> "to Mary"); measured against the whole
    tail it looked like a 1-vs-7 phrase and the first correction never fired.
    """
    m = _CUE.search(rest)
    if m:
        rest = rest[:m.start()]
    m = _SENTENCE_END.search(rest)
    if m:
        rest = rest[:m.start()]
    m = _CLAUSE_SEP.search(rest.rstrip(" .!?…"))
    if m:
        rest = rest[:m.start()]
    return rest.strip()


def _value_phrase(rest: str) -> bool:
    """Does `rest` read as a replacement value, not a new predicate? A clean
    swap is "4", "4 on Wednesday", "six dollars"; "4 people showed up" and
    "six is fine" continue into a clause and are left to the LLM. The value
    ends at the next cue, pause or sentence end: in "at two, no, three, and
    get lunch..." the replacement is "three", and the clause that follows it
    is the speaker carrying on, not part of the swap."""
    rest = _replacement(rest)
    words = [m.group(0).lower() for m in _WORD_RX.finditer(rest)]
    if not words:
        return False
    for w in words[1:]:
        if (w in _TIME_LINK or w in _DAYS or w in _MONTHS
                or w in _TIME_OF_DAY or w in _NUMBER_WORDS
                or w in _VALUE_UNITS or w in _CUE_WORDS
                or _is_number_token(w)):
            continue
        return False
    return True


def _function_stem(word: str) -> str:
    w = word.lower()
    for sep in ("'", "’"):
        if sep in w:
            return w.split(sep)[0]
    return w


def _is_function_word(word: str) -> bool:
    """'it's', 'that's', 'we're' are function words, not a repeated clause
    opening: 'It's not bad, actually, it's quite good' is not a correction."""
    return _function_stem(word) in _FUNCTION


def _trailing_chunk(text: str) -> str:
    """The last spoken item in `text`, punctuation and a glued #/@ removed."""
    stripped = text.rstrip(" \t,;:.!?…—–-")
    if not stripped:
        return ""
    i = max(stripped.rfind(" "), stripped.rfind("\n"), stripped.rfind("\t"))
    start = 0 if i < 0 else i + 1
    while start > 0 and stripped[start - 1] in "#@":
        start -= 1
    return stripped[start:]


def _leading_chunk(text: str) -> str:
    """The first spoken item in `text`, without leading/trailing punctuation."""
    s = text.lstrip(" \t,;:—–-'\"([")
    if not s:
        return ""
    chunk = re.match(r"\S+", s).group(0)
    for junk in "\"'()]}":
        chunk = chunk.rstrip(junk)
    return chunk.rstrip(".!?,;:…—–-")


def _same_shape(a: str, b: str) -> bool:
    """Two tokens of the same spoken shape: a number swapped for a number,
    a day for a day, an address for an address, a path for a path. This is
    the cleanest deterministic swap and the one the speaker most often makes."""
    if not a or not b:
        return False
    if _is_number_token(a) and _is_number_token(b):
        return True
    if _day_month(a) and _day_month(b):
        return True
    if "@" in a and "@" in b:
        return True
    path_a = "/" in a or "://" in a or ("." in a and not a.endswith("."))
    path_b = "/" in b or "://" in b or ("." in b and not b.endswith("."))
    return path_a and path_b


def _anchor_swap(region: str, rest: str, anchor: str) -> bool:
    """A preposition/determiner swap is parallel only when the phrase after
    it has the same length on both sides: "to John, no, to Mary" (1 = 1) is a
    clean swap; "at the store, no, the park was closed" (1 vs 3) is a new
    clause and stays as spoken."""
    rwords = list(_WORD_RX.finditer(rest))
    if len(rwords) < 2 or rwords[0].group(0).lower() != anchor:
        return False
    return _anchor_pos(region, rest, anchor) is not None


def _starts_replacement(out: str, rest: str) -> bool:
    """Evidence that a cue word is a correction and not ordinary speech.

    Only a clean parallel swap counts: the replacement repeats the clause
    opening ("call Sarah, no, call Mark"), reuses the same preposition or
    determiner ("to John, no, to Mary"), swaps a value of the same shape
    (number/day/address/path), or shares a tail where the changed slot is
    the word right before it ("eggs and milk, no, oat milk"). A shared tail
    that begins after a preposition is a phrase rewrite, not a slot swap:
    "Put the box on the table, no, under the table" stays as spoken.
    """
    rest = _replacement(rest)
    first = _first_word(rest)
    if not first or first in _FUNCTION - _ANCHORS:
        return False
    start, _ = _region_bounds(out)
    region = out[start:]
    if not region.strip():
        return False
    if first == _clause_lead(region)[1] and not _is_function_word(first):
        return True
    if first in _ANCHORS and _anchor_swap(region, rest, first):
        return True
    trailing, leading = _trailing_chunk(region), _leading_chunk(rest)
    if _same_shape(trailing, leading):
        if _is_number_token(leading) or _day_month(leading):
            return _value_phrase(rest)
        # an address or a path stands alone: "a@b.com, no, c@d.com"
        head = rest
        nxt = _CUE.search(head)
        if nxt:
            head = head[:nxt.start()]
        return head.strip().rstrip(" \t,;:.!?…—–-") == leading
    category = _value_category(first)
    if category and _last_value(region, category) is not None \
            and _value_phrase(rest):
        return True
    shared = _shared_suffix_len(region, rest)
    if shared:
        words = list(_WORD_RX.finditer(region))
        p_idx = len(words) - shared
        if p_idx > 0 and words[p_idx - 1].group(0).lower() not in _ANCHORS:
            return True
    return False


def _drops_words(region: str, repl: str) -> bool:
    """Would the replacement's cut delete a word the speaker said and the
    replacement does not carry?

    "Order two pizzas, no, three" cuts at the number and drops "pizzas": the
    speaker meant "Order three pizzas". Losing a word is worse than leaving
    the correction for the cleanup model, so a value swap is only taken when
    the words after the value are carried by the replacement ("Tuesday
    morning" -> "Wednesday morning") or are part of the value itself ("June
    5th" -> "June 6th at 4").
    """
    first = _first_word(repl)
    lead = _clause_lead(region)[1]
    if first and first == lead and not _is_function_word(first):
        return False                      # the replacement restates the clause
    if first in _ANCHORS:
        return False                      # parallel slot of equal length
    category = _value_category(first)
    if not category:
        return False
    value = _last_value(region, category)
    if not value:
        return False
    after = set(_content_words(repl))
    for m in _WORD_RX.finditer(region[value.end():]):
        w = m.group(0).lower()
        if (w in after or w in _VALUE_UNITS or w in _TIME_OF_DAY
                or w in _PREP or _is_number_token(w) or _day_month(w)):
            continue
        return True
    return False


def _is_correction(out: str, cue: str, kind: str, rest: str,
                   pre_pause: bool, post_pause: bool) -> bool:
    if kind == "hard":
        # A dictation that is only a retraction keeps its words: "Never
        # mind." must not become empty output.
        if not out.strip() and not _rest_has_content(rest):
            return False
        return True
    if not out.strip():
        return False
    if not _rest_has_content(rest):
        return False
    evidence = _starts_replacement(out, rest)
    if _blocked(cue, rest) and not evidence:
        return False
    start, _ = _region_bounds(out)
    if _drops_words(out[start:], _replacement(rest)):
        return False
    body = out.rstrip()
    at_start = not body or body[-1] in ".!?…\n"
    if _STRONG.search(cue):
        # "make that 4" / "or rather Tuesday" need a value or a pause;
        # "I need you to make that call" and "change that tire" are verbs.
        if pre_pause or post_pause or at_start:
            return True
        return evidence or _value_category(_first_word(rest)) is not None
    # Everything else - bare "no", "wait", "actually", "sorry", "I mean",
    # "never mind" - needs a parallel swap. A pause alone is not enough:
    # "Red paint, no, blue" and "five dollars, no more, no less" are prose.
    return evidence


def _retract(out: str, cue: str, kind: str, rest: str) -> str:
    start, _ = _region_bounds(out)
    if kind == "hard":
        # "never mind that last part" retracts the last clause, not the whole
        # thought; "scratch that" keeps its original clause/sentence rule.
        if re.search(r"\blast\s+part\b", cue, _I) \
                and (not out.rstrip() or out.rstrip()[-1] not in ".!?…\n"):
            body = out[start:].rstrip(" \t,;:—–-")
            commas = list(re.finditer(r"[,;—–-]", body))
            keep = start + (commas[-1].end() if commas else 0)
            return out[:keep].rstrip(" \t,;:—–-")
        return out[:start].rstrip()
    region = out[start:]
    cut = start + _replace_cut(region, rest)
    return out[:cut].rstrip(" \t,;:—–-")


def _drop_repeat(prefix: str, rest: str) -> str:
    """'meet at' + 'at six' -> 'meet at six': don't say the anchor twice."""
    m = re.match(r"\s*([A-Za-z0-9]+(?:[-'’][A-Za-z0-9]+)*)\b\s*", rest)
    if not m:
        return rest
    words = _WORD_RX.findall(prefix.lower())
    if words and m.group(1).lower() == words[-1] and words[-1] in _ANCHORS | _CONJ:
        return rest[m.end():]
    return rest


def _cap_first(text: str) -> str:
    for i, ch in enumerate(text):
        if ch.isalpha():
            return text[:i] + ch.upper() + text[i + 1:]
    return text


def _self_correct(text: str) -> str:
    out, pos, cap_next = "", 0, False
    while True:
        m = _CUE.search(text, pos)
        if not m:
            tail = text[pos:]
            if cap_next:
                tail = _cap_first(tail)
            return (out + tail).strip()
        chunk = text[pos:m.start()]
        if cap_next and any(c.isalpha() for c in chunk):
            chunk = _cap_first(chunk)
            cap_next = False
        out += chunk
        cue, rest = m.group("cue"), text[m.end():]
        chain = m.group("chain") or ""
        post_pause = bool(m.group("pause"))
        pre_pause = bool(out.rstrip()) and out.rstrip()[-1] in ",;:—–-"
        kind = _cue_kind(cue, chain, rest, post_pause)
        if _is_correction(out, cue, kind, rest, pre_pause, post_pause):
            prefix = _retract(out, cue, kind, rest)
            body = prefix.rstrip()
            cap_next = not body or body[-1] in ".!?…\n"
            sep = " "
            if kind == "hard" and body \
                    and body[-1] not in ".!?…\n" and _rest_has_content(rest):
                sep = ", "
            out = prefix + (sep if prefix else "")
            trimmed = _drop_repeat(out, rest)
            if re.search(r"\b(?:make|change)\s+(?:that|it)\b", cue, _I):
                lead = re.match(r"\s*to\s+", trimmed, _I)
                if lead:
                    trimmed = trimmed[lead.end():]
            pos = m.end() + (len(rest) - len(trimmed))
        else:
            out += m.group(0)
            pos = m.end()


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
    Plain words go to the prompt; `heard=meant` pairs are fixed before the LLM.
    Entries end at a comma, a semicolon or a line break: a list typed one entry
    per line used to read as ONE entry, so the first misheard word came back as
    its fix plus the rest of the list."""
    words, aliases = [], []
    for item in re.split(r"[,;\r\n]", spec):
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
    text = _self_correct(text)
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
2. Remove fillers (um, uh, you know, like, "so" at sentence start), stutters and false starts. Apply every self-correction, including several in one dictation: "Tuesday, oh wait no, Wednesday" -> "Wednesday"; "eggs and milk, no, oat milk" -> "eggs and oat milk"; "never mind that last part" -> drop the thought before it. After "scratch that" / "strike that" / "actually" / "I mean" / "make that", KEEP the words that follow and delete the phrase before them. The words "no", "wait", "actually", "sorry", "never mind" and "I mean" are ordinary speech too: "No, I don't think so", "wait for me at the door", "I mean it", "Actually, the movie was better than I expected", "It's fine, actually, I like it" and "Thanks, never mind, I found it" stay as they are. Only drop one of these words when it introduces a correction of words said right before it.
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
in: let's meet Tuesday, oh wait no, Wednesday at 3
out: Let's meet Wednesday at 3.
in: grab eggs and milk, no, oat milk
out: Grab eggs and oat milk.
in: call Sarah. No, call Mark. Meet at five, no, six
out: Call Mark. Meet at six.
in: no, I don't think so
out: No, I don't think so.
in: wait for me at the door
out: Wait for me at the door.
in: I mean it
out: I mean it.
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


def _content_index(text: str) -> list[tuple[int, str]]:
    """Content words with their position, so the guard can talk about *which*
    occurrence of a word was dropped (and whether a cue licensed it)."""
    return [(m.start(), m.group(0)) for m in _WORD.finditer(text.lower())
            if m.group(0) not in _SKIP and len(m.group(0)) > 1]


def _forgiven_ranges(source: str) -> list[tuple[int, int]]:
    """Character ranges of the dictation a leftover correction cue licenses the
    cleanup model to drop.

    The prompt tells the model to delete the cue and the words the speaker took
    back, so a correct repair must not read as content loss: "Book the flight
    for Monday. Never mind, just book it for Tuesday." -> "Book the flight for
    Tuesday." drops Monday, and "Book the flight for Monday, let me rephrase
    that, book it for Tuesday." -> "Book it for Tuesday." drops the whole
    first clause. Both are the correction working.

    The span is the cue itself plus the thought it replaces, found with the
    same rule the pre-pass uses (region = the clause the cue interrupts; hard
    cues retract all of it, replace cues only the part the replacement
    supersedes). A word the replacement also says is not replaced by it, so it
    is still required. Words outside those spans were never corrected, so
    dropping them is still a failure.
    """
    out: list[tuple[int, int]] = []
    for m in _CUE.finditer(source):
        cue, rest = m.group("cue"), source[m.end():]
        chain = m.group("chain") or ""
        post_pause = bool(m.group("pause"))
        out.append((m.start(), m.end()))          # the cue words themselves
        if not _rest_has_content(rest):
            continue
        start, _ = _region_bounds(source[:m.start()])
        region = source[start:m.start()]
        if not region.strip():
            continue
        kind = _cue_kind(cue, chain, rest, post_pause)
        cut = 0 if kind == "hard" else _replace_cut(region, rest)
        after = set(_content_words(_replacement(rest)))
        base = start + cut
        for wm in _WORD.finditer(region[cut:].lower()):
            w = wm.group(0)
            if w in _SKIP or len(w) <= 1 or w in after:
                continue
            out.append((base + wm.start(), base + wm.end()))
    return out


def _respelled(word: str, have: set[str]) -> bool:
    """True if some output token is a close spelling of `word`: the model is
    allowed to fix a misheard name (Johnny -> Jonny), a plural, a tense."""
    for cand in have:
        if abs(len(cand) - len(word)) <= 2 and cand[0] == word[0] \
                and difflib.SequenceMatcher(None, word, cand).ratio() >= 0.75:
            return True
    return False


def _post_cue_lead(source: str, cleaned: str) -> bool:
    """The model's first kept word comes from after a leftover correction
    cue: the word before the cue may be the one the speaker retracted.
    "Red paint, no, blue" -> "Blue paint" is a repair, not a clipped opening;
    "Blue" alone loses the shared noun and is rejected. "No, call Mark" ->
    "Mark" still has nothing before the cue to retract."""
    m = _CUE.search(source)
    if not m or not source[:m.start()].strip():
        return False
    body = _content_words(cleaned)
    if not body:
        return False
    after = set(_content_words(source[m.end():]))
    if body[0] not in after:
        return False
    before = set(_content_words(source[:m.start()]))
    kept = sum(1 for w in body if w in before)
    return kept >= len(before) // 2


# Cue words that are also ordinary speech. The model may drop one only as part
# of a correction it actually made, i.e. some of the words the cue reaches back
# over are gone too. When everything before it survives, nothing was corrected
# and the word was meaning: "Actually, the movie was better", "It's fine,
# actually, I like it", "Thanks, never mind, I found it". These words are in
# _FILLERS, so the content-word count alone never notices them going missing.
_DISCOURSE = re.compile(r"\b(actually|i\s+mean|never\s*mind|sorry)\b", _I)


def _discourse_key(phrase: str) -> str:
    return re.sub(r"\s+", "", phrase.lower())


def _dropped_discourse(source: str, cleaned: str, kept) -> str:
    """The first discourse word the model dropped without correcting anything,
    or "". The span a cue reaches back over is the one the pre-pass uses; a
    filler-only clause ("Friday morning, um, sorry, Saturday") is skipped so
    the cue is judged against the words it actually corrects."""
    need: dict[str, int] = {}
    for m in _DISCOURSE.finditer(source):
        end = m.start()
        while True:
            start, _ = _region_bounds(source[:end])
            words = _content_words(source[start:m.start()])
            if words or start == 0:
                break
            end = start
        if all(kept(w) for w in words):
            k = _discourse_key(m.group(1))
            need[k] = need.get(k, 0) + 1
    have: dict[str, int] = {}
    for m in _DISCOURSE.finditer(cleaned):
        k = _discourse_key(m.group(1))
        have[k] = have.get(k, 0) + 1
    for k, n in need.items():
        if have.get(k, 0) < n:
            return k
    return ""


def guard(source: str, cleaned: str) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means the LLM lost real content.

    Three failure shapes are caught: bulk loss (many content words missing),
    truncation (the dictation's last content word is gone — the most common
    way a chat model "cleans" a trailing sentence out of existence), and a
    clipped opening (the first content word is gone — "Hey Sy, remind me"
    coming back as "Remind me"; under the bulk limit, so it went unnoticed
    until the Call made it audible on 09-05).

    Words a correction cue licenses the model to drop are not losses: the
    prompt asks it to remove the cue and the words the speaker took back.
    Without that, a correct repair reads as 4 dropped words and gets thrown
    away. Everything outside those spans must survive.
    """
    spans = _content_index(source)
    if not spans:
        return True, ""
    src = [w for _, w in spans]
    have = set(_WORD.findall(cleaned.lower()))
    # a source word counts as kept if it survives whole or as a prefix/suffix
    # of a joined token (get_user -> "get","user"; localpeer <- "local","peer")
    joined = cleaned.lower()
    forgiven = _forgiven_ranges(source)
    lost: list[int] = []
    for i, (pos, w) in enumerate(spans):
        if w in have or w in joined or _respelled(w, have):
            continue
        if any(a <= pos < b for a, b in forgiven):
            continue
        lost.append(i)
    limit = max(3, int(len(src) * 0.15))
    if len(lost) > limit:
        return False, f"{len(lost)}/{len(src)} content words dropped"
    if lost and lost[-1] == len(spans) - 1:
        return False, f"trailing word dropped: {src[-1]!r}"
    if lost and lost[0] == 0 and not _post_cue_lead(source, cleaned):
        return False, f"leading word dropped: {src[0]!r}"
    gone = _dropped_discourse(
        source, cleaned, lambda w: w in have or w in joined or _respelled(w, have))
    if gone:
        return False, f"discourse word dropped: {gone!r}"
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


def fit_context(text: str, before: str, style: str, dictionary_words=(),
                keep_period: bool = False) -> str:
    """Deterministic finish after the LLM: casing at a mid-sentence cursor and
    trailing-period conventions the model may have ignored.

    ``keep_period=True`` turns off the chat convention that drops the period
    from a lone sentence, for people who punctuate their messages."""
    if not text:
        return text
    first = text.split()[0] if text.split() else ""
    if (mid_sentence(before) or style == "code") and text[0].isupper() \
            and not _keep_case(first, dictionary_words):
        text = text[0].lower() + text[1:]
    if style == "code":
        if "\n" not in text and text.endswith(".") and not text.endswith(".."):
            text = text[:-1]
    elif style == "message" and not keep_period:
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
