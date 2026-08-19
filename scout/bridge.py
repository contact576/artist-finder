"""Hand a shortlisted artist to the calibrated engine — without pretending to know more.

THIS FILE PRODUCES NO FORECAST. It writes an Artist Tour Engine dossier and stops. The North
American number comes from that engine, which is back-tested (median actual/predicted 1.00x,
show-level median APE 25.3%, bands re-derived by its own backtest.py). Nothing measured in
India is allowed to shortcut that, for the reason the engine itself documents: follower counts
and Indian activity measure reach IN INDIA, the tickets are sold to the NORTH AMERICAN
DIASPORA, and the two come apart by a factor of 15 across its roster.

So the scout's job at this boundary is narrow and specific: create the dossier, record the
Indian evidence as NOTES, and populate `search` ONLY from real US/CA Keyword Planner volumes —
never from Indian proxies. That distinction is the whole point. A measured North American search
figure is exactly what the engine's calibrated path runs on (74 tickets per 1k US, 134 per 1k
Canada); an Indian room size dressed up as one would launder a guess into the only part of the
system that is honest about its error bars.

`actuals` is never written here at all. A real prior result comes from the client, not a crawl.

MERGE, NEVER CLOBBER. A dossier may already contain hand-researched search volume or a real
prior result — the most valuable things in the system. This module only fills nulls and appends
notes. It will not overwrite a populated field, and --force does not exist on purpose.
"""
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import demand  # noqa: E402
import model   # noqa: E402

BASE = model.BASE
ENGINE = os.path.normpath(os.path.join(BASE, '..', 'Artist Tour Engine'))
DOSSIERS = os.path.join(ENGINE, 'artists')

# scout genre -> the engine's fixed GENRES tuple ('indian-comedy', 'indian-music',
# 'western-comedy', 'other'). Anything that is not comedy or music becomes 'other', which the
# engine treats as E4 — outside its 30-artist benchmark population — and says so on the output.
GENRE_MAP = {
    'comedy': 'indian-comedy',
    'music_mainstream': 'indian-music', 'music_indie': 'indian-music',
    'music_classical': 'indian-music', 'devotional': 'indian-music',
    'spoken_word': 'other', 'magic_variety': 'other', 'theatre': 'other',
    'edm_club': 'indian-music', 'sport': 'other', 'unknown': 'other',
}


def engine_available():
    return os.path.isdir(DOSSIERS)


def _blank(name, genre):
    """Mirror of Artist Tour Engine artist.blank(). Imported when the engine is present so the
    shape can never drift; this copy is the fallback when it is not."""
    return dict(
        name=str(name).strip(), slug=model.slugify(name), resolved_name=None, genre=genre,
        researched_at=None,
        social=dict(instagram_handle=None, instagram_followers=None, youtube_channel=None,
                    youtube_subs=None, youtube_recent_views=None, source=None),
        search=dict(us_monthly=None, ca_monthly=None, aliases=[], qualified_us_monthly=None,
                    comedian_share=None, source=None),
        actuals=[], plan={},
        assumptions=dict(price_usd=None, price_cad=None, currency_note=None),
        notes=[])


def new_dossier(name, genre='indian-comedy'):
    if engine_available():
        sys.path.insert(0, os.path.join(ENGINE, 'engine'))
        try:
            import artist as eng_artist  # noqa: E402
            return eng_artist.blank(name, genre)
        except Exception:
            pass
    return _blank(name, genre)


def evidence_notes(scored, sig, asof=None):
    """The Indian evidence, as prose a researcher can act on. Deliberately verbose about what
    it is NOT: none of this is a ticket count, and none of it is diaspora demand."""
    asof = asof or dt.date.today().isoformat()
    L, T = sig['level'], sig['trajectory']
    n = [f'[scout {asof}] India signal — quadrant {scored["quadrant"]} '
         f'(stature {scored["stature"]["value"]}, momentum {scored["momentum"]["value"]}, '
         f'{int((scored["momentum"]["coverage"] or 0) * 100)}% of signal weight observable).',
         f'[scout {asof}] Rooms: best repeated band {L["best_room_band"]} across '
         f'{L["n_cities"]} Indian cities; {L["n_shows"]} listings seen, '
         f'{L["rooms_unknown"]} with an unclassified room.']
    for k, s in T.items():
        if s.get('observable') and s.get('value') not in (None, 0, False):
            n.append(f'[scout {asof}] {k}: {s["value"]} — {s["note"]}')
    fetched = demand.for_engine(scored['slug'])
    share = fetched.get('comedian_share')
    if share is not None and share < demand.CONTAMINATION_FLOOR:
        n.append(f'[scout {asof}] CONTAMINATION WARNING: only {share:.0%} of the bare-name '
                 f'search volume is qualified, so most of it belongs to somebody else. Resolve '
                 f'the alias before trusting us_monthly — measured comparables converted at '
                 f'32-39 tickets per 1k against ~74 for clean names.')
    xs = demand.export_signal(scored['slug'])
    if xs['observable']:
        n.append(f'[scout {asof}] Diaspora demand signal {xs["value"]}/100 '
                 f'({int((xs["coverage"] or 0) * 100)}% observable) — '
                 + '; '.join(p['note'] for p in xs['parts'] if p['observable']))
    n.append(f'[scout {asof}] NOT a ticket count. Indian platforms publish none. These are '
             f'room size, repeat bookings and sell-through flags: proxies for what a promoter '
             f'believes, not for what the diaspora will buy. Do not treat as a NA input.')
    return n


