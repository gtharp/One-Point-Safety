#!/usr/bin/env python3
"""
Sanity-checks data/history.json. This file is hand-curated, not generated
-- run this after editing it by hand (see README.md for how to add an
entry).

Usage: python3 scripts/validate_history.py
"""

import json
import pathlib
import re
import sys

REQUIRED_EVENT_FIELDS = {"id", "year", "date", "category", "title", "summary", "people", "teams", "disputed", "sources"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def main():
    path = pathlib.Path(__file__).parent.parent / "data" / "history.json"
    data = json.loads(path.read_text())
    errors = []

    cat_ids = set()
    for c in data["categories"]:
        if not {"id", "label", "blurb"} <= c.keys():
            errors.append(f"category {c.get('id','?')}: missing field(s)")
        cat_ids.add(c["id"])

    seen_ids = set()
    for e in data["events"]:
        eid = e.get("id", "<missing id>")
        missing = REQUIRED_EVENT_FIELDS - e.keys()
        if missing:
            errors.append(f"{eid}: missing field(s) {missing}")
        if eid in seen_ids:
            errors.append(f"{eid}: duplicate id")
        seen_ids.add(eid)
        if not ID_RE.match(eid):
            errors.append(f"{eid}: id should be lowercase-kebab-case")
        if e.get("category") not in cat_ids:
            errors.append(f"{eid}: unknown category '{e.get('category')}'")
        if not isinstance(e.get("year"), int):
            errors.append(f"{eid}: year must be an integer")
        if e.get("date") is not None and not DATE_RE.match(e["date"]):
            errors.append(f"{eid}: date must be null or 'YYYY-MM-DD'")
        if e.get("disputed") and not e.get("note"):
            errors.append(f"{eid}: marked disputed but has no 'note' explaining why")
        sources = e.get("sources") or []
        if not sources:
            errors.append(f"{eid}: no sources (every entry should cite at least one)")
        for s in sources:
            if not s.get("url", "").startswith("http"):
                errors.append(f"{eid}: source missing a valid url: {s}")

    if errors:
        print(f"{len(errors)} problem(s) found:")
        for err in errors:
            print(" -", err)
        sys.exit(1)

    print(f"OK: {len(data['categories'])} categories, {len(data['events'])} events, all sourced.")


if __name__ == "__main__":
    main()
