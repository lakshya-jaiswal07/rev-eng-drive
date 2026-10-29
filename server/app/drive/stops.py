"""Understanding stop requests: any kind of stop, how many, and where.

People ask for stops in their own words: "stop for fuel", "a quick chai break",
"grab some samosas on the way", "two stops for snacks, one in the morning and
one in the evening", "stop for lunch at Lonavala", "via Nashik". Three layers:

1. **Sentence shapes**, so any word works: whatever fills "stop for ___",
   "a ___ stop/break", "get ___ on the way" or "somewhere to ___" becomes a
   stop, even a word no list contains ("stop for medicines" -> a *medicines*
   stop, shown with the driver's own words).
2. **A lexicon of stop words** (snacks, chai, petrol, pharmacy, ATM...), with
   plurals and near-miss spellings, which gives a known stop its category -
   icon, typical duration and a sensible point along the route.
3. **Counts and places**: "two snack stops" are two stops, spread along the
   way; "at/in/near <town>" anchors a stop to that town, and the route passes
   through it.

A stop is encoded as one string so the runtime can keep stops as plain facts:
``"fuel"`` (a category), ``"custom:medicines"`` (the driver's own words),
``"place@<place_id>"`` (stop in a named town) or ``"food@<place_id>"`` (a
category at a named town). Offline and deterministic, like the rest of Drive.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

# -- lexicon -----------------------------------------------------------------
# Strong words mean a stop wherever they are said ("I'm hungry", "need petrol").
STRONG: dict[str, str] = {
    # fuel
    "fuel": "fuel", "petrol": "fuel", "diesel": "fuel", "gas": "fuel", "refuel": "fuel", "cng": "fuel",
    "pump": "fuel", "refuelling": "fuel", "refueling": "fuel",
    # EV charging
    "charge": "charging", "charging": "charging", "charger": "charging", "ev": "charging",
    "recharge": "charging", "supercharger": "charging",
    # meals
    "food": "food", "eat": "food", "lunch": "food", "dinner": "food", "breakfast": "food", "brunch": "food",
    "meal": "food", "dhaba": "food", "restaurant": "food", "hungry": "food", "thali": "food", "biryani": "food",
    "dosa": "food", "idli": "food", "paratha": "food", "buffet": "food",
    # snacks
    "snack": "snacks", "snacks": "snacks", "nashta": "snacks", "naashta": "snacks", "samosa": "snacks",
    "vadapav": "snacks", "pakora": "snacks", "pakoda": "snacks", "bhajji": "snacks", "bhaji": "snacks",
    "chaat": "snacks", "kachori": "snacks", "sandwich": "snacks", "chips": "snacks", "biscuits": "snacks",
    "munchies": "snacks", "refreshments": "snacks", "tiffin": "snacks", "maggi": "snacks",
    # hot drinks
    "coffee": "coffee", "chai": "coffee", "tea": "coffee", "cafe": "coffee", "cappuccino": "coffee",
    "espresso": "coffee",
    # cold drinks
    "juice": "drinks", "lassi": "drinks", "drinks": "drinks", "buttermilk": "drinks", "chaas": "drinks",
    "nimbu": "drinks", "soda": "drinks",
    # breaks
    "restroom": "rest", "washroom": "rest", "toilet": "rest", "bathroom": "rest", "loo": "rest", "break": "rest",
    # errands
    "pharmacy": "pharmacy", "chemist": "pharmacy", "medicine": "pharmacy", "medicines": "pharmacy",
    "atm": "atm", "groceries": "shopping", "grocery": "shopping", "supermarket": "shopping", "shopping": "shopping",
}
# Weak words are only a stop inside a stop request ("stop for water"), never on
# their own ("I can see the water").
WEAK: dict[str, str] = {
    "water": "drinks", "drink": "drinks", "coconut": "drinks", "cold": "drinks",
    "rest": "rest", "stretch": "rest", "nap": "rest", "sleep": "rest", "leg": "rest", "legs": "rest",
    "cash": "atm", "money": "atm", "withdraw": "atm",
    "mall": "shopping", "market": "shopping", "fruits": "shopping", "fruit": "shopping", "vegetables": "shopping",
    "sweets": "snacks", "mithai": "snacks", "icecream": "snacks", "dessert": "snacks", "bite": "snacks",
    "something": "snacks",  # "a stop to get something to eat" is caught by "eat"; alone it is a snack
    "tablets": "pharmacy", "medical": "pharmacy", "first": "pharmacy",
    "eatery": "food",
}
PHRASES: dict[str, str] = {
    "vada pav": "snacks", "ice cream": "snacks", "coconut water": "drinks", "cold drink": "drinks",
    "medical store": "pharmacy", "first aid": "pharmacy", "petrol pump": "fuel", "gas station": "fuel",
    "petrol station": "fuel", "fuel station": "fuel", "charging station": "charging", "rest stop": "rest",
    "pit stop": "rest", "food court": "food", "tea stall": "coffee", "chai stall": "coffee",
    "cash machine": "atm", "grocery store": "shopping", "stretch my legs": "rest", "stretch our legs": "rest",
    "bite to eat": "snacks", "something to eat": "food", "something to drink": "drinks",
}
_FUZZY_KEYS = sorted(set(STRONG) | set(WEAK))

STOP_NOUNS = {"stop", "stops", "break", "breaks", "halt", "halts", "stopover", "stopovers", "pitstop"}
NUMBERS = {"one": 1, "a": 1, "an": 1, "single": 1, "two": 2, "three": 3, "four": 4, "five": 5,
           "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
ADD_VERBS = {"add", "need", "want", "make", "take", "include", "plan", "put", "have", "schedule", "insert",
             "keep", "like", "planning"}
GET_VERBS = {"get", "grab", "buy", "pick", "have", "fetch", "eat", "drink", "use", "fill", "take", "do", "see"}
ADDITIVE = {"another", "extra", "additional", "second", "more"}
# Said within three words before a stop, these drop it ("skip the coffee stop"). A bare "no"/"not"
# only counts right before it ("no coffee"): "actually no, fuel" is a correction, not a removal.
REMOVERS = {"skip", "cancel", "remove", "without", "drop", "forget", "dont", "don't", "delete", "never"}
ADJECTIVES = {"quick", "short", "small", "little", "proper", "long", "brief", "good", "nice", "big", "quickly", "fast"}
# Words that end the "___" in "stop for ___".
BOUNDARY = {",", "on", "in", "at", "near", "before", "after", "when", "while", "then", "please", "too", "also",
            "so", "because", "around", "by", "during", "if", "to", "from", "towards", "till", "until", "somewhere",
            "along", "en", "which", "that", "where", "maybe", "with", "every", "each", "for", "via", "through",
            "but", "is", "are", "be", "and", "or", "as", "once", "first", "last", "next"}
# Fillers inside the "___" that are not the thing itself.
JUNK = {"a", "an", "some", "the", "quick", "little", "small", "short", "bit", "of", "few", "couple", "more",
        "extra", "another", "good", "nice", "proper", "cup", "cups", "glass", "glasses", "plate", "plates",
        "packet", "packets", "bottle", "bottles", "my", "our", "us", "me", "few", "any", "kind", "sort",
        "get", "grab", "buy", "pick", "up", "have", "fetch", "go", "quickly", "maybe", "just", "really"}
# A "___" that is only one of these is not a stop for anything in particular.
VAGUE = {"now", "later", "sometime", "while", "moment", "second", "minute", "minutes", "it", "this", "that",
         "them", "there", "here", "you", "way", "route", "trip", "journey", "navigation", "talking", "time",
         "everything", "anything", "nothing", "reason", "one", "two", "today", "tomorrow", "us", "me"}
# "stop" followed by one of these is "stop talking" / "stop the navigation", not a stop on the way.
NOT_A_STOP_AFTER = {"talking", "it", "this", "that", "now", "please", "listening", "here", "there",
                    "navigation", "navigating", "speaking", "everything", "driving", "me", "us"}
NOT_A_STOP_AFTER_THE = {"navigation", "trip", "route", "car", "music", "talking", "drive", "journey"}
ARTICLES = {"the", "a", "an", "my", "our", "your", "this", "that"}
ON_THE_WAY = (("on", "the", "way"), ("along", "the", "way"), ("on", "our", "way"), ("on", "my", "way"),
              ("en", "route"), ("on", "route"), ("on", "the", "road"), ("on", "the", "drive"), ("in", "between"))
TIMED = re.compile(r"\b(?:one|1|another|the other|a second one)\s+(?:in|at|around|near|by|for|during|before|after|towards?)\s+"
                   r"(?:the\s+)?(?:morning|afternoon|evening|night|noon|midday|start|beginning|end|middle|way|halfway)\b")
TIME_WORDS = re.compile(r"\b(morning|afternoon|evening|night|noon|midday|sunrise|sunset)\b")


def _singular(word: str) -> list[str]:
    forms = [word]
    if word.endswith("ies") and len(word) > 4:
        forms.append(word[:-3] + "y")
    if word.endswith("es") and len(word) > 3:
        forms.append(word[:-2])
    if word.endswith("s") and len(word) > 2:
        forms.append(word[:-1])
    return forms


def category_of(words: list[str], *, in_request: bool) -> str | None:
    """The stop category for some words, if they name a known kind of stop.

    Outside a stop request only strong words count; inside one ("stop for ___")
    weak words and near-miss spellings ("snaks", "petrl") count too.
    """
    joined = " ".join(words)
    for phrase, cat in PHRASES.items():
        if re.search(rf"\b{re.escape(phrase)}\b", joined):
            return cat
    for w in words:
        for form in _singular(w):
            if form in STRONG:
                return STRONG[form]
            if in_request and form in WEAK:
                return WEAK[form]
    if in_request:
        for w in words:
            if len(w) >= 5:
                close = difflib.get_close_matches(w, _FUZZY_KEYS, n=1, cutoff=0.84)
                if close:
                    return STRONG.get(close[0]) or WEAK[close[0]]
    return None


@dataclass
class StopItem:
    """One stop asked for in an utterance."""
    pos: int            # token index, for ordering and for corrections between stops
    end: int            # token index just after the words naming it
    spec: str           # "fuel", "custom:medicines", "place@123", "food@123"
    count: int = 1
    additive: bool = False  # "another fuel stop": on top of the stops the trip already has
    removed: bool = False


@dataclass
class StopRequest:
    items: list[StopItem]
    timed: bool  # "one in the morning, one in the evening": placement by time of day was asked for


def removed_before(tokens: list[str], start: int) -> bool:
    """Is the stop starting at ``start`` being dropped rather than added?"""
    if start > 0 and tokens[start - 1] in {"no", "not"}:
        return True
    if any(w in REMOVERS for w in tokens[max(0, start - 3):start]):
        return True
    return tokens[start + 1:start + 3] == ["not", "needed"]


def _read_items(tokens: list[str], i: int, blocked: set[int]) -> tuple[list[tuple[int, int, list[str]]], int]:
    """Read the "___ and ___" after "stop for": [(start, end, words)], and where reading stopped."""
    items: list[tuple[int, int, list[str]]] = []
    current: list[str] = []
    start = i
    n = len(tokens)
    while i < n and len(items) < 4:
        t = tokens[i]
        if i in blocked:  # a town name ends it: "stop for lunch at Lonavala"
            break
        if t in {"and", "or"}:
            if current:
                items.append((start, i, current))
            current, start = [], i + 1
            i += 1
            continue
        if t == "to" and current and i + 1 < n and tokens[i + 1] in {"eat", "drink"}:
            current += ["to", tokens[i + 1]]  # "a bite to eat", "something to drink"
            i += 2
            continue
        if t in BOUNDARY or t in STOP_NOUNS or (t in NUMBERS and t not in {"a", "an"}) or len(current) >= 4:
            break
        current.append(t)
        i += 1
    if current:
        items.append((start, i, current))
    return items, i


def _item_spec(words: list[str], *, allow_custom: bool = True) -> str | None:
    """What one "___" asks to stop for: a category, or the driver's own words."""
    cat = category_of(words, in_request=True)
    if cat:
        return cat
    content = [w for w in words if w not in JUNK]
    if not allow_custom or not content or all(w in VAGUE for w in content) or len(content) > 3:
        return None
    label = " ".join(content)
    return f"custom:{label}" if re.fullmatch(r"[a-z][a-z' ]*", label) else None


