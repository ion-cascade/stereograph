"""Clothing profile parsing and bounded correction envelopes."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from stereograph_bridge.fitting.corrective_profile import Corrective
from stereograph_bridge.fitting.profile import FitProfile
from stereograph_bridge.wire import valid_timeout

DOCS = Path(__file__).resolve().parents[1] / "docs"


class FitProfileTests(unittest.TestCase):
    def setUp(self):
        self.fit = json.loads((DOCS / "clothing-fit-profile.json").read_text())
        self.plan = json.loads((DOCS / "cloth-corrective-profile.json").read_text())

    def test_examples_parse_and_geometry_roles_resolve(self):
        fit = FitProfile.parse(self.fit)
        self.assertEqual(fit.layers["skirt"].kind, "cloth")
        self.assertEqual(fit.attachments["sewn-edge"].a.vertex, 280)
        self.assertEqual(fit.strain["panel"].group, "Feathered knee patch")
        plan = Corrective.parse(self.plan)
        self.assertEqual(plan.pairs[0].obstacle.object, "Knee plate")
        self.assertEqual(plan.times, (8., 8.5, 9., 9.5, 10., 10.5, 11., 11.5, 12.))

    def test_landmarks_reject_ambiguous_or_missing_roles(self):
        landmark = self.fit["attachments"]["sewn-edge"]["a"]
        landmark["point"] = [0, 0, 0]
        with self.assertRaisesRegex(ValueError, "vertex index or"):
            FitProfile.parse(self.fit)
        del landmark["vertex"]
        landmark["layer"] = "missing"
        with self.assertRaisesRegex(ValueError, "undeclared"):
            FitProfile.parse(self.fit)

    def test_invalid_envelope_and_overlapping_protected_region(self):
        for field, value in (("direction", [0, 0, 0]), ("distance", float("inf")),
                             ("protected_groups", [self.plan["mask"]]), ("extra_frames", list(range(9)))):
            with self.subTest(field=field), self.assertRaises(ValueError):
                Corrective.parse({**self.plan, field: value})

    def test_plan_cannot_self_measure_or_redirect_garment_region(self):
        for key in ("obstacle", "garment_region"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                plan = deepcopy(self.plan)
                plan["pairs"][0][key] = {"object": plan["garment"]}
                Corrective.parse(plan)

    def test_long_deadline_limited_to_fit_evaluation(self):
        for operation in ("fit_inspect", "cloth_preview", "cloth_apply"):
            self.assertTrue(valid_timeout(600, operation))
            self.assertFalse(valid_timeout(600.01, operation))
        self.assertFalse(valid_timeout(600, "cloth_cancel"))
