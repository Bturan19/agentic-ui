from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class LearningStore:
    """Ajanin kalici ogrenimlerini tutar (data/memory/learnings.md)."""

    def __init__(self, base_dir: Path, tz: str = "Europe/Istanbul") -> None:
        self._path = base_dir / "learnings.md"
        self._tz = ZoneInfo(tz)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text("# Ogrenimler\n\n", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self._path

    def append(self, text: str, tags: list[str] | None = None, source: str | None = None) -> str:
        clean = " ".join(text.split())
        if not clean:
            raise ValueError("Ogrenim metni bos olamaz")

        existing = self._path.read_text(encoding="utf-8")
        normalized = clean.lower().rstrip(".")
        if normalized in existing.lower():
            return "zaten kayitli"

        stamp = datetime.now(self._tz).strftime("%Y-%m-%d %H:%M")
        suffix = " ".join(f"#{tag.strip().lstrip('#')}" for tag in (tags or []) if tag.strip())
        source_note = f" _(kaynak: {source})_" if source else ""
        line = f"- [{stamp}] {clean}" + (f" — {suffix}" if suffix else "") + source_note
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return "kaydedildi"

    def read(self, max_chars: int = 6000) -> str:
        text = self._path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            text = "# Ogrenimler (son kayitlar)\n…\n" + text[-max_chars:]
        return text
