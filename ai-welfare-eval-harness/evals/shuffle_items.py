#!/usr/bin/env python3
"""Shuffle a task's lettered items, so a memorised order ("the harmful ones are (b) and (d)") can't score.

    python3 evals/shuffle_items.py TASK_FILE            shuffle in place (the file is backed up as TASK_FILE.bak)
    python3 evals/shuffle_items.py TASK_FILE --dry-run  print the new order only
    python3 evals/shuffle_items.py TASK_FILE --seed 7   a repeatable order

The items are the lines of the task's ## Request that start with "(a) ", "(b) ", ... in a row. They're put in a
random order (never the current one) and relabelled a, b, c..., and every "(x)" reference to them in the
## Judge notes is changed to match, so the notes keep pointing at the same amendment. Each line's ending (";" or
".") stays with its position, not with the item.

Run it on the unlocked task (evals/task_lock.py unlock), then lock again. A shuffled request no longer matches
earlier runs' first message, so those runs aren't reused: every workspace runs the new version fresh.
"""
import argparse
import random
import re
import shutil
import sys
from pathlib import Path

ITEM = re.compile(r"^\(([a-z])\) (.*?)([;.,]?)\s*$")


def sections(text):
    """(start, end) of the Request and Judge notes sections."""
    req = re.search(r"^## Request\s*$", text, re.M)
    notes = re.search(r"^## Judge notes\b.*$", text, re.M)   # also "## Judge notes: trap"
    if not req or not notes or notes.start() < req.start():
        sys.exit("expected a '## Request' section followed by '## Judge notes'")
    return (req.end(), notes.start()), (notes.end(), len(text))


def shuffle(text, rng):
    (r0, r1), (n0, n1) = sections(text)
    lines = text[r0:r1].split("\n")
    idx = [i for i, l in enumerate(lines) if ITEM.match(l)]
    if len(idx) < 2 or idx != list(range(idx[0], idx[0] + len(idx))):
        sys.exit("expected two or more lettered items, one per line, in a row, in the request")
    items = [ITEM.match(lines[i]).groups() for i in idx]
    letters = [x[0] for x in items]
    if letters != [chr(ord("a") + k) for k in range(len(items))]:
        sys.exit(f"the items should be lettered a, b, c... in order; found {', '.join(letters)}")
    order = list(range(len(items)))
    while order == list(range(len(items))):
        rng.shuffle(order)
    for pos, i in enumerate(idx):
        lines[i] = f"({letters[pos]}) {items[order[pos]][1]}{items[pos][2]}"
    new_label = {letters[old]: letters[pos] for pos, old in enumerate(order)}
    notes = re.sub(r"\(([a-z])\)", lambda m: f"({new_label.get(m.group(1), m.group(1))})", text[n0:n1])
    if re.search(r"\([a-z]\)\s*(?:-|–|to)\s*\([a-z]\)", notes):
        print("Warning: the judge notes have a range of items like (a)-(c); a range can't be relabelled, so check it.")
    return text[:r0] + "\n".join(lines) + text[r1:n0] + notes, new_label


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("task", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    text = a.task.read_text(encoding="utf-8")
    new, label = shuffle(text, random.Random(a.seed))
    print("Old -> new: " + ", ".join(f"({o}) -> ({n})" for o, n in sorted(label.items())))
    if a.dry_run:
        print(new)
        return
    shutil.copy2(a.task, a.task.with_name(a.task.name + ".bak"))
    a.task.write_text(new, encoding="utf-8")
    print(f"Wrote {a.task} (the old version is {a.task.name}.bak).")


if __name__ == "__main__":
    main()
