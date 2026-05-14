"""Unit tests for the skill_tool module."""
import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure the project root is on sys.path
_proj = Path(__file__).parent
if str(_proj) not in sys.path:
    sys.path.insert(0, str(_proj))


class TestScanSkills(unittest.TestCase):
    """Test scan_skills() under various disk conditions."""

    def test_skills_dir_not_exists_returns_empty(self):
        """When Skills/ doesn't exist, return ([], {})."""
        from Tools.builtin.skill_tool import scan_skills, SKILLS_DIR

        with patch.object(Path, "is_dir", return_value=False):
            skills, by_dir = scan_skills()
            self.assertEqual(skills, [])
            self.assertEqual(by_dir, {})

    def test_empty_skills_dir_returns_empty(self):
        """When Skills/ exists but has no subdirs, return ([], {})."""
        from Tools.builtin.skill_tool import scan_skills

        with patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "iterdir", return_value=[]):
            skills, by_dir = scan_skills()
            self.assertEqual(skills, [])
            self.assertEqual(by_dir, {})

    def test_valid_skill_is_scanned(self):
        """A subdir with skill.md yields correct name and description."""
        from Tools.builtin.skill_tool import scan_skills

        fake_subdir = MagicMock()
        fake_subdir.configure_mock(name="my_skill", spec=Path)
        fake_subdir.is_dir.return_value = True
        fake_subdir.is_file.return_value = False
        fake_subdir.__truediv__ = MagicMock()

        fake_skill_md = MagicMock(spec=Path)
        fake_skill_md.is_file.return_value = True
        fake_skill_md.read_text.return_value = "My Skill\nA helpful description.\n\nBody content."
        fake_subdir.__truediv__.return_value = fake_skill_md

        with patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "iterdir", return_value=[fake_subdir]):
            skills, by_dir = scan_skills()
            self.assertEqual(len(skills), 1)
            self.assertEqual(skills[0]["name"], "My Skill")
            self.assertEqual(skills[0]["description"], "A helpful description.")
            self.assertEqual(skills[0]["dir_name"], "my_skill")
            self.assertIn("my_skill", by_dir)

    def test_markdown_heading_is_stripped(self):
        """Leading # markdown heading is stripped from the name line."""
        from Tools.builtin.skill_tool import scan_skills

        fake_subdir = MagicMock()
        fake_subdir.configure_mock(name="my_skill", spec=Path)
        fake_subdir.is_dir.return_value = True
        fake_subdir.is_file.return_value = False
        fake_subdir.__truediv__ = MagicMock()

        fake_skill_md = MagicMock(spec=Path)
        fake_skill_md.is_file.return_value = True
        fake_skill_md.read_text.return_value = "# Title With Hash\nDescription line."
        fake_subdir.__truediv__.return_value = fake_skill_md

        with patch.object(Path, "is_dir", return_value=True), \
             patch.object(Path, "iterdir", return_value=[fake_subdir]):
            skills, by_dir = scan_skills()
            self.assertEqual(skills[0]["name"], "Title With Hash")


class TestInvokeSkill(unittest.TestCase):
    """Test invoke_skill() handler behavior."""

    def test_empty_name_returns_error(self):
        """Empty name should return JSON error with available skills."""
        from Tools.builtin.skill_tool import invoke_skill
        result = json.loads(asyncio.run(invoke_skill(name="")))
        self.assertIn("error", result)
        self.assertIn("available_skills", result)

    def test_unknown_skill_returns_error(self):
        """Non-existent skill returns JSON error with available list."""
        from Tools.builtin.skill_tool import invoke_skill, SKILLS_BY_DIR

        with patch.dict(SKILLS_BY_DIR, {}, clear=True):
            result = json.loads(asyncio.run(invoke_skill(name="nonexistent")))
            self.assertIn("error", result)
            self.assertIn("available_skills", result)

    def _run_invoke_with_fake(self, dir_name, entry_name, content):
        """Helper: patch SKILLS_BY_DIR and Path.read_text, run invoke_skill."""
        from Tools.builtin.skill_tool import invoke_skill, SKILLS_BY_DIR

        fake_entry = {"name": entry_name, "description": "desc", "dir_name": dir_name}
        with patch.dict(SKILLS_BY_DIR, {dir_name: fake_entry}, clear=True), \
             patch.object(Path, "read_text", return_value=content):
            return asyncio.run(invoke_skill(name=dir_name))

    def test_known_skill_returns_full_content(self):
        """A known skill returns the full skill.md content with prefix."""
        content = "# TestSkill\nA test skill.\n\nMore text here."
        result = self._run_invoke_with_fake("test", "TestSkill", content)
        self.assertIn("[Skill Loaded: TestSkill]", result)
        self.assertIn(content, result)

    def test_case_insensitive_match(self):
        """Name matching is case-insensitive."""
        content = "Content here."
        from Tools.builtin.skill_tool import invoke_skill, SKILLS_BY_DIR

        fake_entry = {"name": "TestSkill", "description": "desc", "dir_name": "Test_Skill"}
        with patch.dict(SKILLS_BY_DIR, {"Test_Skill": fake_entry}, clear=True), \
             patch.object(Path, "read_text", return_value=content):
            result = asyncio.run(invoke_skill(name="test_skill"))
            self.assertIn("[Skill Loaded: TestSkill]", result)


class TestRegistryRegistration(unittest.TestCase):
    """Verify the Skill tool is registered correctly."""

    def test_skill_is_registered(self):
        """After importing skill_tool, the Skill tool entry exists."""
        from Tools.registry import registry
        entry = registry.get_entry("Skill")
        self.assertIsNotNone(entry, "Skill tool should be registered")
        self.assertEqual(entry.group, "knowledge")
        self.assertTrue(entry.is_async)
        self.assertIn("name", str(entry.arguments_schema.get("properties", {})))
        self.assertIn("name", entry.arguments_schema.get("required", []))

    def test_build_description_with_skills(self):
        """build_skill_description() includes available skills when they exist."""
        from Tools.builtin.skill_tool import build_skill_description, AVAILABLE_SKILLS
        if AVAILABLE_SKILLS:
            desc = build_skill_description()
            self.assertIn("Available skills:", desc)
        else:
            desc = build_skill_description()
            self.assertIn("No skills", desc)

    def test_available_skills_is_list(self):
        """AVAILABLE_SKILLS is a list (possibly empty)."""
        from Tools.builtin.skill_tool import AVAILABLE_SKILLS
        self.assertIsInstance(AVAILABLE_SKILLS, list)


if __name__ == "__main__":
    unittest.main()
