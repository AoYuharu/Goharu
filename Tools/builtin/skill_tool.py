"""
Skill Tool - Load skill instruction documents from Skills/ directory.
"""
import json
import logging
from pathlib import Path

from Tools.registry import registry

logger = logging.getLogger(__name__)

_project_root = Path(__file__).parent.parent.parent
SKILLS_DIR = _project_root / "Skills"


def scan_skills():
    """Scan all subdirectories of Skills/ for skill.md, reading first two non-empty lines."""
    skills = []
    by_dir = {}

    if not SKILLS_DIR.is_dir():
        logger.debug(f"Skills directory not found: {SKILLS_DIR}")
        return skills, by_dir

    for subdir in sorted(SKILLS_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        skill_file = subdir / "skill.md"
        if not skill_file.is_file():
            continue

        try:
            content = skill_file.read_text(encoding="utf-8")
            lines = [line.strip() for line in content.splitlines() if line.strip()]

            name = lines[0].lstrip("#").strip() if lines else subdir.name
            description = lines[1] if len(lines) > 1 else ""

            skills.append({
                "name": name,
                "description": description,
                "dir_name": subdir.name,
            })
            by_dir[subdir.name] = skills[-1]
            logger.debug(f"Loaded skill: {subdir.name} -> {name}")
        except Exception as exc:
            logger.warning(f"Failed to read skill from {skill_file}: {exc}")

    return skills, by_dir


AVAILABLE_SKILLS, SKILLS_BY_DIR = scan_skills()


def build_skill_description():
    """Build the Skill tool description listing all available skills."""
    base = (
        "Load a specific skill's full instruction document into the conversation context. "
        "Use this when you need specialized guidance for a task. "
    )
    if not AVAILABLE_SKILLS:
        base += "No skills are currently available."
        return base

    items = []
    for skill in AVAILABLE_SKILLS:
        if skill["description"]:
            items.append(f"{skill['name']} ({skill['dir_name']}): {skill['description']}")
        else:
            items.append(f"{skill['name']} ({skill['dir_name']})")
    base += "Available skills:\n" + "\n".join(items)
    return base


async def invoke_skill(name: str = "") -> str:
    """Load the full content of a skill by its subfolder name."""
    key = str(name).strip()
    if not key:
        return json.dumps({
            "error": "Skill name is required.",
            "available_skills": [s["name"] for s in AVAILABLE_SKILLS],
        }, ensure_ascii=False)

    search_key = key.lower()
    skill_entry = SKILLS_BY_DIR.get(key)
    if skill_entry is None:
        for dir_name, entry in SKILLS_BY_DIR.items():
            if dir_name.lower() == search_key:
                skill_entry = entry
                break

    if skill_entry is None:
        return json.dumps({
            "error": f"Skill not found: {name}",
            "available_skills": [s["name"] for s in AVAILABLE_SKILLS],
        }, ensure_ascii=False)

    try:
        skill_file = SKILLS_DIR / skill_entry["dir_name"] / "skill.md"
        content = skill_file.read_text(encoding="utf-8")
        return f"[Skill Loaded: {skill_entry['name']}]\n\n{content}"
    except Exception as exc:
        return json.dumps({
            "error": f"Failed to load skill content: {exc}",
            "available_skills": [s["name"] for s in AVAILABLE_SKILLS],
        }, ensure_ascii=False)


registry.register(
    name="Skill",
    description=build_skill_description(),
    arguments_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The skill subfolder name to load (case-insensitive).",
            },
        },
        "required": ["name"],
    },
    handler=invoke_skill,
    group="knowledge",
    is_async=True,
)
