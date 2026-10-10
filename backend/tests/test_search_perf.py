"""How long a search of a large Wiki takes: 10,000 documents, an eight-word query, at most 0.3 seconds.

It builds the Wiki first (half a minute), so it runs only when asked: PRIVATE_AGENT_PERF=1. The documents are made of
Korean and English words drawn as often as words of a language are (Zipf), so some words of the query are in most of
them and others in a few. Run with -s to see the times."""
import os
import random
import sqlite3
import time
import uuid

import pytest

from agent.db import Database
from agent.service import Service

pytestmark = pytest.mark.skipif(os.environ.get("PRIVATE_AGENT_PERF") != "1",
                                reason="a performance test: set PRIVATE_AGENT_PERF=1 to run it")

DOCUMENTS = 10_000
LIMIT = 0.3  # Seconds the search may take.
SYLLABLES = ("가나다라마바사아자차카타파하고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후기니디리미비시이지치키티피히"
             "개내대래매배새애재채캐태패해데레메베세에제체케테페헤")


def vocabulary(rng):
    """12,000 words, the most frequent first: Korean of two to four syllables and English of three to nine letters."""
    found = set()
    while len(found) < 12_000:
        if rng.random() < 0.6:
            found.add("".join(rng.choice(SYLLABLES) for _ in range(rng.choice((2, 2, 3, 3, 3, 4)))))
        else:
            found.add("".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 9))))
    words = sorted(found)
    rng.shuffle(words)
    return words


@pytest.fixture(scope="module")
def wiki(tmp_path_factory):
    return build(tmp_path_factory.mktemp("perf") / "wiki.db")


def build(path):
    """The Wiki of DOCUMENTS documents at `path`, with its search text and trigram index made: (service, the words of
    three or more characters by frequency, the queries timed)."""
    rng = random.Random(7)
    words = vocabulary(rng)
    weights = [1 / (rank + 1) ** 1.05 for rank in range(len(words))]
    Database(path)
    rows, keywords = [], []
    for i in range(DOCUMENTS):
        # Bodies of 150-600 words in paragraphs and sections, a few of 2,500 or more; three in ten analyzed.
        size = rng.randint(150, 600) if rng.random() > 0.03 else rng.randint(2500, 3500)
        blocks = []
        for start in range(0, size, 60):
            if rng.random() < 0.3:
                blocks.append("## " + " ".join(rng.choices(words, weights, k=3)))
            blocks.append(" ".join(rng.choices(words, weights, k=min(60, size - start))) + ".")
        analyzed = rng.random() < 0.3
        ident = str(uuid.UUID(int=rng.getrandbits(128)))
        stamp = f"2026-{rng.randint(1, 9):02d}-{rng.randint(1, 28):02d}T{rng.randint(0, 23):02d}:00:00.{i:06d}+00:00"
        rows.append((ident, " ".join(rng.choices(words, weights, k=rng.randint(3, 7))), "\n\n".join(blocks),
                     " ".join(rng.choices(words, weights, k=40)) if analyzed else "", "ready" if analyzed else "pending",
                     "wiki" if analyzed else "", stamp, stamp))
        if analyzed:
            keywords += [(ident, word) for word in set(rng.choices(words[:2000], k=6))]
    with sqlite3.connect(path) as db:
        db.executemany("""INSERT INTO documents(id,title,content,summary,status,index_mode,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""", rows)
        db.executemany("INSERT INTO keywords VALUES (?,?)", keywords)
    database = Database(path)
    started = time.perf_counter()
    assert database._index()
    print(f"\nsearch text and trigram index of {DOCUMENTS:,} documents: {time.perf_counter() - started:.1f}s "
          f"(trigram index: {database.fts})")
    long = [word for word in words if len(word) >= 3]
    rank = {word: index for index, word in enumerate(words)}
    # Eight words of one document, from its most frequent to its rarest, which a word search finds it by. Neither query
    # has a word more frequent than the 40th, which is in two of three documents: the first words are in every one,
    # as words a search drops would be (FILLER), and are timed on their own.
    own = sorted({word.strip(".") for word in rows[123][2].split()} & set(long[40:]), key=rank.get)
    queries = {"frequent to rare": " ".join(long[r] for r in (40, 150, 400, 900, 1800, 3500, 6000, 9000)),
               "one document's words": " ".join(own[round(i * (len(own) - 1) / 7)] for i in range(8)),
               "a word in every document": " ".join(long[r] for r in (0, 150, 400, 900, 1800, 3500, 6000, 9000))}
    print("\n".join(f"{kind}: {query}" for kind, query in queries.items()))
    return Service(database, None), long, queries


def timed(service, query, mode):
    """The best of five runs after a first that warms the cache, in seconds, and the documents found: the other
    programs of a busy machine slow some runs, never the search itself."""
    found = service.wiki_search({"query": query, "mode": mode})["total"]
    best = min(timer(service, query, mode) for _ in range(5))
    return best, found


def timer(service, query, mode):
    started = time.perf_counter()
    service.wiki_search({"query": query, "mode": mode})
    return time.perf_counter() - started


@pytest.mark.parametrize("mode", ["auto", "word"])
@pytest.mark.parametrize("kind", ["frequent to rare", "one document's words"])
def test_an_eight_word_search_of_ten_thousand_documents_takes_at_most_300_ms(wiki, kind, mode):
    """Words from the frequent to the rare (the first is in about two of three documents), and the words of one
    document, which a word search finds."""
    service, _, queries = wiki
    seconds, found = timed(service, queries[kind], mode)
    print(f"\n{mode}, {kind}: {found:,} of {DOCUMENTS:,} documents found, {seconds * 1000:.0f} ms")
    assert found or (mode, kind) == ("word", "frequent to rare")
    assert seconds <= LIMIT, f"{mode} search took {seconds * 1000:.0f} ms"


def test_a_word_in_every_document_has_every_document_counted(wiki):
    """A word in every document makes a search rank all of them, counting the word in each. Reported, not limited."""
    service, _, queries = wiki
    for mode in ("auto", "word"):
        seconds, found = timed(service, queries["a word in every document"], mode)
        print(f"\n{mode}, a word in every document: {found:,} documents found, {seconds * 1000:.0f} ms")


def test_words_shorter_than_a_trigram_are_compared_over_the_search_text(wiki):
    """Two-letter words have no trigram to look up: they are read from the search text, which costs more. Reported,
    not limited."""
    service, long, _ = wiki
    with service.db.connect() as db:
        short = [row[0] for row in db.execute("SELECT keyword FROM keywords WHERE length(keyword)=2 GROUP BY keyword "
                                              "ORDER BY count(*) DESC LIMIT 2")]
    query = " ".join(short + [long[rank] for rank in (400, 900, 1800, 3500, 6000, 9000)])
    for mode in ("auto", "word"):
        seconds, found = timed(service, query, mode)
        print(f"\n{mode}: 2 short and 6 long words, {found:,} documents found, {seconds * 1000:.0f} ms")
