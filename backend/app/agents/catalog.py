"""Versioned, curated skill packages; selection never grants permissions."""

import hashlib
import re
from pathlib import Path

CATALOG = (
    (
        "operational-analysis",
        "Operational investigation, maintenance, anomaly and data validation",
        (
            "validation",
            "data",
            "anomaly",
            "fault",
            "furnace",
            "maintenance",
            "sop",
            "incident",
            "diagnos",
        ),
    ),
    (
        "management-briefing",
        "Management briefs, shift handover and production comparisons",
        ("brief", "handover", "shift", "production", "weekly", "report", "compare", "management"),
    ),
    (
        "personal-assistant",
        "Personal notes, commitments, reminders and meeting preparation",
        ("note", "remember", "remind", "meeting", "calendar", "thought", "task", "prepare"),
    ),
)


class SkillCatalog:
    def select(self, query: str, workspace_id: str | None, limit: int = 3) -> list[dict]:
        lowered = query.casefold()
        ranked = sorted(CATALOG, key=lambda s: sum(word in lowered for word in s[2]), reverse=True)
        selected = [s for s in ranked if any(word in lowered for word in s[2])][:limit]
        if not selected:
            selected = [CATALOG[0] if workspace_id else CATALOG[2]]
        result = []
        for name, description, _ in selected:
            path = Path(__file__).parent / "skills" / (name + ".md")
            source = path.read_text(encoding="utf-8")
            # Static trusted paths only; no filenames or executable scripts from the model.
            body = re.sub(r"^---\n.*?\n---\n", "", source, count=1, flags=re.S)
            result.append(
                {
                    "name": name,
                    "description": description,
                    "version": hashlib.sha256(source.encode()).hexdigest()[:16],
                    "instructions": body,
                }
            )
        return result
