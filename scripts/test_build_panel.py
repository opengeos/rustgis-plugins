"""Manifest form compatibility with RustGIS's PanelItemDecl and PanelDecl::validate."""

import unittest

import build


class PanelFormTests(unittest.TestCase):
    def check(self, item, **panel):
        build.problems.clear()
        build.check_panel({"commands": [{"id": "refresh"}], "panel": {
            "title": "Form", "items": [item], **panel,
        }}, "test")
        return list(build.problems)

    def test_form_with_and_without_command(self):
        for command in ({}, {"command": "refresh"}, {"command": ""}, {"default": None}):
            with self.subTest(command=command):
                self.assertEqual(self.check({"kind": "form", "id": "config", **command}), [])

    def test_invalid_form_declarations(self):
        for change in (
            {"command": "missing"}, {"command": 3}, {"command": None}, {"id": "Bad"}, {"id": "x" * 65},
            {"label": "x" * 2001}, {"help": "x" * 2001}, {"text": "static"},
            {"label": 1}, {"search": True}, {"search": "yes"},
            {"min": 1}, {"max": 1}, {"steps": True}, {"options": ["a"]},
            {"default": {}}, {"default": True}, {"shape": "point"}, {"shape": 1},
            {"min": False}, {"min": 0.0}, {"max": None}, {"steps": 0},
            {"options": None}, {"id": "config\n"},
        ):
            with self.subTest(change=change):
                self.assertTrue(self.check({"kind": "form", "id": "config", **change}))

    def test_panel_item_limit(self):
        items = [{"kind": "form", "id": f"f-{n}"} for n in range(33)]
        self.assertTrue(self.check(items[0], items=items))

    def test_duplicate_form_ids(self):
        item = {"kind": "form", "id": "config"}
        self.assertTrue(self.check(item, items=[item, item]))

    def test_declared_text_boundary(self):
        self.assertEqual(self.check({"kind": "form", "id": "config", "label": "x" * 2000}), [])

    def test_unknown_kind_still_rejected(self):
        self.assertTrue(self.check({"kind": "forms", "id": "config"}))

    def test_unknown_fields_follow_host_serde_behavior(self):
        # Fields are supplied in command replies, not PanelItemDecl. Serde ignores unknown
        # declaration keys; the 64-field/256-choice runtime limits do not apply here.
        self.assertEqual(self.check({"kind": "form", "id": "config",
                                    "fields": [{}] * 65, "unknown": True}), [])


if __name__ == "__main__":
    unittest.main()
