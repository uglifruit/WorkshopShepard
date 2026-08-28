#!/usr/bin/env python3
"""
extract_transcript.py - pull a readable transcript out of a Claude Code
session log.

Claude Code writes every session to ~/.claude/projects/<slug>/<uuid>.jsonl,
one JSON record per line. Most records are machinery - tool calls, tool
results, file snapshots, UI state - so a raw read is unusable. This keeps
only what a person actually said and what Claude said back, in order.

Tool *calls* are summarised as one line each rather than dumped, because the
arguments are frequently whole files. The point is to show the shape of the
work, not to reproduce it.

Usage:
    python tools/extract_transcript.py <session.jsonl> [-o out.md]
    python tools/extract_transcript.py --find        # list sessions

Written for this card's own session, but nothing here is card-specific.
"""

import argparse
import json
import os
import sys
from datetime import datetime

PROJECTS = os.path.expanduser("~/.claude/projects")

# Records that are Claude Code's own bookkeeping rather than conversation.
SKIP_TYPES = {
    "attachment", "file-history-snapshot", "file-history-delta",
    "ai-title", "atis-latch", "last-prompt", "queue-operation",
    "pr-link", "system",
}

# User text that is a slash command or an interruption notice, not a prompt.
META_PREFIXES = (
    "<command-", "<local-command", "[Request interrupted",
    "Caveat:", "<system-reminder>",
)


def find_sessions():
    if not os.path.isdir(PROJECTS):
        print(f"no projects directory at {PROJECTS}")
        return
    rows = []
    for proj in sorted(os.listdir(PROJECTS)):
        d = os.path.join(PROJECTS, proj)
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if fn.endswith(".jsonl"):
                full = os.path.join(d, fn)
                rows.append((os.path.getmtime(full), full,
                             os.path.getsize(full)))
    for mtime, full, size in sorted(rows, reverse=True):
        when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {when}  {size / 1e6:6.1f} MB  {full}")


def text_of(content):
    """Flatten a message content field to plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    out = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            out.append(block.get("text", ""))
    return "\n".join(out)


def tool_calls(content):
    """Summarise tool_use blocks as (name, one-line description)."""
    calls = []
    if not isinstance(content, list):
        return calls
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_use"):
            continue
        name = block.get("name", "?")
        inp = block.get("input", {}) or {}
        desc = (inp.get("description") or inp.get("command")
                or inp.get("file_path") or inp.get("pattern")
                or inp.get("prompt") or "")
        desc = " ".join(str(desc).split())
        if len(desc) > 100:
            desc = desc[:97] + "..."
        calls.append((name, desc))
    return calls


def is_tool_result(content):
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result"
        for b in content)


def extract(path):
    turns = []
    with open(path, encoding="utf8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            kind = rec.get("type")
            if kind in SKIP_TYPES or kind not in ("user", "assistant"):
                continue

            msg = rec.get("message") or {}
            content = msg.get("content")
            ts = rec.get("timestamp", "")

            if kind == "user":
                if is_tool_result(content):
                    continue
                body = text_of(content).strip()
                if not body or body.lstrip().startswith(META_PREFIXES):
                    continue
                turns.append(("user", ts, body, []))
            else:
                body = text_of(content).strip()
                calls = tool_calls(content)
                if not body and not calls:
                    continue
                turns.append(("assistant", ts, body, calls))
    return turns


def render(turns, out):
    src_note = (
        "Extracted verbatim from a Claude Code session log by\n"
        "`tools/extract_transcript.py`. User prompts and Claude's replies are\n"
        "unedited. Tool calls are summarised as single lines - the full\n"
        "arguments are often entire files - so the shape of the work is\n"
        "visible without the bulk.\n")
    out.write("# Session transcript\n\n" + src_note + "\n---\n\n")

    n_user = 0
    # Merge consecutive assistant records into one reply block, which is how
    # they read in the terminal: Claude often emits text, then tools, then
    # more text within a single turn.
    i = 0
    while i < len(turns):
        role, ts, body, calls = turns[i]
        if role == "user":
            n_user += 1
            when = ts[:19].replace("T", " ") if ts else ""
            out.write(f"## {n_user}. User\n\n")
            if when:
                out.write(f"*{when}*\n\n")
            out.write(body.rstrip() + "\n\n")
            i += 1
            continue

        # gather the whole assistant turn
        chunks, allcalls = [], []
        while i < len(turns) and turns[i][0] == "assistant":
            _, _, b, c = turns[i]
            if b:
                chunks.append(b)
            allcalls.extend(c)
            i += 1
        out.write("### Claude\n\n")
        if allcalls:
            out.write(f"<details><summary>{len(allcalls)} tool "
                      f"call{'s' if len(allcalls) != 1 else ''}</summary>\n\n")
            for name, desc in allcalls:
                out.write(f"- `{name}` — {desc}\n" if desc
                          else f"- `{name}`\n")
            out.write("\n</details>\n\n")
        if chunks:
            out.write("\n\n".join(c.rstrip() for c in chunks) + "\n\n")
    return n_user


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session", nargs="?", help="path to a .jsonl session log")
    ap.add_argument("-o", "--output", default="-")
    ap.add_argument("--find", action="store_true",
                    help="list available session logs, newest first")
    args = ap.parse_args()

    if args.find or not args.session:
        find_sessions()
        return 0

    turns = extract(args.session)
    if args.output == "-":
        n = render(turns, sys.stdout)
    else:
        with open(args.output, "w", encoding="utf8") as f:
            n = render(turns, f)
        print(f"wrote {args.output}: {n} prompts, "
              f"{sum(1 for t in turns if t[0] == 'assistant')} reply records")
    return 0


if __name__ == "__main__":
    sys.exit(main())
