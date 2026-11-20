#!/usr/bin/env python3
"""Count the `gh` (or `tea`) calls that a timeline.py run makes, through IFW_GH or IFW_TEA.

    python3 scripts/count-gh.py wrap <dir> [--target gh]     # prints the wrapper path
    IFW_GH=<wrapper> python3 issue-flow/scripts/timeline.py collect ...
    python3 scripts/count-gh.py count <dir> [--match REGEX]  # prints the number of calls

`wrap` writes `<dir>/gh` and an empty `<dir>/calls`. The wrapper appends one line per call to
`calls`, with every argument, and then runs the target with the same arguments. The target is
a command name on PATH or a path, so a live run counts the real `gh` and a test counts its fake.
`count` prints how many lines of `calls` match REGEX (a `re.search`; default: every line).

Tests import this file and call `wrap(dir, target)` and `calls(dir, match)` directly.
"""

import argparse
import os
import re
import shlex
import shutil
import sys


def wrap(directory, target="gh"):
    """Return the path of a wrapper that logs each call to `<directory>/calls` and runs `target`.

    `target` is a path, or a command name that is looked up on PATH now, so the wrapper never
    finds itself. A missing target raises FileNotFoundError.
    """
    found = target if os.path.sep in target else shutil.which(target)
    if not found or not os.path.exists(found):
        raise FileNotFoundError(f"cannot find {target}")
    os.makedirs(directory, exist_ok=True)
    log = os.path.join(directory, "calls")
    open(log, "w").close()
    path = os.path.join(directory, "gh")
    with open(path, "w") as handle:
        # One short line per call goes through O_APPEND, so parallel calls do not mix their lines.
        handle.write(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {shlex.quote(log)}\nexec {shlex.quote(os.path.abspath(found))} "$@"\n')
    os.chmod(path, 0o755)
    return path


def calls(directory, match=None):
    """Return the logged calls in `directory`, as one string of arguments each, in call order."""
    try:
        with open(os.path.join(directory, "calls")) as handle:
            rows = handle.read().splitlines()
    except OSError:
        return []
    return [row for row in rows if match is None or re.search(match, row)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("wrap", help="write the counting wrapper and print its path")
    w.add_argument("dir")
    w.add_argument("--target", default="gh", help="the command the wrapper runs (default: gh on PATH)")
    c = sub.add_parser("count", help="print the number of logged calls")
    c.add_argument("dir")
    c.add_argument("--match", help="count only the calls that match this regular expression")
    args = parser.parse_args(argv)
    if args.command == "wrap":
        try:
            print(wrap(args.dir, args.target))
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0
    print(len(calls(args.dir, args.match)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