def upsert(scored, sig, category=None, asof=None):
    """Create or top up the engine dossier. Returns (path, action, changed_fields).

    A production or festival is NOT written. The engine forecasts an artist's draw; a touring
    play is a different business with a different cost base, and handing it a production would
    produce a confident number about the wrong thing.
    """
    if not engine_available():
        return None, 'engine-not-found', []
    if scored.get('kind') not in (None, 'artist', 'dj_night'):
        return None, f'skipped ({scored.get("kind")} — not an artist)', []
    os.makedirs(DOSSIERS, exist_ok=True)
    slug = scored['slug']
    path = os.path.join(DOSSIERS, f'{slug}.json')
    genre = GENRE_MAP.get(category or scored.get('genre'), 'other')

    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        action, changed = 'updated', []
    else:
        d = new_dossier(scored['name'], genre)
        action, changed = 'created', ['(new dossier)']

    # Fill only what is empty. Never touch search/actuals — those are researched, not scraped.
    if not d.get('genre'):
        d['genre'] = genre
        changed.append('genre')
    if not d.get('resolved_name'):
        pass  # only the benchmark can resolve this; leaving it null is correct

    # THE SEARCH VOLUMES — the one field that turns an unresearched artist into an E3 forecast.
    # Fills nulls ONLY. A hand-researched figure, or a real prior result, always wins: those are
    # the most valuable things in the system and a scraped number must never overwrite them.
    eng = demand.for_engine(slug)
    sd = d.setdefault('search', {})
    for k in ('us_monthly', 'ca_monthly'):
        if sd.get(k) in (None, 0) and eng.get(k):
            sd[k] = eng[k]
            changed.append(f'search.{k}')
    if sd.get('comedian_share') is None and eng.get('comedian_share') is not None:
        sd['comedian_share'] = eng['comedian_share']
        changed.append('search.comedian_share')
    if not sd.get('source') and (eng.get('us_monthly') or eng.get('ca_monthly')):
        sd['source'] = f"artist-scout via {eng.get('source') or 'keyword planner'} ({asof})"
        changed.append('search.source')
    for al in (eng.get('aliases') or []):
        if al not in (sd.setdefault('aliases', [])):
            sd['aliases'].append(al)
            changed.append('search.aliases')

    existing = set(d.get('notes') or [])
    fresh = [x for x in evidence_notes(scored, sig, asof) if x not in existing]
    if fresh:
        d.setdefault('notes', []).extend(fresh)
        changed.append(f'notes(+{len(fresh)})')

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    return path, action, changed


def screen_command(slug, shows=10):
    """The exact command to run next. Printed rather than executed — running the engine is a
    decision with a cost, and the operator makes it."""
    py = r'"C:/Users/dapat/AppData/Local/Programs/Python/Python312/python.exe"'
    return (f'cd "{ENGINE}/engine" && PYTHONIOENCODING=utf-8 {py} '
            f'screen.py "{slug}" --shows {shows}')


def _selftest():
    print(f'  engine at   : {ENGINE}')
    print(f'  dossiers dir: {DOSSIERS}')
    print(f'  available   : {engine_available()}')
    d = new_dossier('Test Scout Artist', 'indian-comedy')
    keys = sorted(d.keys())
    ok = {'name', 'slug', 'genre', 'social', 'search', 'actuals', 'notes'} <= set(keys)
    print(f'  blank dossier keys: {keys}')
    print(f'  search/actuals left empty: {d["search"]["us_monthly"] is None and d["actuals"] == []}')

    fake_scored = dict(slug='test-scout-artist', name='Test Scout Artist', quadrant='RISING',
                       stature=dict(value=60), momentum=dict(value=70, coverage=0.8))
    fake_sig = dict(level=dict(best_room_band='theatre_mid', n_cities=3, n_shows=7,
                               rooms_unknown=1),
                    trajectory=dict(room_escalation=dict(observable=True, value=2,
                                                         note='rank 2 -> 4'),
                                    added_nights=dict(observable=False, value=None, note='x')))
    for line in evidence_notes(fake_scored, fake_sig, '2026-08-19'):
        print(f'    note: {line[:100]}')
    print(f'\n  next command would be:\n    {screen_command("test-scout-artist")[:110]}...')
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())
