"""A small, dependency-free BM25 index over the provided corpus.

Kept deliberately simple: the interesting engineering in this project is the
interruption and speculation machinery, and retrieval only has to be fast,
deterministic, and honest about what it matched.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent.parent / "corpus"

_WORD = re.compile(r"[a-z0-9]+")

_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "for",
    "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
    "with", "as", "at", "by", "from", "i", "me", "my", "we", "you", "your",
    "can", "do", "does", "did", "what", "how", "when", "where", "which",
    # Discourse markers: they steer the conversation but carry no topic, so
    # counting them as content makes an early prefix look far more informative
    # than it is.
    "actually", "wait", "okay", "ok", "well", "hmm", "um", "uh", "just",
    "please", "sorry", "anyway", "also", "now", "then", "so", "like",
    "happen", "happens", "tell", "know", "think", "want", "need", "get",
}


_SUFFIXES = ("ations", "ation", "ingly", "edly", "ments", "ment", "ings", "ing", "ies", "ed", "es", "s")


def _stem(word: str) -> str:
    """Crude suffix stripper.

    Good enough to make "cancelled"/"cancellation" and "refund"/"refunds" meet
    in the middle, which is all BM25 needs over a corpus this size.
    """
    if len(word) <= 4:
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            stem = word[: -len(suffix)]
            # collapse doubled consonants left behind ("cancell" -> "cancel")
            if len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
                stem = stem[:-1]
            return stem
    return word


def tokenize(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 1]


@dataclass(frozen=True)
class Doc:
    doc_id: str
    source: str
    title: str
    text: str

    @property
    def snippet(self) -> str:
        flat = " ".join(self.text.split())
        return flat[:260] + ("..." if len(flat) > 260 else "")


@dataclass(frozen=True)
class Hit:
    doc: Doc
    score: float


class Corpus:
    """BM25 over markdown sections (one document per ``##`` heading)."""

    K1 = 1.4
    B = 0.75

    def __init__(self, docs: list[Doc]) -> None:
        self.docs = docs
        # Titles are weighted: a section called "Properties" should win a hotel
        # question against a body paragraph that merely mentions an airport.
        self._tokens: list[list[str]] = [
            tokenize(d.text) + tokenize(d.title) * 3 + tokenize(d.source) * 2 for d in docs
        ]
        self._tf: list[Counter[str]] = [Counter(t) for t in self._tokens]
        self._len = [len(t) or 1 for t in self._tokens]
        self._avg_len = sum(self._len) / max(len(self._len), 1)
        df: Counter[str] = Counter()
        for toks in self._tokens:
            df.update(set(toks))
        n = max(len(docs), 1)
        self._idf = {
            term: math.log(1 + (n - count + 0.5) / (count + 0.5)) for term, count in df.items()
        }

    @classmethod
    def load(cls, directory: Path = CORPUS_DIR) -> "Corpus":
        docs: list[Doc] = []
        for path in sorted(directory.glob("*.md")):
            source = path.stem
            current_title = source.title()
            buffer: list[str] = []

            def flush() -> None:
                body = "\n".join(buffer).strip()
                if body:
                    docs.append(
                        Doc(
                            doc_id=f"{source}#{len(docs)}",
                            source=source,
                            title=current_title,
                            text=body,
                        )
                    )

            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## "):
                    flush()
                    buffer = []
                    current_title = line[3:].strip()
                elif line.startswith("# "):
                    continue
                else:
                    buffer.append(line)
            flush()
        return cls(docs)

    def search(self, query: str, k: int = 3) -> list[Hit]:
        q = tokenize(query)
        if not q:
            return []
        scored: list[Hit] = []
        for i, doc in enumerate(self.docs):
            tf = self._tf[i]
            length = self._len[i]
            score = 0.0
            for term in q:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                idf = self._idf.get(term, 0.0)
                denom = freq + self.K1 * (1 - self.B + self.B * length / self._avg_len)
                score += idf * (freq * (self.K1 + 1)) / denom
            if score > 0:
                scored.append(Hit(doc=doc, score=round(score, 4)))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:k]

    async def search_async(self, query: str, latency_ms: int, k: int = 3) -> list[Hit]:
        """Search behind a simulated I/O delay.

        The sleep is a real cancellation point, which is what lets an in-flight
        speculative retrieval be abandoned the instant the user changes course.
        """
        if latency_ms > 0:
            await asyncio.sleep(latency_ms / 1000.0)
        return self.search(query, k=k)


corpus = Corpus.load()
