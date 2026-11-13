import json, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import main  # noqa: E402

CASES = {"overwrite_config.json": (5, {2, 4}), "deleted_dependency.json": (6, {0, 3, 5}), "regression_amid_noise.json": (9, {4, 8})}


class DdminTests(unittest.TestCase):
    def test_fixtures_reduce_to_hand_verified_minimum(self):
        for name, (n, ids) in CASES.items():
            t = main.load_transcript(ROOT / "fixtures" / name)
            self.assertEqual(len(t["turns"]), n)
            got = main.ddmin(t["turns"], t["recorded_failure"])
            self.assertEqual({x["id"] for x in got}, ids, name)

    def test_selfcheck_exit_zero(self):
        self.assertEqual(main.run_selfcheck(), 0)

    def test_missing_file_is_a_clean_error(self):
        r = subprocess.run([sys.executable, str(ROOT / "main.py"), "nope.json"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback", r.stderr)

    def test_cli_writes_min_json(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "m.json"
            r = subprocess.run([sys.executable, str(ROOT / "main.py"), str(ROOT / "fixtures" / "overwrite_config.json"), "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(out.read_text())["minimal_turn_ids"], [2, 4])


if __name__ == "__main__":
    unittest.main()
