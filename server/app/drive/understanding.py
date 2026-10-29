"""Understanding spoken, disfluent driving requests - offline and rule-based.

People think out loud: "take me to, um, Delhi - actually no, Jaipur", "not
Pune, Nashik", "from Mumbai to Goa and, uh, stop for fuel on the way". This
module turns such an utterance into structured intent: the *final*
destination (earlier ones are reported as abandoned, never acted on), the
origin, stops added or dropped, route preferences, saved places ("my office is
in Noida") and questions (ETA, distance, status).

Nothing is tied to particular cities: places come from the gazetteer (every
Indian city and town with 1,000+ people). A single-word name is only accepted
in a *place slot* - after "to", "from", "in", "instead", "actually", "not"... or
at the start of the utterance - because many town names are also ordinary
words ("Got", "May", "Than"). Multi-word names ("Navi Mumbai") match anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..speech_control import is_hold
from . import stops as stop_words
from .geo import Place, gazetteer, normalise

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "hmm", "hm", "mm", "ah", "eh", "oh", "like"}
ARTICLES = {"the", "a", "an", "my", "our"}
# A strong cue right before a name makes it a place ("to Got" is the town);
# weaker positions (utterance start, after a comma, "me", "and"...) also accept
# a name, but never an ordinary English word ("May I...", "got to go").
STRONG_CUES = {"to", "towards", "toward", "till", "until", "reach", "visit", "in", "at", "from", "via",
               "through", "near", "instead", "not", "rather", "destination", "into", "reroute", "redirect", "except"}
FUNCTION_WORDS = {"of", "the", "a", "an", "my", "our", "me", "us", "i", "you", "we", "it", "for", "with", "on",
                  "by", "please", "and", "or", "to", "in", "at", "from", "is", "so", "then", "there", "here"}
COMMON_WORDS = set("""a about after again all also am an and any are as at away back be because been before being
best better between big both but by call can car change come could day do does done drive driving each eat end
even find first for from fuel get give go going good got great had has have he head her here high highway him his
home how i if in instead into is it its just keep know last late left let like long look made make many may me mind
more most much must my near need never new next nice no not now of off office ok okay on once one only or other our
out over park place please plan put quick rather reach really right road route safe same save say see set she
should show side slow so some soon start stay still stop straight such take tea than thank thanks that the their
them then there these they thing think this those through time to today too toll town turn two up us use very via
wait want was way we well went were what when where which while who why will with work would yes yet you your
coffee food lunch dinner charge rest break fill petrol diesel gas station city village hotel school college gym
market temple beach hill fort lake river bridge airport port junction cross main north south east west hello hi
hey sorry mean actually never cancel""".split())
# words that open a place slot when they come right before a name
SLOT_CUES = {
    "to", "towards", "toward", "till", "until", "reach", "visit", "in", "at", "from", "via", "through",
    "near", "instead", "actually", "no", "not", "rather", "make", "it", "its", "is", "as", "go", "drive",
    "head", "navigate", "change", "switch", "wait", "sorry", "mean", "and", "or", "then", "for", "into",
    "reroute", "redirect", "destination", "correction", "also", "except", "me", "us", "of", "maybe", "be",
    "skip", "remove", "delete",
}
CORRECTION_CUES = ("actually", "instead", "rather", "i mean", "no wait", "wait", "sorry", "scratch that",
                   "make that", "make it", "change it to", "change that to", "switch to", "no no", "correction")
ORIGIN_CUES = ("from", "starting from", "start from", "starting in", "i'm in", "i am in", "we're in", "we are in",
               "i'm at", "i am at", "currently in", "leaving from", "coming from")
NEGATION_BEFORE = {"not", "except"}
# "to <verb>" is not a destination ("I need to get fuel")
VERBS_AFTER_TO = {"get", "find", "stop", "eat", "have", "grab", "fill", "charge", "know", "go", "be", "take",
                  "reach", "see", "make", "add", "change", "cancel", "avoid", "use", "drive", "save", "set",
                  "check", "tell", "hear", "do", "pick", "drop", "buy", "rest", "refuel", "head", "visit",
                  # "I want to travel from Pune" ("travel" is one letter from the town Tral)
                  "travel", "travelling", "traveling", "fly", "commute", "ride", "move", "come", "return",
                  "leave", "start", "depart", "relocate", "shift", "plan", "book", "add", "stay", "spend"}

# Words that name a kind of stop are never place names ("stop for chai", not the town Chai).
STOP_KEYWORDS = {**stop_words.STRONG, **stop_words.WEAK}
STOP_REMOVERS = stop_words.REMOVERS
# A place right after one of these is a stop on the way, not the destination:
# "via Nashik", "stop at Lonavala", "passing through Khandala".
VIA_CUES = ("via", "through", "passing through", "pass through", "going through", "stop at", "stops at",
            "stop in", "stopping at", "stopping in", "stop by", "stopover at", "stopover in", "stop over at",
            "halt at", "break at", "break in", "stop near", "break the journey at", "break the journey in")
VIA_REMOVERS = {"skip", "remove", "delete"}  # "skip Lonavala" drops the stop; "forget Agra" stays a correction
SAVED_LABELS = {"home", "office", "work", "gym", "school", "college", "hotel", "parents", "hostel"}
LABEL_ALIASES = {"work": "office", "house": "home", "place": "home"}


@dataclass
class Mention:
    text: str
    start: int
    end: int
    role: str  # "to" | "from" | "via" (a stop on the way)
    place: Place | None = None
    saved_label: str | None = None  # "home", "office"... resolved later against saved places
    negated: bool = False


@dataclass
class Understanding:
    text: str
    destination: Mention | None = None
    abandoned: list[Mention] = field(default_factory=list)
    origin: Mention | None = None
    unresolved: str | None = None  # a spoken destination the map does not know
    # Stops as specs (see stops.py): "fuel", "custom:medicines", "place@<id>", "food@<id>".
    # A spec appears once per stop: two snack stops are ["snacks", "snacks"].
    add_stops: list[str] = field(default_factory=list)
    remove_stops: list[str] = field(default_factory=list)
    abandoned_stops: list[str] = field(default_factory=list)
    more_stops: list[str] = field(default_factory=list)  # "another fuel stop": add on top of existing ones
    timed_stops: bool = False  # "one in the morning, one in the evening"
    avoid_highways: bool | None = None
    save_label: str | None = None  # "save this as office" / "my office is in Noida"
    save_place: Mention | None = None
    cancel: bool = False
    eta: bool = False
    distance: bool = False
    status: bool = False
    resume: bool = False
    repeat: bool = False
    greeting: bool = False
    hold: bool = False  # "hold on", "stop": go quiet and wait, not a request
    corrected: bool = False

    @property
    def is_navigation(self) -> bool:
        return bool(self.destination or self.origin or self.unresolved or self.add_stops or self.remove_stops
                    or self.avoid_highways is not None or self.save_label or self.cancel)

    @property
    def is_question(self) -> bool:
        return self.eta or self.distance or self.status


def tokenize(text: str) -> list[str]:
    text = text.lower()
    text = re.sub(r"[—–]|--|\.\.\.+|[,;:!?]|\s-\s|(?<=\w)\.(?=\s|$)", " , ", text)
    text = text.replace("’", "'")
    text = re.sub(r"[^a-z0-9', ]+", " ", text)
    tokens = [t for t in text.split() if t]
    out: list[str] = []
    for t in tokens:
        if t in FILLERS:
            continue
        if t == "," and (not out or out[-1] == ","):
            continue
        out.append(t)
    # "you know" is a filler phrase
    joined = " ".join(out)
    joined = re.sub(r"\byou know\b", " ", joined)
    return [t for t in joined.split() if t]


def _prev_word(tokens: list[str], i: int) -> tuple[str | None, int]:
    """The nearest word before i, skipping articles; commas count as a slot boundary."""
    j = i - 1
    while j >= 0 and tokens[j] in ARTICLES:
        j -= 1
    return (tokens[j], j) if j >= 0 else (None, -1)


def _in_slot(tokens: list[str], i: int, end: int) -> bool:
    """Is tokens[i:end] in a position where a place name is expected?"""
    prev, _ = _prev_word(tokens, i)
    if prev in STRONG_CUES:
        return True
    weak = prev is None or prev == "," or prev in SLOT_CUES or (end < len(tokens) and tokens[end] in {"instead", "please"})
    return weak and not (end - i == 1 and tokens[i] in COMMON_WORDS)


def _role(tokens: list[str], i: int, end: int | None = None) -> str:
    before = " " + " ".join(tokens[max(0, i - 4):i]) + " "
    before = re.sub(r"\b(the|a|an|my|our)\b", " ", before)
    before = re.sub(r"\s+", " ", before)
    if any(before.endswith(f" {cue} ") for cue in ORIGIN_CUES):
        return "from"
    if any(before.endswith(f" {cue} ") for cue in VIA_CUES):
        return "via"
    prev, j = _prev_word(tokens, i)
    if prev in VIA_REMOVERS:
        return "via"  # "skip Lonavala", "remove the Lonavala stop"
    # "stop for lunch at Lonavala", "fuel in Khopoli": a stop request just before "at/in <town>"
    if prev in {"at", "in", "near", "by"}:
        window = [t for t in tokens[max(0, j - 4):j] if t != ","]
        if window and (set(window) & stop_words.STOP_NOUNS or stop_words.category_of(window, in_request=False)):
            return "via"
    # "the Lonavala stop", "a Khandala break"
    if end is not None and end < len(tokens) and tokens[end] in stop_words.STOP_NOUNS:
        return "via"
    return "to"


def _negated(tokens: list[str], i: int) -> bool:
    prev, j = _prev_word(tokens, i)
    if prev in NEGATION_BEFORE:
        return True
    return j >= 1 and tokens[j - 1:j + 1] in (["instead", "of"], ["rather", "than"])


def _find_places(tokens: list[str]) -> tuple[list[Mention], str | None]:
    gz = gazetteer()
    mentions: list[Mention] = []
    unresolved: str | None = None
    i = 0
    while i < len(tokens):
        if tokens[i] == ",":
            i += 1
            continue
        match: Mention | None = None
        # saved labels: "home", "my office", "to work"
        label = LABEL_ALIASES.get(tokens[i], tokens[i])
        prev, _ = _prev_word(tokens, i)
        if label in SAVED_LABELS and (prev is None or prev in SLOT_CUES or prev == ","):
            match = Mention(tokens[i], i, i + 1, _role(tokens, i, i + 1), saved_label=label)
        if match is None:
            for n in range(min(gz.max_words, 4), 0, -1):
                window = tokens[i:i + n]
                if len(window) < n or "," in window:
                    continue
                phrase = " ".join(window)
                place = gz.exact(phrase)
                if place and (n > 1 or _in_slot(tokens, i, i + n)):
                    match = Mention(phrase, i, i + n, _role(tokens, i, i + n), place=place)
                    break
        if match is None and _in_slot(tokens, i, i + 1) and tokens[i] not in SLOT_CUES | ARTICLES | VERBS_AFTER_TO:
            # a near-miss spelling in a place slot ("jaipor", "coimbatur")
            for n in (2, 1):
                window = tokens[i:i + n]
                if len(window) == n and "," not in window and not set(window) & (SLOT_CUES | FUNCTION_WORDS | COMMON_WORDS | set(STOP_KEYWORDS)):
                    place = gz.fuzzy(" ".join(window))
                    if place:
                        match = Mention(" ".join(window), i, i + n, _role(tokens, i, i + n), place=place)
                        break
        if match:
            match.negated = _negated(tokens, i)
            # "stop at Lonavala and Khandala": a place listed right after a stop is a stop too
            prev, _ = _prev_word(tokens, i)
            if match.role == "to" and prev in {"and", ",", "then"} and mentions and mentions[-1].role == "via" \
                    and mentions[-1].end >= i - 2:
                match.role = "via"
            mentions.append(match)
            i = match.end
            continue
        i += 1

    # "take me to Zzyzx": a destination was spoken but the map does not know it
    for k, tok in enumerate(tokens[:-1]):
        if tok in {"to", "towards", "reach", "visit"} and not any(m.start in range(k + 1, k + 3) for m in mentions):
            nxt = [t for t in tokens[k + 1:k + 4] if t not in ARTICLES]
            words = []
            for t in nxt:
                if t == "," or t in SLOT_CUES or t in VERBS_AFTER_TO or t in STOP_KEYWORDS or t in stop_words.STOP_NOUNS \
                        or t in {"me", "us", "please", "there", "here", "it", "that", "this", "two"}:
                    break
                words.append(t)
            if words and not any(w in {"a", "stop", "the"} for w in words):
                unresolved = " ".join(words)
    return mentions, unresolved


def _correction_positions(tokens: list[str]) -> list[int]:
    text = " ".join(tokens)
    positions = []
    for cue in CORRECTION_CUES:
        for m in re.finditer(rf"\b{re.escape(cue)}\b", text):
            positions.append(len(text[:m.start()].split()))
    # "no, <something>" corrects what came before; "not X" is a negation, handled separately
    for k, t in enumerate(tokens[:-1]):
        if t == "no" and tokens[k + 1] == ",":
            positions.append(k)
    return sorted(positions)


@dataclass
class _StopResult:
    add: list[str]
    remove: list[str]
    abandoned: list[str]
    more: list[str]
    timed: bool


def _stops(tokens: list[str], corrections: list[int], mentions: list[Mention]) -> _StopResult:
    request = stop_words.parse(tokens, {m.start: m.end for m in mentions})
    items = list(request.items)

    # Named places: "via Nashik", "stop at Lonavala" - or a stop anchored there ("lunch at Lonavala").
    for m in (m for m in mentions if m.role == "via" and m.place is not None):
        prev, _ = _prev_word(tokens, m.start)
        removed = (m.negated or prev in VIA_REMOVERS or stop_words.removed_before(tokens, m.start)
                   or _negated_stop(tokens, m.start))
        host = next((it for it in items if "@" not in it.spec and it.end <= m.start <= it.end + 2
                     and it.end < len(tokens) and tokens[it.end] in {"at", "in", "near", "by", "over"}), None)
        if host is not None and not removed:
            host.spec = f"{host.spec}@{m.place.place_id}"
        else:
            items.append(stop_words.StopItem(m.start, m.end, f"place@{m.place.place_id}", 1, False, removed))
    items.sort(key=lambda it: it.pos)

    live = [it for it in items if not it.removed]
    # "stop for coffee, actually no, fuel": a correction between two stops drops the earlier one
    abandoned = [it for idx, it in enumerate(live)
                 if any(it.pos < c < later.pos for c in corrections for later in live[idx + 1:])]
    removed_specs = sorted({_removal_key(it.spec) for it in items if it.removed})
    kept = [it for it in live if it not in abandoned and not matches_removal(it.spec, removed_specs)]

    # The same stop said twice is one stop ("stop for fuel ... yes, fuel"); a count or
    # "another" is what makes several.
    order: list[str] = []
    base: dict[str, int] = {}
    extra: dict[str, int] = {}
    for it in kept:
        if it.spec not in order:
            order.append(it.spec)
        if it.additive:
            extra[it.spec] = extra.get(it.spec, 0) + it.count
        else:
            base[it.spec] = max(base.get(it.spec, 0), it.count)
    add: list[str] = []
    for spec in order:
        add += [spec] * (base.get(spec, 0) + extra.get(spec, 0))
    more = [spec for spec in order if spec not in base]
    abandoned_specs = list(dict.fromkeys(it.spec for it in abandoned))
    return _StopResult(add, removed_specs, abandoned_specs, more, request.timed and bool(add))


def _negated_stop(tokens: list[str], start: int) -> bool:
    """ "not via Nashik", "don't stop at Lonavala", "no need to stop at Lonavala" - but not the
    correction "actually no, stop at Behror", where "no" is followed by a pause."""
    between_ok = {"via", "through", "stop", "stops", "stopping", "at", "in", "by", "near", "need", "to", "want",
                  "the", "a", "any", "halt", "break", "over", "going", "pass", "passing", "go", "drive"}
    for w in range(max(0, start - 5), start):
        if tokens[w] in {"not", "no", "dont", "don't", "without", "avoid", "never"} and tokens[w + 1] != "," \
                and all(t in between_ok for t in tokens[w + 1:start]):
            return True
    return False


def _removal_key(spec: str) -> str:
    """What a dropped stop removes: a category drops every stop of that kind, a town every stop there."""
    if spec.startswith("place@"):
        return "@" + spec.split("@", 1)[1]
    return spec.split("@", 1)[0]


def matches_removal(spec: str, removals: list[str]) -> bool:
    return any(spec.endswith(r) if r.startswith("@") else spec.split("@", 1)[0] == r for r in removals)


def _save_request(tokens: list[str], mentions: list[Mention]) -> tuple[str | None, Mention | None]:
    text = " ".join(tokens)
    m = re.search(r"\b(?:save|remember|mark) (?:this|it|that|here|the destination|this place)? ?as (?:my |the |a )?([a-z]+)", text)
    if m:
        label = LABEL_ALIASES.get(m.group(1), m.group(1))
        mentions[:] = [x for x in mentions if x.saved_label != label]
        return label, None  # "this" = the current destination
    m = re.search(r"\b(?:my|our) ([a-z]+) is (?:in|at|near) ", text) or re.search(r"\bset (?:my |the )?([a-z]+) (?:to|as) ", text)
    if m:
        label = LABEL_ALIASES.get(m.group(1), m.group(1))
        after = len(text[:m.end()].split())
        target = next((x for x in mentions if x.start >= after - 1 and x.place is not None), None)
        if target:
            mentions[:] = [x for x in mentions if x is not target and x.saved_label != label]
            return label, target
    return None, None


def understand(text: str) -> Understanding:
    tokens = tokenize(text)
    joined = " ".join(t for t in tokens if t != ",")
    u = Understanding(text=joined)
    corrections = _correction_positions(tokens)
    u.corrected = bool(corrections)

    mentions, u.unresolved = _find_places(tokens)
    u.save_label, u.save_place = _save_request(tokens, mentions)

    origins = [m for m in mentions if m.role == "from" and not m.negated]
    u.origin = origins[-1] if origins else None
    destinations = [m for m in mentions if m.role == "to"]
    live = [m for m in destinations if not m.negated]
    if live:
        u.destination = live[-1]
        u.abandoned = [m for m in destinations if m is not u.destination]
        u.unresolved = None
    stops = _stops(tokens, corrections, mentions)
    u.add_stops, u.remove_stops, u.abandoned_stops = stops.add, stops.remove, stops.abandoned
    u.more_stops, u.timed_stops = stops.more, stops.timed

    if re.search(r"\b(avoid|no|skip|without) (the )?(highways?|tolls?|expressways?)\b|\b(toll|highway)[ -]free\b", joined):
        u.avoid_highways = True
    elif re.search(r"\b(use|take) (the )?(highways?|expressways?)\b|\b(tolls?|highways?) (are|is) (fine|ok|okay)\b", joined):
        u.avoid_highways = False
    u.cancel = bool(re.search(r"\b(cancel|stop|end|abort|quit)( the| this| my)? (navigation|trip|route|drive|journey|ride)\b"
                              r"|\bstop navigating\b|^(never mind|cancel|cancel it|forget it)$", joined))
    u.eta = bool(re.search(r"\b(eta|how long|how much (more )?time|what time (will|do|would)|when (will|do|would|can) (we|i)"
                           r"|arrival time|time (left|remaining)|how many (hours|minutes))\b", joined))
    u.distance = bool(re.search(r"\b(how far|distance|how many (km|kms|kilometers|kilometres))\b", joined))
    u.status = bool(re.search(r"\bwhere (are we|am i) (going|headed|heading)\b|\bwhat'?s the (plan|route)\b|\b(current|my) route\b", joined))
    u.resume = bool(re.search(r"^(ok |okay |yes |yeah )?(go on|continue|carry on|keep going|go ahead|you were saying|finish that|and then)\b", joined))
    u.repeat = bool(re.search(r"\b(repeat that|repeat it|say (that|it) again|come again|what did you say|pardon)\b|^repeat$", joined))
    u.hold = is_hold(text) and not u.is_navigation
    u.greeting = bool(re.search(r"^(hi|hello|hey|namaste|good (morning|evening|afternoon))\b|\b(help|what can you do)\b", joined))
    return u
