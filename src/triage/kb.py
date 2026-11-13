"""Help-centre retrieval: the markdown files in the package's data/kb, split by section and ranked with BM25.

BM25 needs no embedding key and returns the same ranking every run, which keeps the retrieval
evaluation deterministic. Its weakness is vocabulary: a question that shares no words with the
right section will miss it.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi

from triage.config import KB_DIR

_STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from has have how i if in is it my of on or "
    "our so that the their this to was we what when where which who why will with you your".split()
)


@dataclass(frozen=True)
class Chunk:
    """One section of a help-centre article. `doc_id` is the file name and is what replies cite."""

    doc_id: str
    title: str
    section: str
    text: str


def _root(word: str) -> str:
    """A crude word root, so that payout/payouts and arrive/arrives/arrived/arriving meet. Not a real stemmer."""
    if len(word) > 5 and word.endswith("ing"):
        word = word[:-3]
    elif len(word) > 4 and word.endswith("ed"):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word[:-1] if len(word) > 4 and word.endswith("e") else word


def _tokens(text: str, roots: bool = False) -> list[str]:
    words = [word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _STOPWORDS]
    return [_root(word) for word in words] if roots else words


@lru_cache(maxsize=2)
def _index(roots: bool = False) -> tuple[list[Chunk], BM25Okapi]:
    chunks = []
    for path in sorted(KB_DIR.glob("*.md")):
        title_block, *sections = re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.MULTILINE)
        title = title_block.strip().removeprefix("# ")
        for section in sections:
            heading, _, body = section.partition("\n")
            chunks.append(Chunk(path.stem, title, heading.strip(), body.strip()))
    # The article title and section heading are indexed with the body: they carry the best keywords.
    corpus = [_tokens(f"{chunk.title} {chunk.section} {chunk.text}", roots) for chunk in chunks]
    return chunks, BM25Okapi(corpus)


def doc_ids() -> set[str]:
    """Every article id in the help centre."""
    return {chunk.doc_id for chunk in _index()[0]}


def section_ids() -> set[str]:
    """Every section in the help centre, as `doc_id#Section heading`."""
    return {f"{chunk.doc_id}#{chunk.section}" for chunk in _index()[0]}


def section_text(section_id: str) -> str | None:
    """The text of one section, given as `doc_id#Section heading`."""
    for chunk in _index()[0]:
        if f"{chunk.doc_id}#{chunk.section}" == section_id:
            return f"{chunk.title} › {chunk.section}\n{chunk.text}"
    return None


def search(query: str, k: int = 4, roots: bool = False) -> list[Chunk]:
    """Return the k sections that best match the query, best first. Sections with no shared words are left out.

    With `roots`, words are matched by their root, so "payout" finds "payouts" (agent v3).
    """
    chunks, bm25 = _index(roots)
    scores = bm25.get_scores(_tokens(query, roots))
    ranked = sorted(zip(scores, range(len(chunks)), strict=True), reverse=True)[:k]
    return [chunks[position] for score, position in ranked if score > 0]


def format_chunks(chunks: list[Chunk]) -> str:
    """Render search results the way the model sees them."""
    if not chunks:
        return "No help-centre article matches that search."
    return "\n\n".join(f"[doc_id: {c.doc_id}] {c.title} › {c.section}\n{c.text}" for c in chunks)
