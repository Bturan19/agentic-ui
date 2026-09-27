from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, parts[2].strip()


class SkillLibrary:
    """skills/<isim>/SKILL.md dosyalarini yukler (opencode/Claude skills duzeni)."""

    def __init__(self, base_dir: Path) -> None:
        self._dir = base_dir

    def load(self) -> list[Skill]:
        skills: list[Skill] = []
        if not self._dir.exists():
            return skills
        for path in sorted(self._dir.glob("*/SKILL.md")):
            meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
            skills.append(
                Skill(
                    name=meta.get("name") or path.parent.name,
                    description=meta.get("description", ""),
                    body=body,
                    path=path,
                )
            )
        return skills

    def read(self, name: str) -> Skill | None:
        for skill in self.load():
            if skill.name == name or skill.path.parent.name == name:
                return skill
        return None

    def index_markdown(self) -> str:
        skills = self.load()
        if not skills:
            return "(tanimli skill yok)"
        return "\n".join(f"- {skill.name}: {skill.description}" for skill in skills)
