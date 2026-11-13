# agent-turn-delta-debugger

Bisect a saved agent-session transcript down to the **minimal set of turns that still reproduces its recorded failure**, using Zeller's `ddmin` delta-debugging algorithm. Python 3 standard library only, no network, no model calls.

A transcript is a JSON list of agent turns (tool calls `write_file`, `append_file`, `delete_file`, `run_check`) plus the `recorded_failure` that was observed (exception type + message substring). The tool replays subsets of the turns against a small deterministic in-memory virtual filesystem and keeps removing turns while the exact same failure still happens. Output: a 1-minimal repro transcript instead of the whole log.

## Quickstart

```
git clone https://github.com/MegaStretch23/agent-turn-delta-debugger.git
cd agent-turn-delta-debugger
python3 main.py fixtures/regression_amid_noise.json
```

Real output (run 2026-11-13, Python 3, clean clone):

```
transcript:        fixtures/regression_amid_noise.json
recorded failure:  AssertionError: 'config.json'
original turns:    9
minimal turns:     2  (ids: [4, 8])
reduction:         78%
replay tests run:  8
minimal repro ->   fixtures/regression_amid_noise.min.json
```

9 recorded turns, 2 of them are the actual cause (ids 4 and 8); the minimal repro is written to `fixtures/regression_amid_noise.min.json` (or pass `--out path.json`). A saved copy of the output file for the 5-turn fixture is in `examples/`.

Other fixtures: `fixtures/overwrite_config.json` (5 turns -> 2: a later write clobbers a good config), `fixtures/deleted_dependency.json` (6 turns -> 3: create + delete + check are all load-bearing).

## Verify it

```
python3 main.py --selfcheck
python3 -m unittest discover -s tests
```

`--selfcheck` runs the bisector on the three fixtures and asserts the result equals minimal turn sets derived by hand beforehand (5->2, 6->3, 9->2 turns), then writes the `.min.json` artifact, re-reads it from disk and replays it to confirm it still fails the same way.

## Transcript format

```json
{
  "session_id": "demo",
  "recorded_failure": {"type": "AssertionError", "message_contains": "config.json"},
  "turns": [
    {"id": 0, "tool": "write_file", "args": {"path": "config.json", "content": "..."}},
    {"id": 1, "tool": "run_check", "args": {"path": "config.json", "expect_substring": "..."}}
  ]
}
```

Every turn needs a unique `id`. Turns are only ever removed, never reordered.

## Limits

Fixed four-tool vocabulary over saved or hand-written transcripts. Not included: capturing real agent-SDK transcripts into this shape, non-deterministic tools (shell, network), and bisecting inside a single tool call's arguments.

## Source pack

The same code, packaged with the fixtures as a zip: **[Gumroad, $25](https://vedovino.gumroad.com/l/zdwyic)**. Everything in it that is code is already free in this repo.

## License

MIT
