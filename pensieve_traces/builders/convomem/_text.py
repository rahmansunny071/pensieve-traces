"""Text helpers for the filler check, ported unchanged from convomem-arena ``convomem_arena/text.py``.

Source: https://github.com/rahmansunny071/convomem-arena (same author as this repository; the
repository has no license file, see ``NOTICE``). Only the functions the filler check uses are
ported (``key_terms`` and ``coverage`` are left out). Keep them identical to the source: the dataset
check (#9) runs convomem-arena's ``check_filler`` with these definitions.
"""

from __future__ import annotations

import re
import unicodedata

_STOP_WORDS = (
    "the a an and or of to in on for with at by from is are was were be been being it its "
    "this that these those your you my our their his her they them there what which who "
    "whom when where how why should would could can will just also than then into about "
    "over not no yes did does do has have had i me we us he she as so if but"
)
STOP = frozenset(_STOP_WORDS.split())


_LETTER = r"[^\W\d_]"
_ABBREVIATION = re.compile(rf"\b(?:{_LETTER}\.){{2,}}")
_LETTER_DIGIT = re.compile(rf"(?<={_LETTER})(?=\d)|(?<=\d)(?={_LETTER})")
_NAME = re.compile(rf"{_LETTER}+(?:['\u2019-]{_LETTER}+)*")
_CONTRACTION_ENDINGS = frozenset({"s", "t", "d", "m", "ve", "re", "ll"})


def fold(text: str) -> str:
    """Accents dropped ("Brené" -> "Brene") and dotted abbreviations closed ("a.m." -> "am").

    Applied to answers and replies alike, so the same spelling always reads the same way.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return _ABBREVIATION.sub(lambda m: m.group().replace(".", "") + " ", text)


def norm(text: str) -> str:
    """Folded, lowercased, letters split from digits ("XY123456" -> "xy 123456"),
    punctuation and underscores to spaces, whitespace collapsed."""
    text = _LETTER_DIGIT.sub(" ", fold(text).lower())
    return re.sub(r"[\W_]+", " ", text).strip()


def words(text: str) -> list[str]:
    return norm(text).split()


def content_words(text: str) -> list[str]:
    """Distinct non-stopword tokens of three or more characters (or any number), in order."""
    out = [w for w in words(text) if w not in STOP and (len(w) >= 3 or w.isdigit())]
    return list(dict.fromkeys(out))


def numbers(text: str) -> set[str]:
    """Numbers in `text`, thousands separators removed ("$1,150" -> "1150")."""
    return {n.replace(",", "") for n in re.findall(r"\d+(?:[.,]\d+)*", text)}


def name_parts(token: str) -> list[str]:
    """The checkable parts of a capitalised token, read the way `words` reads a reply.

    A hyphenated or possessive name counts by its parts of two or more letters
    ("T-Mobile" -> ["mobile"], "Maya's" -> ["maya"]); a contraction that opens with a
    stopword ("I've", "It's") is not a name at all.
    """
    pieces = re.split(r"['\u2019]", token)
    if len(pieces) > 1 and norm(pieces[0]) in STOP:
        return []
    kept = [pieces[0]] + [p for p in pieces[1:] if norm(p) not in _CONTRACTION_ENDINGS]
    parts = norm(" ".join(kept)).split()
    return [p for p in parts if len(p) >= 2 and p not in STOP]


def specifics(answer: str, question: str = "", *, short_starts: bool = False) -> list[str]:
    """The checkable details of an answer: numbers and names (capitalised words that
    don't start a sentence), minus anything the question already contains.

    Gold answers are often written-out advice ("Certainly. Based on your goals, …"),
    so most of their words never appear in the evidence; their names and numbers must.

    With `short_starts`, a capitalised word that opens a short "sentence" (under four
    words, like a list item or a line of a list) counts too: used to count the candidate
    answers a reply offers, where a list must not hide its entries behind line breaks.
    """
    # list markers (line-start "1." / "-" and inline "1)") aren't details
    answer = re.sub(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+", "", answer)
    answer = re.sub(r"(?<![\w$.,])\d+\)\s", " ", answer)
    answer = fold(answer)
    asked_words, asked_numbers = set(words(question)), numbers(question)
    found = [n for n in numbers(answer) if n not in asked_numbers]
    for m in _NAME.finditer(answer):
        token = m.group()
        if not token[0].isupper():
            continue
        before = answer[: m.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:\n*-•\"'(" or before.endswith("**")
        if sentence_start and before and before[-1] in "\"'":
            # a short quoted span is a title or a password ('Pixel&Tomatoes22!'), not a sentence
            quoted = re.split(r"[\"'.!?]", answer[m.start() :], maxsplit=1)[0]
            sentence_start = len(quoted.split()) >= 4
        if sentence_start and short_starts:
            sentence = re.split(r"[.!?:;\n]", answer[m.start() :], maxsplit=1)[0]
            sentence_start = len(sentence.split()) >= 4
        if sentence_start:
            continue
        found += [p for p in name_parts(token) if p not in asked_words]
    return list(dict.fromkeys(found))


def is_number(term: str) -> bool:
    return term[:1].isdigit()


def present(terms: list[str], text: str) -> list[str]:
    """The `terms` (words or numbers from `specifics`) that occur in `text`.

    Numbers are looked up only among the numbers of `text`, so "$2,100" holds 2100 and
    not 2 or 100; names only among its words.
    """
    ws, ns = set(words(text)), numbers(text)
    return [t for t in terms if (t in ns if is_number(t) else t in ws)]
