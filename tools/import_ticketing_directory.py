"""Append researched ticketing performers to the roster without changing existing rows.

The source directory intentionally contains cancelled listings, productions and
unresolved evidence.  This importer keeps the broad research value while
excluding productions and cancellation-only records.  New names always enter
as review candidates; no listing silently verifies an identity or keyword.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

BASE = Path(__file__).resolve().parents[1]
SCOUT = BASE / "scout"
if str(SCOUT) not in sys.path:
    sys.path.insert(0, str(SCOUT))

import model  # noqa: E402
import roster  # noqa: E402

RESEARCH_DATE = "2026-09-08"
DEFAULT_SOURCE = BASE / "out" / f"ticketing-directory-{RESEARCH_DATE}" / "artist_directory.json"
EXCLUDED_STATUSES = {"archived", "cancelled_only", "outside_window"}
CONFIRMED_EQUIVALENTS = {
    # The ticketing source shortens the already-reviewed canonical identity.
    "shreya priyam": "shreya-priyam-roy",
}
MAJOR_HOSTS = {
    "bookmyshow.com", "in.bookmyshow.com", "district.in", "www.district.in",
    "insider.in", "www.insider.in", "in.bmscdn.com", "skillboxes.com",
    "www.skillboxes.com", "ticketgenie.in", "www.ticketgenie.in",
    "ticketnew.com", "www.ticketnew.com", "sortmyscene.com", "www.sortmyscene.com",
}


def _read(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _identity_key(value: object) -> str:
    """Exact identity comparison with only case and repeated space ignored."""
    return " ".join(str(value or "").strip().casefold().split())


def _valid_url(value: object) -> bool:
    try:
        parsed = urlsplit(str(value or ""))
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def _evidence_score(event: dict) -> tuple:
    host = (urlsplit(str(event.get("url") or "")).hostname or "").lower()
    kind = event.get("evidence_type")
    return (
        int(host in MAJOR_HOSTS),
        int(kind == "performed"),
        int(kind == "past_listing"),
        int(event.get("date_precision") in {"day", "range"}),
        str(event.get("date") or event.get("date_end") or ""),
    )


def _best_evidence(row: dict) -> dict | None:
    candidates = [event for event in row.get("events") or []
                  if isinstance(event, dict)
                  and event.get("verification_status") != "hold"
                  and event.get("evidence_type") != "cancelled_listing"
                  and _valid_url(event.get("url"))]
    return max(candidates, key=_evidence_score, default=None)


def _unique_slug(name: str, category: str, registry: dict) -> tuple[str, bool]:
    base = model.slugify(name)
    if not base:
        raise ValueError(f"name does not produce a valid slug: {name!r}")
    if base not in registry["artists"]:
        return base, False
    # A collision is not permission to modify the older identity.  Keep both
    # records and make the new candidate visibly source-scoped.
    scoped = model.slugify(f"{name}-{category}-ticketing")
    value, index = scoped, 2
    while value in registry["artists"]:
        value = f"{scoped}-{index}"
        index += 1
    return value, True


def plan_import(registry: dict, directory: dict) -> dict:
    niche_ids = {item["id"] for item in roster.load_niches() if not item.get("ui_only")}
    known = {}
    for slug, artist in registry["artists"].items():
        values = [artist.get("name"), *(artist.get("aliases") or [])]
        canonical = str(artist.get("name") or "").strip()
        if " (" in canonical and canonical.endswith(")"):
            values.append(canonical.split(" (", 1)[0])
        for value in values:
            key = _identity_key(value)
            if key:
                known.setdefault(key, slug)

    additions = []
    skipped_existing = []
    excluded = []
    collisions = []
    seen_source = set()
    for row in directory.get("records") or []:
        if not isinstance(row, dict):
            raise ValueError("directory records must be objects")
        name = str(row.get("name") or "").strip()
        category = str(row.get("category") or "")
        key = _identity_key(name)
        if not name or not key:
            raise ValueError("directory record needs a name")
        if category not in niche_ids:
            raise ValueError(f"unknown category for {name}: {category}")
        if key in seen_source:
            raise ValueError(f"duplicate source identity: {name}")
        seen_source.add(key)

        reason = None
        if row.get("entity_type") == "production":
            reason = "production"
        elif row.get("status") in EXCLUDED_STATUSES:
            reason = str(row.get("status"))
        evidence = _best_evidence(row)
        if reason or evidence is None:
            excluded.append({"name": name, "reason": reason or "no usable source evidence"})
            continue
        confirmed_slug = CONFIRMED_EQUIVALENTS.get(key)
        if confirmed_slug and confirmed_slug not in registry["artists"]:
            raise ValueError(f"confirmed equivalence target is missing: {confirmed_slug}")
        if key in known or confirmed_slug:
            skipped_existing.append({"name": name, "slug": confirmed_slug or known[key],
                                     "matched_by": "confirmed_equivalence" if confirmed_slug else "name_or_alias"})
            continue

        slug, collided = _unique_slug(name, category, registry)
        if collided:
            collisions.append({"name": name, "new_slug": slug, "existing_slug": model.slugify(name)})
        event_label = str(evidence.get("event_name") or evidence.get("platform") or "ticketing evidence").strip()
        platform = str(evidence.get("platform") or "public source").strip()
        note = (f"Named in {platform} evidence for {event_label}. Added from the "
                f"{RESEARCH_DATE} India ticketing directory as a review candidate; "
                "the source does not prove ticket sales or North American demand.")
        additions.append(dict(
            slug=slug,
            name=name,
            aliases=[],
            primary_genre=category,
            niche_tags=[category],
            status="candidate",
            measurement_keyword=name,
            keyword_variants=[],
            keyword_review_state="pending",
            search_quality_state="unchecked",
            search_quality_keyword=None,
            search_quality_note="Search query quality has not been reviewed.",
            contamination_status="unchecked",
            contamination_note="Ticketing identity and generic-name risk require review.",
            identity_evidence=dict(
                kind="ticketing_directory_candidate",
                reviewed_at=RESEARCH_DATE,
                note=note,
                url=evidence["url"],
            ),
            first_seen=RESEARCH_DATE,
            verified_at=None,
            last_reviewed=RESEARCH_DATE,
            merged_into=None,
            source=f"ticketing_directory_{RESEARCH_DATE}",
            research_state="directory_candidate",
            curated=True,
            entity_type=row.get("entity_type") or "unknown",
        ))
        known[key] = slug

    return {
        "source_records": len(directory.get("records") or []),
        "additions": additions,
        "added": len(additions),
        "skipped_existing": skipped_existing,
        "excluded": excluded,
        "slug_collisions": collisions,
    }


def apply_import(registry: dict, result: dict) -> dict:
    before = copy.deepcopy(registry["artists"])
    for artist in result["additions"]:
        if artist["slug"] in registry["artists"]:
            raise ValueError(f"planned slug already exists: {artist['slug']}")
        registry["artists"][artist["slug"]] = artist
    for slug, artist in before.items():
        if registry["artists"].get(slug) != artist:
            raise AssertionError(f"existing artist changed during append: {slug}")
    roster.validate(registry)
    return registry


def _summary(result: dict, roster_before: int, roster_after: int, applied: bool) -> dict:
    return {
        "applied": applied,
        "source_records": result["source_records"],
        "added": result["added"],
        "skipped_existing": len(result["skipped_existing"]),
        "excluded": len(result["excluded"]),
        "slug_collisions": result["slug_collisions"],
        "roster_before": roster_before,
        "roster_after": roster_after,
        "added_by_category": dict(sorted(__import__("collections").Counter(
            item["primary_genre"] for item in result["additions"]).items())),
        "added_names": [item["name"] for item in result["additions"]],
    }


def _selftest() -> int:
    existing = dict(slug="existing-act", name="Existing Act", aliases=["Existing Alias"],
                    primary_genre="comedy", niche_tags=["comedy"], status="candidate",
                    measurement_keyword="Existing Act", keyword_review_state="pending",
                    identity_evidence={}, research_state="operator_candidate", curated=False)
    registry = {"schema_version": 1, "updated_at": None, "artists": {"existing-act": existing}}
    event = dict(platform="BookMyShow", url="https://in.bookmyshow.com/events/example/ET0001",
                 event_name="Example Live", date="2025-01-01", date_precision="day",
                 city="Mumbai", country="India", evidence_type="past_listing")
    directory = {"records": [
        dict(name="Existing Alias", category="comedy", entity_type="person", status="past_major_listing", events=[event]),
        dict(name="New Act", category="live_bands", entity_type="band", status="past_major_listing", events=[event]),
        dict(name="Cancelled Act", category="djs", entity_type="person", status="cancelled_only", events=[dict(event, evidence_type="cancelled_listing")]),
        dict(name="A Play", category="theatre_musicals", entity_type="production", status="past_major_listing", events=[event]),
    ]}
    prior = copy.deepcopy(registry["artists"])
    result = plan_import(registry, directory)
    apply_import(registry, result)
    checks = [
        ("alias prevents duplicate", len(result["skipped_existing"]) == 1),
        ("only relevant performer appended", result["added"] == 1 and result["additions"][0]["name"] == "New Act"),
        ("production and cancellation excluded", {row["reason"] for row in result["excluded"]} == {"production", "cancelled_only"}),
        ("existing rows unchanged", registry["artists"]["existing-act"] == prior["existing-act"]),
        ("new artist remains review candidate", registry["artists"]["new-act"]["status"] == "candidate" and registry["artists"]["new-act"]["curated"] is True),
        ("registry validates", roster.validate(registry)),
    ]
    ok = all(good for _, good in checks)
    for label, good in checks:
        print(f"  [{'ok ' if good else 'FAIL'}] {label}")
    print(f"\n  {'ALL CHECKS PASS' if ok else 'SELF-TEST FAILED'}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append source-linked ticketing performers to the Artist Finder roster.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--roster", type=Path, default=Path(roster.PATH))
    parser.add_argument("--apply", action="store_true", help="Persist the append-only plan. Omit for dry-run.")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return _selftest()
    registry = roster.load(str(args.roster))
    before = len(registry["artists"])
    result = plan_import(registry, _read(args.source))
    if args.apply:
        apply_import(registry, result)
        roster.save(registry, str(args.roster))
    print(json.dumps(_summary(result, before, before + result["added"], args.apply), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
