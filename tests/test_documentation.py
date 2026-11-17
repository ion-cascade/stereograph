import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from stereograph.documentation import api_lookup, search_docs


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        for name in ("api", "manual"):
            (self.root / name).mkdir()
        (self.root / "api/bpy.types.Object.rst").write_text(
            "Object\n======\n\n.. attribute:: location\n\n   Position in local space.\n"
            "\n.. attribute:: scale\n\n   Scale factors.\n",
            encoding="utf-8",
        )
        (self.root / "manual/local_view.rst").write_text("Local View\n==========\nIsolate objects.\n", encoding="utf-8")
        env = patch.dict(os.environ, {"STEREOGRAPH_DOCS_ROOT": str(self.root), "STEREOGRAPH_DOCS_VERSION": "test"})
        env.start()
        self.addCleanup(env.stop)

    def test_member_lookup_stops_at_the_next_definition(self):
        result = api_lookup("bpy.types.Object.location")["data"]
        self.assertIn("Position in local space", result["content"])
        self.assertNotIn("Scale factors", result["content"])
        self.assertEqual(result["version"], "test")

    def test_wildcard_and_index(self):
        self.assertEqual(api_lookup("bpy.types.*")["data"]["identifiers"], ["bpy.types.Object"])
        self.assertEqual(api_lookup("bpy.types.*", index=1)["data"]["identifiers"], [])

    def test_manual_search_and_empty_result(self):
        result = search_docs("manual", "local view", 3, 1, None)["data"]
        self.assertEqual(result["results"][0]["path"], "local_view.rst")
        self.assertEqual(search_docs("manual", "missing", 3, 0, 0)["data"]["results"], [])

    def test_invalid_identifier_and_query(self):
        self.assertEqual(api_lookup("../outside")["error"]["code"], "invalid_identifier")
        self.assertEqual(search_docs("manual", " ", 3, 0, 0)["error"]["code"], "bad_query")
