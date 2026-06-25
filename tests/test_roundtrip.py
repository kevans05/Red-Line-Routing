"""Golden round-trip tests for core.model — the Phase 0 safety net.

Run from anywhere:  python3 tests/test_roundtrip.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.model import to_dict, from_dict   # noqa: E402

# A complete .redline project, keys in the canonical order to_dict emits, so the
# JSON-string comparison below is meaningful.
FIXTURE = {
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
        "notes": "", "completed": False,
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
