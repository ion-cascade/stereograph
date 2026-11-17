"""Validate the shipped profile and fail closed on mismatched review samples."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from stereograph_bridge.review.profile import Profile
from stereograph_bridge.review.results import compare


DOCS = Path(__file__).resolve().parents[1] / "docs"


class MotionProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads((DOCS / "motion-review-profile.json").read_text())

    def test_shipped_profile_matches_the_schema_and_runtime(self):
        schema = json.loads((DOCS / "motion-review.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(self.profile)
        result = Profile.parse(self.profile)
        self.assertEqual(result.rules[2].base_frames(result.step), [11])
        self.assertEqual(result.surfaces["pad"].group, "Contact pad")

    def test_invalid_interval_and_impossible_sampling_are_rejected(self):
        for changes in ({"frames": [11, 10]}, {"sampling": {"step": .001}},
                        {"action": {}}, {"rules": [{"id": "x", "mode": "clear", "pair": ["a", "b"]}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Profile.parse({**self.profile, **changes})

    def test_absent_counterpart_does_not_establish_a_fix(self):
        before = dict(version=1, profile_signature="same", range=[10, 11], identity={},
                      samples=[dict(rule="sweep", frame=10.5, status="fail", code="surface_crossing")])
        after = {**before, "samples": []}
        diff = compare(before, after)
        self.assertFalse(diff["fixed"])
        self.assertEqual(diff["unverified"][0]["frame"], 10.5)
        invalid = deepcopy(before)
        invalid["profile_signature"] = "different"
        with self.assertRaises(ValueError):
            compare(before, invalid)
