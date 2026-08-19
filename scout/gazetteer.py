"""Known artist names — so one performer does not become three entities.

THE PROBLEM THIS SOLVES, OBSERVED IN THE FIRST REAL CRAWL.
Indian listings glue the show name onto the performer with no separator:

    "Vipul Goyal Unleashed"          -> entity "Vipul Goyal Unleashed"
    "Vipul Goyal-Unleashed"          -> entity "Vipul Goyal-Unleashed"
    "Vipul Goyal Live in Borivali"   -> entity "Vipul Goyal"

Three entities, one comedian, each with a third of his shows — and since this project measures
ESCALATION ACROSS SHOWS, fragmenting an artist is not cosmetic. It splits the very trajectory
the tool exists to detect, and the artist reads as three quiet acts instead of one rising one.

HOW IT WORKS, AND THE GUARD THAT KEEPS IT SAFE.
A name matches a known artist when the known artist's tokens are a CONTIGUOUS RUN inside it:
"vipul goyal unleashed" contains "vipul goyal", so it canonicalises. Requiring contiguity
matters — "Rahul" plus "Dua" scattered across an unrelated title is not Rahul Dua.

Single-token known names are never matched on, and the match must be UNIQUE. "Singh" and
"Gupta" appear in a dozen names, so they resolve to nobody rather than to the wrong person.
Wrongly merging two artists corrupts both histories at once and is far worse than leaving a
variant unmerged.

THE LIST GROWS THREE WAYS.
  1. seeded from the Artist Tour Engine's benchmark (41 names with measured ticket history)
  2. `data/aliases.json`, hand-editable — the same principle as the engine's dossiers
  3. every artist the scout confirms, once someone signs or researches them

It is deliberately NOT auto-populated from crawled names. That would let one bad parse teach
the gazetteer a wrong name, and every later listing would then be merged into the mistake.
"""
import csv
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

ALIASES = os.path.join(model.BASE, 'data', 'aliases.json')
BENCHMARK = os.path.normpath(os.path.join(
    model.BASE, '..', 'Artist Tour Engine', 'data', 'shows.csv'))

MIN_TOKENS = 2          # a one-word known name is not distinctive enough to merge on
_CACHE = None


def _tokens(name):
    return [t for t in re.split(r'[^a-z0-9]+', str(name).lower()) if t]


def load():
    """-> {canonical_name: [token, ...]}. Benchmark plus hand-maintained aliases."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    known = {}

    if os.path.exists(BENCHMARK):
        try:
            with open(BENCHMARK, encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    nm = (row.get('Artist') or '').strip()
                    if nm and len(_tokens(nm)) >= MIN_TOKENS:
                        known[nm] = _tokens(nm)
        except Exception:
            pass                      # a missing or moved benchmark must not break a crawl

    extra = {}
    if os.path.exists(ALIASES):
        with open(ALIASES, encoding='utf-8') as f:
            extra = json.load(f)
    for nm in (extra.get('known') or []):
        if len(_tokens(nm)) >= MIN_TOKENS:
            known[nm] = _tokens(nm)

    _CACHE = dict(known=known,
                  explicit={k.lower(): v for k, v in (extra.get('map') or {}).items()})
    return _CACHE


def _contains_run(hay, needle):
    """Are `needle`'s tokens a contiguous run inside `hay`'s?"""
    n = len(needle)
    return any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def canonical(name):
    """-> (canonical_name, why) or (name, None) when nothing matches confidently."""
    g = load()
    raw = str(name or '').strip()
    if not raw:
        return raw, None

    hit = g['explicit'].get(raw.lower())
    if hit:
        return hit, 'explicit alias'

    toks = _tokens(raw)
    if not toks:
        return raw, None
    matches = [k for k, kt in g['known'].items()
               if len(kt) >= MIN_TOKENS and _contains_run(toks, kt)]
    if len(matches) == 1 and matches[0].lower() != raw.lower():
        return matches[0], f'contains known artist "{matches[0]}"'
    if len(matches) > 1:
        # Two known artists inside one string is a lineup, not a name. Leave it alone and let
        # the lineup splitter deal with it rather than picking a winner arbitrarily.
        return raw, None
    return raw, None


def add_known(name):
    """Record a confirmed artist so later variants canonicalise onto them."""
    global _CACHE
    data = {'known': [], 'map': {}}
    if os.path.exists(ALIASES):
        with open(ALIASES, encoding='utf-8') as f:
            data = json.load(f)
    data.setdefault('known', [])
    if name not in data['known']:
        data['known'].append(name)
        data['known'].sort()
    with open(ALIASES, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    _CACHE = None
    return name


def add_alias(variant, canonical_name):
    global _CACHE
    data = {'known': [], 'map': {}}
    if os.path.exists(ALIASES):
        with open(ALIASES, encoding='utf-8') as f:
            data = json.load(f)
    data.setdefault('map', {})[variant] = canonical_name
    with open(ALIASES, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    _CACHE = None
    return canonical_name


def _selftest():
    g = load()
    print(f'  gazetteer: {len(g["known"])} known artists '
          f'(benchmark at {os.path.basename(BENCHMARK)})')
    cases = [
        ('Vipul Goyal Unleashed', 'Vipul Goyal'),
        ('Vipul Goyal-Unleashed', 'Vipul Goyal'),
        ('Vipul Goyal', 'Vipul Goyal'),
        ('Rahul Dua Live', 'Rahul Dua'),
        ('Harsh Gujral', 'Harsh Gujral'),
        ('Someone Entirely New', 'Someone Entirely New'),
        # Two known artists in one string is a lineup — must NOT collapse to either.
        ('Zakir Khan And Kanan Gill', 'Zakir Khan And Kanan Gill'),
    ]
    ok = True
    for raw, want in cases:
        got, why = canonical(raw)
        good = got == want
        ok = ok and good
        print(f'  [{"ok " if good else "FAIL"}] {raw[:32]:34} -> {got[:26]:28} '
              f'{why or ""}')

    # A surname alone must resolve to nobody: many benchmark artists share one.
    singles = [n for n in ('Singh', 'Gupta', 'Khan') if canonical(n)[1] is not None]
    good = not singles
    ok = ok and good
    print(f'  [{"ok " if good else "FAIL"}] bare surnames match nobody '
          f'{"" if good else singles}')
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())
