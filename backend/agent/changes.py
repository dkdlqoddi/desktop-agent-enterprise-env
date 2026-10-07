"""What changed in a page or ticket between two imports of it: the lines the newer text added and removed, for the
automatic routines that watch Confluence pages and JIRA tickets (routines.py). Only an excerpt of the changed lines is
kept, so a job's results stay small however long the documents are."""
import bisect
import difflib
from collections import Counter

MAX_LINES = 60  # Changed lines kept for the screens and the LLM,
MAX_LINE = 400  # each cut to this many characters.
# A stretch of lines with no line that occurs once on each side is compared with difflib, which takes time growing with
# the square of its size: a longer one in blocks of about this many lines each side, where they are as far in.
BLOCK = 400


def increasing(pairs):
    """The longest run of `pairs` (i, j), in order of i, whose j also increase (patience sorting)."""
    tails, links, ends = [], [], []
    for index, (_, j) in enumerate(pairs):
        place = bisect.bisect_left(tails, j)
        if place == len(tails):
            tails.append(j)
            ends.append(index)
        else:
            tails[place] = j
            ends[place] = index
        links.append(ends[place - 1] if place else -1)
    run, index = [], ends[-1] if ends else -1
    while index >= 0:
        run.append(pairs[index])
        index = links[index]
    return run[::-1]


def stay(old, new, a0, a1, b0, b1, kept):
    """Add the pairs of lines of old[a0:a1] that stay in new[b0:b1] to `kept`, for a stretch where no line occurs once
    on each side: difflib's, block by block when the stretch is long."""
    count = max(1, round(max(a1 - a0, b1 - b0) / BLOCK))
    for block in range(count):
        start, end = a0 + (a1 - a0) * block // count, a0 + (a1 - a0) * (block + 1) // count
        first, last = b0 + (b1 - b0) * block // count, b0 + (b1 - b0) * (block + 1) // count
        matcher = difflib.SequenceMatcher(None, old[start:end], new[first:last], autojunk=False)
        for i, j, size in matcher.get_matching_blocks():
            kept += [(start + i + offset, first + j + offset) for offset in range(size)]


def matches(old, new):
    """The pairs (i, j) of lines old[i] == new[j] that stay, in order: lines found once on each side anchor the rest
    (patience diff), so long texts are compared in about the time it takes to read them."""
    kept, stack = [], [(0, len(old), 0, len(new))]
    while stack:
        a0, a1, b0, b1 = stack.pop()
        while a0 < a1 and b0 < b1 and old[a0] == new[b0]:
            kept.append((a0, b0))
            a0, b0 = a0 + 1, b0 + 1
        while a0 < a1 and b0 < b1 and old[a1 - 1] == new[b1 - 1]:
            a1, b1 = a1 - 1, b1 - 1
            kept.append((a1, b1))
        if a0 == a1 or b0 == b1:
            continue
        before, after = Counter(old[a0:a1]), Counter(new[b0:b1])
        places = {new[j]: j for j in range(b0, b1) if after[new[j]] == 1}
        anchors = increasing([(i, places[old[i]]) for i in range(a0, a1)
                              if before[old[i]] == 1 and old[i] in places])
        if not anchors:
            stay(old, new, a0, a1, b0, b1, kept)
            continue
        kept += anchors
        last = (a0, b0)
        for i, j in anchors:
            stack.append((last[0], i, last[1], j))
            last = (i + 1, j + 1)
        stack.append((last[0], a1, last[1], b1))
    return sorted(kept)


def compare(before, after):
    """{"added", "removed", "lines": [{"op": "+" or "-", "text"}], "more"}: the lines `after` added to and removed from
    `before`, blank lines left out, in the order they occur; None when the texts hold the same lines."""
    old = [line.rstrip() for line in before.splitlines()]
    new = [line.rstrip() for line in after.splitlines()]
    added = removed = 0
    lines = []
    i = j = 0
    # Between two lines that stay, the old lines were removed and the new ones added.
    for kept_i, kept_j in [*matches(old, new), (len(old), len(new))]:
        went = [line for line in old[i:kept_i] if line.strip()]
        came = [line for line in new[j:kept_j] if line.strip()]
        removed += len(went)
        added += len(came)
        lines += [("-", line) for line in went] + [("+", line) for line in came]
        i, j = kept_i + 1, kept_j + 1
    if not lines:
        return None
    return {"added": added, "removed": removed,
            "lines": [{"op": op, "text": text.strip()[:MAX_LINE]} for op, text in lines[:MAX_LINES]],
            "more": max(0, len(lines) - MAX_LINES)}


def excerpt(change, limit=6000):
    """A change as diff lines ("+ …", "- …") for the LLM, within `limit` characters."""
    text = "\n".join(f"{line['op']} {line['text']}" for line in change["lines"])
    if change["more"]:
        text += f"\n(바뀐 줄 {change['more']}개 더 있음)"
    return text[:limit]
