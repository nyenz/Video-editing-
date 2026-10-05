#!/usr/bin/env python3
"""Rebuild the whole EditForge folder from the single text file.

    python tools/extract_repo.py EditForge_v1_0_COMPLETE_REPO_SOURCE.txt [where-to-put-it]

Needs only Python 3.8 or newer. It refuses to write outside the target folder.
"""

import os
import sys

BAR = "=" * 60


def parse(text):
    """Yield (path, content) for every file block in the document."""
    lines = text.replace("\r\n", "\n").split("\n")
    heads = []
    for i in range(len(lines) - 3):
        if lines[i] == BAR and lines[i + 1].startswith("NAME: ") and lines[i + 2].startswith("PATH: ") and lines[i + 3] == BAR:
            heads.append(i)
    for n, h in enumerate(heads):
        path = lines[h + 2][len("PATH: "):]
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        body = lines[h + 4:end]
        if body and body[0] == "":
            body = body[1:]
        while body and body[-1] == "":
            body.pop()
        yield path, "\n".join(body) + "\n"


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        text = fh.read()
    target = os.path.abspath(argv[2] if len(argv) > 2 else ".")
    count = 0
    for path, content in parse(text):
        dest = os.path.abspath(os.path.join(target, path))
        if not dest.startswith(target + os.sep):
            print("Skipping an unsafe path:", path)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8", newline="\n") as out:
            out.write(content)
        count += 1
    if count == 0:
        print("I found no files in that document. Is it the right file?")
        return 1
    print(f"Wrote {count} files into {target}")
    print(f"Next: open the '{path.split('/')[0]}' folder and double-click start.bat (Windows) or start.command (Mac), or run: bash start.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
