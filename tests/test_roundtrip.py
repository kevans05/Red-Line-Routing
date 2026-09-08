"""Golden round-trip tests for core.model — the Phase 0 safety net.

Run from anywhere:  python3 tests/test_roundtrip.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.model import to_dict, from_dict, ensure_ids   # noqa: E402

# A complete .redline project, keys in the canonical order to_dict emits, so the
# JSON-string comparison below is meaningful.
FIXTURE = {
    "schema": 2,
    "plan_id": "0123456789abcdef0123456789abcdef",
    "project": "Demo Site Work Order 7",
    "title_page": {
        "notes": "first line\nsecond line",
        "crows": [{"outage_number": "CR-1", "url": "http://crow/1", "files": ["outage.pdf"]}],
        "tailboard_signons": [], "tailboard_done": False,
        "safety_signons": [], "safety_done": False,
    },
    "drawing_registry": {
        "AAAA-HZZ-0001-1": {"title": "Schematic", "rev": "C", "url": "http://d/1", "notes": ""}
    },
    "relay_settings": {
        "DEV1": {"title": "", "revision": "", "engineer": "", "contact": "",
                 "url": "", "wo_device": "", "aspen_model": "", "aspen_url": "", "aspen_notes": ""}
    },
    "maintenance_standards": {},
    "engineering_standards": {},
    "pts_files": {},
    "tailboard_refs": {"tailboard": {"url": ""}, "hbr": {"url": ""},
                       "loa": {"url": ""}, "safety_regs": {"url": ""}},
    "history": {"device": ["D1"], "location": [], "pin": [], "panel": [], "wire": ["RD"]},
    "jobs": [{
        "type": "REMOVE", "description": "remove wire", "wire": "RD",
        "start": {"device": "D1", "location": "L1", "pin": "1", "panel": "P1",
                  "drawing": "", "drawing_rev": "", "drawing_url": "", "drawing_cell": ""},
        "end": {"device": "D2", "location": "L2", "pin": "2", "panel": "P2",
                "drawing": "", "drawing_rev": "", "drawing_url": "", "drawing_cell": ""},
        "notes": "", "completed": False, "id": "feedface00000000feedface00000000",
    }],
}


class RoundTrip(unittest.TestCase):
    def test_complete_project_roundtrips_exactly(self):
        self.assertEqual(to_dict(from_dict(FIXTURE)), FIXTURE)

    def test_json_bytes_are_stable(self):
        once = json.dumps(to_dict(from_dict(FIXTURE)), indent=2)
        twice = json.dumps(to_dict(from_dict(json.loads(once))), indent=2)
        self.assertEqual(once, twice)
        # …and identical to serialising the fixture directly (byte-compatibility).
        self.assertEqual(once, json.dumps(FIXTURE, indent=2))

    def test_relay_key_is_translated_both_ways(self):
        # on-disk "relay_settings" <-> in-memory "relay_registry"
        state = from_dict(FIXTURE)
        self.assertEqual(state["relay_registry"], FIXTURE["relay_settings"])
        self.assertEqual(to_dict(state)["relay_settings"], FIXTURE["relay_settings"])
        self.assertNotIn("relay_registry", to_dict(state))

    def test_partial_file_is_normalised_then_stable(self):
        # An older file missing most keys gains defaults, then never changes again.
        out1 = to_dict(from_dict({"project": "x", "jobs": []}))
        self.assertEqual(out1["relay_settings"], {})
        self.assertEqual(out1["history"], {"device": [], "location": [], "pin": [], "panel": [], "wire": []})
        self.assertEqual(list(out1), list(FIXTURE))            # same canonical key order
        out2 = to_dict(from_dict(out1))
        self.assertEqual(out1, out2)

    def test_migration_backfills_ids_idempotently(self):
        # An older file with no schema/plan_id and no job ids gains them on load…
        old = {"project": "Legacy", "jobs": [{"type": "REMOVE", "description": "x"}]}
        s1 = ensure_ids(from_dict(old))
        self.assertEqual(s1["schema"], 2)
        self.assertEqual(len(s1["plan_id"]), 32)
        self.assertEqual(len(s1["jobs"][0]["id"]), 32)
        out1 = to_dict(s1)
        self.assertEqual(out1["plan_id"], s1["plan_id"])
        self.assertEqual(out1["jobs"][0]["id"], s1["jobs"][0]["id"])
        # …and re-running never changes an existing id (idempotent).
        s2 = ensure_ids(from_dict(out1))
        self.assertEqual(s2["plan_id"], s1["plan_id"])
        self.assertEqual(s2["jobs"][0]["id"], s1["jobs"][0]["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
