"""What changed in a page or ticket between two imports of it: the lines the newer text added and removed, for the
automatic routines that watch Confluence pages and JIRA tickets (routines.py). Only an excerpt of the changed lines is
kept, so a job's results stay small however long the documents are. The document window's edit conflict compares
whole texts with the same line matching (diff), and merges two edits of a text whose changes do not meet (merge)."""

import bisect
import difflib
import re
from collections import Counter

MAX_LINES = 60  # Changed lines kept for the screens and the LLM,
MAX_LINE = 400  # each cut to this many characters.
# A stretch of lines with no line that occurs once on each side is compared with difflib, which takes time growing with
# the square of its size: a longer one in blocks of about this many lines each side, where they are as far in.
BLOCK = 400
CONTEXT = 3  # Unchanged lines shown around each change of a document comparison (diff),
MAX_DIFF_LINES = 3000  # and the most lines one comparison carries to the screen.
# A line with the break that ends it, whichever a text uses (\r\n, \n, \r); the last line of a text may have none.
LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


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
        anchors = increasing([(i, places[old[i]]) for i in range(a0, a1) if before[old[i]] == 1 and old[i] in places])
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
    return {
        "added": added,
        "removed": removed,
        "lines": [{"op": op, "text": text.strip()[:MAX_LINE]} for op, text in lines[:MAX_LINES]],
        "more": max(0, len(lines) - MAX_LINES),
    }


def excerpt(change, limit=6000):
    """A change as diff lines ("+ …", "- …") for the LLM, within `limit` characters."""
    text = "\n".join(f"{line['op']} {line['text']}" for line in change["lines"])
    if change["more"]:
        text += f"\n(바뀐 줄 {change['more']}개 더 있음)"
    return text[:limit]


def lines_of(text):
    """A text's lines as an editor numbers them, whatever its line breaks; a final line break starts no line of its
    own."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


def diff(before, after, context=CONTEXT, limit=MAX_DIFF_LINES):
    """Two texts compared line by line as a unified diff, for the document window's edit conflict: {"added",
    "removed", "hunks": [{"old_start", "new_start", "lines": [{"op": " ", "+" or "-", "text"}]}], "more"}. A hunk holds
    changes with up to `context` unchanged lines around them and starts at those line numbers of each text (from 1);
    `more` counts the changed lines left out past `limit` lines. Unlike compare(), blank lines count and every line
    keeps its whole text, as the user chooses between the texts by them; lines that differ only in trailing spaces are
    the same line."""
    old, new = lines_of(before), lines_of(after)
    # Each line once, in the order a reader goes through both texts: (op, text, line of the old text, line of the new
    # one). A line one text lacks carries the number of that text's next line, where a hunk starting with it starts.
    rows, i, j = [], 0, 0
    kept = matches([line.rstrip() for line in old], [line.rstrip() for line in new])
    for kept_i, kept_j in [*kept, (len(old), len(new))]:
        rows += [("-", old[n], n + 1, j + 1) for n in range(i, kept_i)]
        rows += [("+", new[n], kept_i + 1, n + 1) for n in range(j, kept_j)]
        if kept_i < len(old):
            rows.append((" ", new[kept_j], kept_i + 1, kept_j + 1))
        i, j = kept_i + 1, kept_j + 1
    changed = [index for index, row in enumerate(rows) if row[0] != " "]
    hunks, shown, told = [], 0, 0
    start = 0
    while start < len(changed) and shown < limit:
        # Changes no more than twice the context apart share one hunk, so no unchanged line shows twice.
        end = start
        while end + 1 < len(changed) and changed[end + 1] - changed[end] <= 2 * context + 1:
            end += 1
        part = rows[max(0, changed[start] - context) : changed[end] + context + 1][: limit - shown]
        if all(op == " " for op, _, _, _ in part):
            break  # Cut short before its first change: the changes go to `more`.
        hunks.append(
            {
                "old_start": part[0][2],
                "new_start": part[0][3],
                "lines": [{"op": op, "text": text} for op, text, _, _ in part],
            }
        )
        shown += len(part)
        told += sum(op != " " for op, _, _, _ in part)
        start = end + 1
    return {
        "added": sum(row[0] == "+" for row in rows),
        "removed": sum(row[0] == "-" for row in rows),
        "hunks": hunks,
        "more": len(changed) - told,
    }


def merge(base, ours, theirs):
    """Two edits of the text `base` merged line by line (three-way, as diff3 does), for an edit that meets a change
    made elsewhere: `ours` the editor's text, `theirs` the document's. Where one side changed lines the other left as
    they were, the changed side's lines go in, so neither side's change is lost; where both made the same change, it
    goes in once. None when they changed the same lines, lines next to each other, or put different lines at the same
    place: only the user can settle those.

    Lines compare without their line breaks, so one side's \\r\\n against the other's \\n is no change; each line keeps
    its own, and a last line without one gets a \\n when lines come after it in the merge. Whether the text ends with a
    line break is merged as a line is: as the side that changed it has it. Lines that differ in anything else,
    trailing spaces included (a Markdown line break), differ."""
    old, mine, other = LINE.findall(base), LINE.findall(ours), LINE.findall(theirs)
    keys = [[line.rstrip("\r\n") for line in lines] for lines in (old, mine, other)]
    # Where each line of base stays on each side (matches: in order on both).
    kept_mine, kept_other = (dict(matches(keys[0], side)) for side in keys[1:])
    merged, i, j, k = [], 0, 0, 0  # The next line of base, ours and theirs.
    while i < len(old) or j < len(mine) or k < len(other):
        if i < len(old) and kept_mine.get(i) == j and kept_other.get(i) == k:
            merged.append(mine[j])  # A line neither side changed.
            i, j, k = i + 1, j + 1, k + 1
            continue
        # What each side made of base up to its next line that both kept (or to the ends).
        end = i
        while end < len(old) and not (end in kept_mine and end in kept_other):
            end += 1
        to_mine, to_other = (kept_mine[end], kept_other[end]) if end < len(old) else (len(mine), len(other))
        was, made, met = keys[0][i:end], keys[1][j:to_mine], keys[2][k:to_other]
        if made == was:
            merged += other[k:to_other]
        elif met == was or made == met:
            merged += mine[j:to_mine]
        else:
            return None
        i, j, k = end, to_mine, to_other
    lines = [
        line if place == len(merged) - 1 or line.endswith(("\n", "\r")) else line + "\n"
        for place, line in enumerate(merged)
    ]
    ending = final_break(mine) if final_break(mine) != final_break(old) else final_break(other)
    if lines and lines[-1].rstrip("\r\n"):
        lines[-1] = lines[-1].rstrip("\r\n") + ending
    return "".join(lines)


def final_break(lines):
    """The line break the last of `lines` ends with: '' for none."""
    last = lines[-1] if lines else ""
    return last[len(last.rstrip("\r\n")) :]
