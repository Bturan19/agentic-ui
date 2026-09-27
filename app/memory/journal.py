from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class JournalWriter:
    """Ajanin gunluk karar/aksiyon gunlugunu markdown olarak yazar.

    Dosyalar: data/journal/YYYY-MM-DD.md
    """

    def __init__(self, base_dir: Path, tz: str = "Europe/Istanbul") -> None:
        self._dir = base_dir
        self._tz = ZoneInfo(tz)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _now(self) -> datetime:
        return datetime.now(self._tz)

    def _path(self, day: str) -> Path:
        return self._dir / f"{day}.md"

    def append(
        self,
        *,
        title: str,
        summary: str = "",
        actions: list[str] | None = None,
        learnings: list[str] | None = None,
        tags: list[str] | None = None,
        conversation_id: str | None = None,
    ) -> str:
        now = self._now()
        day = now.date().isoformat()
        path = self._path(day)

        lines = [f"## {now.strftime('%H:%M')} — {title}"]
        if summary:
            lines += ["", summary.strip()]
        if actions:
            lines += ["", "**Aksiyonlar:**", *[f"- {item}" for item in actions]]
        if learnings:
            lines += ["", "**Ogrenimler:**", *[f"- {item}" for item in learnings]]
        meta = []
        if tags:
            meta.append("etiketler: " + " ".join(f"#{tag}" for tag in tags))
        if conversation_id:
            meta.append(f"konusma: {conversation_id}")
        if meta:
            lines += ["", "_" + " • ".join(meta) + "_"]
        lines.append("")

        if not path.exists():
            path.write_text(f"# {day} — Ajanda Gunlugu\n\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return str(path)

    def list_days(self) -> list[str]:
        return sorted((path.stem for path in self._dir.glob("*.md")), reverse=True)

    def read_day(self, day: str) -> str:
        path = self._path(day)
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def read_recent(self, days: int = 7, max_chars: int = 12000) -> str:
        chunks: list[str] = []
        for day in self.list_days()[:days]:
            chunks.append(self.read_day(day))
        text = "\n".join(chunk for chunk in chunks if chunk.strip())
        if len(text) > max_chars:
            text = "…\n" + text[-max_chars:]
        return text
