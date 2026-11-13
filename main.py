#!/usr/bin/env python3
"""Delta-debugger for failed agent-session transcripts.

Takes a saved transcript of agent turns (tool calls like write_file,
delete_file, run_check) that is known to end in a failure, replays
candidate subsets of those turns against a small deterministic virtual
filesystem (no live model calls, no real disk writes), and uses Zeller's
ddmin delta-debugging algorithm to bisect down to the minimal ordered
subsequence of turns that still reproduces the exact recorded failure.

Usage:
    python3 main.py <transcript.json> [--out minimal.json]
    python3 main.py --selfcheck
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Replay engine: turns are tool calls against an in-memory virtual
# filesystem (path -> content). _DELETED is a tombstone so "deleted" is
# distinguishable from "never created" -- that distinction is what lets the
# bisector prove a delete_file turn is actually load-bearing for a failure,
# rather than collapsing every missing-file failure to "nothing ran".
# ---------------------------------------------------------------------------

_DELETED = object()


def _exists(state: dict, path: str) -> bool:
    return path in state and state[path] is not _DELETED


def apply_turn(state: dict, turn: dict) -> None:
    """Execute one recorded tool call against the replay state in place.
    Raises on the same error conditions a real tool invocation would hit."""
    tool = turn["tool"]
    args = turn.get("args", {})
    path = args.get("path")

    if tool == "write_file":
        state[path] = args["content"]
    elif tool == "append_file":
        if not _exists(state, path):
            raise FileNotFoundError(f"append_file: {path} does not exist")
        state[path] += args["content"]
    elif tool == "delete_file":
        if not _exists(state, path):
            raise FileNotFoundError(f"delete_file: {path} does not exist")
        state[path] = _DELETED
    elif tool == "run_check":
        if path in state and state[path] is _DELETED:
            raise FileNotFoundError(f"run_check: {path} was deleted earlier in this session")
        if path not in state:
            raise FileNotFoundError(f"run_check: {path} was never created")
        if args["expect_substring"] not in state[path]:
            raise AssertionError(
                f"run_check: {path} missing expected content "
                f"{args['expect_substring']!r} (actual={state[path]!r})"
            )
    else:
        raise ValueError(f"unknown tool in transcript: {tool!r}")


def replay(turns: list[dict]) -> Exception | None:
    """Replay turns in original order from a fresh state. Returns the
    exception raised, or None if the whole subsequence completed cleanly."""
    state: dict = {}
    for turn in turns:
        try:
            apply_turn(state, turn)
        except Exception as exc:  # noqa: BLE001 - inspecting any failure is the point
            return exc
    return None


def matches_recorded_failure(exc: Exception | None, recorded_failure: dict) -> bool:
    """Whether a replay's outcome is the SAME failure as the one the
    transcript was captured for (type + message substring), not just any
    failure. Without this, bisection would converge on the first crash it
    finds instead of the one that actually reproduces the original bug."""
    if exc is None:
        return False
    if type(exc).__name__ != recorded_failure["type"]:
        return False
    needle = recorded_failure.get("message_contains", "")
    return needle in str(exc)


# ---------------------------------------------------------------------------
# Delta debugging (Zeller's ddmin), operating on an ordered list of turns.
# We only ever remove turns, never reorder them, so every candidate is
# naturally replayed in original transcript order.
# ---------------------------------------------------------------------------

def ddmin(turns: list[dict], recorded_failure: dict, trace: list | None = None) -> list[dict]:
    """Bisect `turns` down to a 1-minimal subsequence that still reproduces
    recorded_failure: removing any single remaining turn stops the repro."""

    def reproduces(candidate: list[dict]) -> bool:
        result = matches_recorded_failure(replay(candidate), recorded_failure)
        if trace is not None:
            trace.append({"size": len(candidate), "reproduced": result})
        return result

    if not reproduces(turns):
        raise ValueError(
            "the full transcript does not reproduce its own recorded_failure; nothing to bisect"
        )

    current = list(turns)
    n = 2
    while len(current) >= 2:
        chunk_size = max(1, len(current) // n)
        chunks = [current[i:i + chunk_size] for i in range(0, len(current), chunk_size)]
        reduced = False

        for chunk in chunks:
            chunk_ids = {id(t) for t in chunk}
            complement = [t for t in current if id(t) not in chunk_ids]
            if complement and reproduces(complement):
                current = complement
                n = max(n - 1, 2)
                reduced = True
                break

        if not reduced:
            if n >= len(current):
                break
            n = min(n * 2, len(current))

    return current


# ---------------------------------------------------------------------------
# Transcript I/O
# ---------------------------------------------------------------------------

def load_transcript(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "turns" not in data or "recorded_failure" not in data:
        raise ValueError("transcript must have 'turns' and 'recorded_failure' fields")
    return data


def write_minimal_transcript(transcript: dict, minimal_turns: list[dict], out_path: Path) -> None:
    payload = {
        "session_id": transcript.get("session_id"),
        "recorded_failure": transcript["recorded_failure"],
        "original_turn_count": len(transcript["turns"]),
        "minimal_turn_ids": [t["id"] for t in minimal_turns],
        "turns": minimal_turns,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Selfcheck: runs the real bisector against fixtures whose minimal turn set
# was derived by hand (not by running the tool and copying its answer), and
# verifies the file main.py actually writes by reading it back from disk.
# ---------------------------------------------------------------------------

def run_selfcheck() -> int:
    base = Path(__file__).resolve().parent
    cases = [
        ("overwrite_config.json", {2, 4}),
        ("deleted_dependency.json", {0, 3, 5}),
        ("regression_amid_noise.json", {4, 8}),
    ]

    for name, expected_ids in cases:
        path = base / "fixtures" / name
        transcript = load_transcript(path)
        trace: list = []
        minimal = ddmin(transcript["turns"], transcript["recorded_failure"], trace=trace)
        got_ids = {t["id"] for t in minimal}
        assert got_ids == expected_ids, f"{name}: expected minimal ids {expected_ids}, got {got_ids}"
        assert len(trace) >= 3, f"{name}: ddmin ran too few replay tests to be real bisection ({len(trace)})"
        assert len(minimal) < len(transcript["turns"]), f"{name}: minimal set did not shrink at all"
        # the minimal subsequence must independently re-reproduce the exact recorded failure
        assert matches_recorded_failure(replay(minimal), transcript["recorded_failure"])

    # Prove the artifact main.py writes to disk, by writing it for real and
    # reading back the bytes a user would get -- not just the in-memory object.
    transcript_path = base / "fixtures" / "overwrite_config.json"
    out_path = base / "_selfcheck_minimal.json"
    try:
        transcript = load_transcript(transcript_path)
        minimal = ddmin(transcript["turns"], transcript["recorded_failure"])
        write_minimal_transcript(transcript, minimal, out_path)

        on_disk = json.loads(out_path.read_text(encoding="utf-8"))
        assert on_disk["minimal_turn_ids"] == [2, 4], on_disk["minimal_turn_ids"]
        assert [t["id"] for t in on_disk["turns"]] == [2, 4]
        assert on_disk["original_turn_count"] == 5

        # the file on disk, replayed on its own, must still fail the same way
        exc = replay(on_disk["turns"])
        assert matches_recorded_failure(exc, on_disk["recorded_failure"])
    finally:
        if out_path.exists():
            out_path.unlink()

    print(
        "selfcheck: OK - 3 fixtures bisected to hand-verified minimal turn ranges "
        "(5->2, 6->3, 9->2 turns); write/read round-trip of the minimal-transcript "
        "artifact verified from disk."
    )
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Delta-debug a failed agent-session transcript down to the minimal "
            "turn range that still reproduces the recorded failure."
        ),
    )
    parser.add_argument("transcript", nargs="?", help="path to a failing transcript JSON file")
    parser.add_argument("--out", help="where to write the minimal repro transcript (default: <name>.min.json next to the input file)")
    parser.add_argument("--selfcheck", action="store_true", help="run the built-in correctness check and exit")
    args = parser.parse_args(argv)

    if args.selfcheck:
        return run_selfcheck()

    if not args.transcript:
        parser.error("a transcript path is required (or pass --selfcheck)")

    in_path = Path(args.transcript)
    try:
        transcript = load_transcript(in_path)
    except (OSError, ValueError) as exc:  # missing file, bad JSON (JSONDecodeError is a ValueError), missing fields
        print(f"error: cannot read transcript {in_path}: {exc}", file=sys.stderr)
        return 1

    trace: list = []
    try:
        minimal = ddmin(transcript["turns"], transcript["recorded_failure"], trace=trace)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out) if args.out else in_path.parent / f"{in_path.stem}.min.json"
    try:
        write_minimal_transcript(transcript, minimal, out_path)
    except OSError as exc:
        print(f"error: cannot write {out_path}: {exc}", file=sys.stderr)
        return 1

    total = len(transcript["turns"])
    kept = len(minimal)
    rf = transcript["recorded_failure"]
    print(f"transcript:        {in_path}")
    print(f"recorded failure:  {rf['type']}: {rf.get('message_contains', '')!r}")
    print(f"original turns:    {total}")
    print(f"minimal turns:     {kept}  (ids: {[t['id'] for t in minimal]})")
    print(f"reduction:         {100 * (1 - kept / total):.0f}%")
    print(f"replay tests run:  {len(trace)}")
    print(f"minimal repro ->   {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