def _count_before(toks: list[str], j: int) -> tuple[int | None, bool, int]:
    """A count ("two", "a", "a couple of") and "another" ending at position j.

    Returns (count or None, additive, index where the stop request begins).
    """
    count: int | None = None
    additive = False
    k = j
    while k >= 0 and toks[k] in ADJECTIVES:
        k -= 1
    if k >= 0 and toks[k] in NUMBERS:
        count = NUMBERS[toks[k]]
        k -= 1
    elif k >= 1 and toks[k - 1:k + 1] == ["couple", "of"]:
        count = 2
        k -= 2
        if k >= 0 and toks[k] == "a":
            k -= 1
    if k >= 0 and toks[k] in ADDITIVE:
        additive = True
        k -= 1
    elif k >= 1 and toks[k - 1:k + 1] == ["one", "more"]:
        additive, count = True, count or 1
        k -= 2
    return count, additive, k + 1


def _on_the_way_at(toks: list[str], x: int) -> int:
    """Length of an "on the way"-like phrase starting at x, or 0."""
    for p in ON_THE_WAY:
        if tuple(toks[x:x + len(p)]) == p:
            return len(p)
    return 0


def parse(tokens: list[str], place_spans: dict[int, int]) -> StopRequest:
    """All the stops asked for in an utterance.

    ``place_spans`` maps the start token of every place mention to its end, so a
    town name is never read as the thing to stop for.
    """
    toks = list(tokens)
    n = len(toks)
    # A recogniser hears "add two stops" as "add to stops".
    for k in range(n - 1):
        if toks[k] == "to" and toks[k + 1] in {"stops", "breaks", "halts", "stopovers"} and (
                k == 0 or toks[k - 1] in ADD_VERBS | {"with", ",", "also", "and", "us", "me"}):
            toks[k] = "two"
    blocked = {j for s, e in place_spans.items() for j in range(s, e)}
    covered: set[int] = set()
    items: list[StopItem] = []

    def add(start: int, end: int, spec: str, count: int, additive: bool, removed: bool) -> None:
        items.append(StopItem(start, end, spec, max(1, count), additive, removed))
        covered.update(range(start, end))

    # 1. "a ___ stop", "stop for ___", "two stops for ___", "stop to get ___", "add a stop"
    for k, t in enumerate(toks):
        if t not in STOP_NOUNS or k in blocked:
            continue
        nxt = toks[k + 1] if k + 1 < n else ""
        nxt2 = toks[k + 2] if k + 2 < n else ""
        if t in {"stop", "halt"} and (nxt in NOT_A_STOP_AFTER or (nxt == "the" and nxt2 in NOT_A_STOP_AFTER_THE)):
            continue  # "stop talking", "stop the navigation"
        # words right before the noun that name the stop: "coffee stop", "snack break"
        j, pre = k - 1, []
        while j >= 0 and len(pre) < 2 and j not in blocked and toks[j] not in (
                BOUNDARY | set(NUMBERS) | ADD_VERBS | REMOVERS | STOP_NOUNS | ADDITIVE | ARTICLES | {"no", "not"}):
            if toks[j] not in ADJECTIVES:
                pre.insert(0, toks[j])
            j -= 1
        pre_cat = category_of(pre, in_request=True) if pre else None  # "bus stop", "last stop": not a request
        named_from = j + 1 if pre_cat else k
        count, additive, start = _count_before(toks, named_from - 1)
        removed = removed_before(toks, start)

        found: list[tuple[int, int, str]] = [(named_from, k + 1, pre_cat)] if pre_cat else []
        after = k + 1
        if nxt == "for":
            read, after = _read_items(toks, k + 2, blocked)
            found += [(s, e, spec) for s, e, w in read if (spec := _item_spec(w))]
        elif nxt == "to" and nxt2 in GET_VERBS | {"drink"}:
            first = k + 3 + (1 if k + 3 < n and toks[k + 3] == "up" else 0)
            read, after = _read_items(toks, first, blocked)
            found += [(s, e, spec) for s, e, w in read if (spec := _item_spec(w))]
            if not read and nxt2 in {"eat", "drink"}:
                found.append((k, first, "food" if nxt2 == "eat" else "drinks"))
        at_a_place = after < n and toks[after] in {"at", "in", "near", "by", "over"} and any(
            s in place_spans for s in range(after + 1, min(n, after + 3)))
        if not found:
            # No "for ___": only a stop if it is clearly asked for ("add a stop", "two stops",
            # "a stop on the way") - never a bare "stop", which is the barge-in.
            asked = (count is not None and not (start == 0 and k == 0)) or additive or any(
                w in ADD_VERBS for w in toks[max(0, start - 4):start + 1]) or any(
                _on_the_way_at(toks, x) for x in range(k + 1, min(n, k + 4)))
            if at_a_place or not asked:
                covered.update(range(start, k + 1))
                continue
            found.append((k, k + 1, "rest"))  # an untyped stop is a short break
        if count and count > 1 and len(found) == 1:
            add(*found[0], count, additive, removed)
        else:
            for s, e, spec in found:
                add(s, e, spec, 1, additive, removed)
        covered.update(range(start, max(after, k + 1)))

    # 2. "grab some samosas on the way", "need water on the road"
    for x in range(n):
        if not _on_the_way_at(toks, x):
            continue
        v = next((y for y in range(x - 1, max(-1, x - 7), -1) if toks[y] in GET_VERBS | {"need", "want"}), None)
        if v is None or any(y in covered for y in range(v, x)):
            continue
        first = v + 1 + (1 if v + 1 < n and toks[v + 1] in {"up", "to"} else 0)  # "need to stretch my legs"
        count = None
        if first < n and toks[first] in NUMBERS and toks[first] not in {"a", "an"}:
            count, first = NUMBERS[toks[first]], first + 1
        read, _ = _read_items(toks[:x], first, blocked)
        for s, e, w in read:
            # "get ___ on the way" can be anything; "want ___ on the way" only a known kind of stop
            spec = _item_spec(w, allow_custom=toks[v] in GET_VERBS)
            if spec:
                add(s, e, spec, count or 1, False, removed_before(toks, v))

    # 3. "somewhere to eat", "somewhere for chai"
    for x in range(n - 2):
        if toks[x] != "somewhere" or toks[x + 1] not in {"to", "for"} or x in covered:
            continue
        if toks[x + 1] == "for":
            read, _ = _read_items(toks, x + 2, blocked)
        else:
            read = [(x + 2, x + 3, toks[x + 2:x + 3])]
        for s, e, w in read:
            spec = category_of(w, in_request=True) or (_item_spec(w) if toks[x + 1] == "for" else None)
            if spec:
                add(x if s == x + 2 else s, e, spec, 1, False, removed_before(toks, x))

    # 4. strong words anywhere ("I'm hungry", "need petrol", "no coffee")
    for k, t in enumerate(toks):
        if k in covered or k in blocked:
            continue
        pair = f"{t} {toks[k + 1]}" if k + 1 < n else ""
        cat = PHRASES.get(pair) or STRONG.get(t)
        if cat is None or (t in {"charge", "break"} and toks[k + 1:k + 2] in (["me"], ["you"], ["it"], ["the"])):
            continue
        immediate = toks[k - 1] if k else ""
        count = NUMBERS[immediate] if immediate in NUMBERS and immediate not in {"a", "an"} else 1
        add(k, k + (2 if pair in PHRASES else 1), cat, count, immediate in ADDITIVE, removed_before(toks, k))

    items.sort(key=lambda it: it.pos)
    joined = " ".join(toks)
    timed_count = len(TIMED.findall(joined))
    timed = bool(items) and bool(timed_count or TIME_WORDS.search(joined))
    live = [it for it in items if not it.removed]
    if timed_count >= 2 and len({it.spec for it in live}) == 1:
        for it in live:  # "two stops for snacks, one in the morning and one in the evening"
            it.count = max(it.count, timed_count)
    return StopRequest(items, timed)


