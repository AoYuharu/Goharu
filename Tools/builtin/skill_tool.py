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


def _find_skill_file(subdir):
    """大小写不敏感地找 skill 文件（skill.md / SKILL.md）。"""
    for child in subdir.iterdir():
        if child.is_file() and child.name.lower() == "skill.md":
            return child
    return None


def _parse_skill_meta(content, fallback_name):
    """优先解析 YAML frontmatter（Anthropic 标准技能格式），
    否则回退到旧约定：前两行非空行 = 标题 + 描述。"""
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            try:
                import yaml
                meta = yaml.safe_load(content[3:end])
                if isinstance(meta, dict) and meta.get("name"):
                    return str(meta["name"]), str(meta.get("description") or "")
            except Exception:
                pass
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    name = lines[0].lstrip("#").strip() if lines else fallback_name
    description = lines[1] if len(lines) > 1 else ""
    return name, description


def scan_skills():
    """Scan all subdirectories of Skills/ for skill.md / SKILL.md."""
    skills = []
    by_dir = {}

    if not SKILLS_DIR.is_dir():
        logger.debug(f"Skills directory not found: {SKILLS_DIR}")
        return skills, by_dir

    for subdir in sorted(SKILLS_DIR.iterdir()):
        if not subdir.is_dir():
            continue
        skill_file = _find_skill_file(subdir)
        if skill_file is None:
            continue

        try:
            content = skill_file.read_text(encoding="utf-8")
            name, description = _parse_skill_meta(content, subdir.name)

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


def _load_single_skill(dir_name):
    """加载指定子目录的 skill 并注册进缓存（大小写不敏感）。
    用于运行期新增的 skill：未命中时按需补扫单个目录，不全量重扫。"""
    key = dir_name.lower()
    if not SKILLS_DIR.is_dir():
        return None
    for subdir in SKILLS_DIR.iterdir():
        if not subdir.is_dir() or subdir.name.lower() != key:
            continue
        skill_file = _find_skill_file(subdir)
        if skill_file is None:
            return None
        try:
            content = skill_file.read_text(encoding="utf-8")
            name, description = _parse_skill_meta(content, subdir.name)
            entry = {"name": name, "description": description, "dir_name": subdir.name}
            SKILLS_BY_DIR[subdir.name] = entry
            AVAILABLE_SKILLS.append(entry)
            logger.info(f"Skill hot-loaded: {subdir.name} -> {name}")
            return entry
        except Exception as exc:
            logger.warning(f"Failed to read skill from {skill_file}: {exc}")
            return None
    return None


AVAILABLE_SKILLS, SKILLS_BY_DIR = scan_skills()


def build_skill_description():
    """Build the Skill tool description listing all available skills."""
    base = (
        "Load a specific skill's full instruction document into the conversation context. "
        "Skills provide specialized capabilities, workflows, templates, and bundled resources "
        "that MUST be followed when applicable.\n"
        "When a skill matches the user's request, this is a BLOCKING REQUIREMENT: "
        "invoke this tool BEFORE generating any other response about the task. "
        "NEVER mention a skill without actually calling this tool. "
        "A skill's content is a mandatory procedure/template, not reference material — "
        "do not improvise your own approach when a matching skill exists. "
        "If a skill has already been loaded in this conversation "
        "(marked by [Skill Loaded: ...]), follow its instructions directly "
        "instead of calling this tool again.\n"
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
        skill_entry = _load_single_skill(key)

    if skill_entry is None:
        return json.dumps({
            "error": f"Skill not found: {name}",
            "available_skills": [s["name"] for s in AVAILABLE_SKILLS],
        }, ensure_ascii=False)

    try:
        skill_file = _find_skill_file(SKILLS_DIR / skill_entry["dir_name"])
        if skill_file is None:
            raise FileNotFoundError("skill file missing")
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
