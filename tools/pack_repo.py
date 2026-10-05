#!/usr/bin/env python3
"""Pack the repository into the single text document (used to make EditForge_v1_0_COMPLETE_REPO_SOURCE.txt)."""

import os
import sys

BAR = "=" * 60
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "samples", "build", "dist", "editforge.egg-info", ".mypy_cache"}
SKIP_SUFFIX = (".pyc", ".pyo", ".part", ".sqlite3", ".mp4", ".wav", ".png", ".ts")


def collect(root):
    files = []
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith(".egg-info"))
        for n in sorted(names):
            if n.endswith(SKIP_SUFFIX):
                continue
            files.append(os.path.relpath(os.path.join(dirpath, n), root).replace(os.sep, "/"))
    return files


def tree(name, files):
    root = {}
    for f in files:
        node = root
        for part in f.split("/"):
            node = node.setdefault(part, {})
    out = [name + "/"]

    def walk(node, prefix):
        for key in sorted(node, key=lambda k: (not node[k], k)):
            out.append(f"{prefix}+-- {key}")
            if node[key]:
                walk(node[key], prefix + "|   ")

    walk(root, "")
    return "\n".join(out)


def main(root, dest):
    name = os.path.basename(os.path.abspath(root))
    files = collect(root)
    parts = ["# EditForge v1.0.0 -- Complete Repository Source\n",
             "This document contains every non-generated repository/source file in EditForge. Generated runtime artifacts are excluded.\n",
             "## REPOSITORY TREE\n", "```text\n" + tree(name, files) + "\n```\n", "## REPOSITORY FILES\n"]
    for rel in files:
        with open(os.path.join(root, rel), "r", encoding="utf-8", newline="") as fh:
            content = fh.read().replace("\r\n", "\n")
        parts.append(f"{BAR}\nNAME: {os.path.basename(rel)}\nPATH: {name}/{rel}\n{BAR}\n\n{content.rstrip(chr(10))}\n")
    with open(dest, "w", encoding="utf-8", newline="\n") as out:
        out.write("\n".join(parts))
    print(f"packed {len(files)} files -> {dest}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".", sys.argv[2] if len(sys.argv) > 2 else "EditForge_v1_0_COMPLETE_REPO_SOURCE.txt")
