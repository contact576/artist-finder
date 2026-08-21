"""Turning a messy event listing into an artist, a room and a role.

Everything downstream is arithmetic on these three fields, so this module is where the project
either works or quietly poisons itself. Three things are worth knowing before editing it.

ARTIST EXTRACTION IS THE HARD PART, AND IT IS ALLOWED TO FAIL.
Listings are free text written by whoever uploaded the event: "SAMAY RAINA - Still Alive Tour",
"Comedy Night ft. Bassi", "Kanan Gill: Yes I'm Fine", "Rajat presents The Weekend Show". There is
no schema. extract_artist() returns a CONFIDENCE alongside the name and anything below
MIN_TRUST goes to a review queue instead of the watchlist. A scout that silently invents artists
called "Comedy Night" is worse than one that admits it could not parse 20% of rows.

ROLE IS NOT DECORATION.
A solo headline in a 900-seat theatre and a five-name showcase in the same room are completely
different evidence about one performer. `role` and `lineup_size` carry that, and score.py
discounts lineup slots. Losing this distinction is the fastest way to rank a competent open-mic
regular above a real breakout.

BANDS, NOT SEAT COUNTS.
classify_venue() returns a band. The capacities in venues_india.json are mostly unverified and
the code must never depend on their precision — see the file's own header.
"""
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, 'data')

import gazetteer  # noqa: E402  (both need the sys.path line above)
import genres     # noqa: E402

# Below this, the parse is not trusted enough to create or update a watchlist entry.
MIN_TRUST = 0.55

_VENUES = None
_SOURCES = None


def load_venues():
    global _VENUES
    if _VENUES is None:
        with open(os.path.join(DATA, 'venues_india.json'), encoding='utf-8') as f:
            _VENUES = json.load(f)
    return _VENUES


def load_sources():
    global _SOURCES
    if _SOURCES is None:
        with open(os.path.join(DATA, 'sources.json'), encoding='utf-8') as f:
            _SOURCES = json.load(f)
    return _SOURCES


def slugify(name):
    """Byte-identical to Artist Tour Engine's artist.slugify — the bridge depends on it."""
    return re.sub(r'-+', '-', re.sub(r'[^a-z0-9]+', '-', str(name).strip().lower())).strip('-')


def _fold(s):
    """Strip accents and collapse whitespace. 'Café' and 'Cafe' must not be two venues."""
    s = unicodedata.normalize('NFKD', str(s))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s).strip()


# ---------------------------------------------------------------- artist extraction

# Words that are never part of a performer's name. Matched as whole tokens.
_NOISE = {
    'live', 'tour', 'show', 'shows', 'standup', 'stand', 'up', 'comedy', 'night', 'nights',
    'special', 'concert', 'gig', 'tickets', 'ticket', 'event', 'presents', 'presented',
    'featuring', 'ft', 'feat', 'with', 'the', 'a', 'an', 'in', 'at', 'on', 'by', 'and',
    'open', 'mic', 'openmic', 'improv', 'showcase', 'edition', 'season', 'vol', 'volume',
    'tickets', 'india', 'tour', 'world', 'new', 'material', 'trial', 'preview', 'encore',
}

# Phrases that mark the whole listing as NOT a single-artist show. These are real events but
# they are evidence about a venue's programming, not about any one performer.
_GENERIC_TITLES = (
    'open mic', 'open-mic', 'comedy night', 'comedy nights', 'improv night', 'jam',
    'showcase', 'new material', 'trial night', 'karaoke', 'quiz', 'workshop',
)

# Cities AND suburbs. Suburbs matter: "Vipul Goyal Unleashed Live in Borivali" left five tokens
# after cleaning and failed the person test, losing a benchmark artist entirely. Indian listings
# name the neighbourhood far more often than the city.
_CITY_TAIL = re.compile(
    r'\b(?:in|at)\s+(mumbai|delhi|new delhi|ncr|gurgaon|gurugram|noida|bengaluru|bangalore|'
    r'pune|hyderabad|chennai|kolkata|ahmedabad|chandigarh|jaipur|indore|kochi|goa|surat|'
    r'nagpur|lucknow|bhopal|coimbatore|thane|navi mumbai|borivali|andheri|bandra|juhu|powai|'
    r'dadar|malad|kandivali|vashi|whitefield|koramangala|indiranagar|jayanagar|hsr|'
    r'gachibowli|jubilee hills|banjara hills|salt lake|howrah|faridabad|saket|rohini|'
    r'electronic city|gomti nagar|viman nagar|kharadi|hinjewadi)\b.*$', re.I)

_SPLIT_LINEUP = re.compile(r'\s*(?:,|&|\+|\band\b|\bwith\b|\bx\b)\s*', re.I)

# A dash separating a show name from a performer needs whitespace on EITHER side, not both.
# Real listings write "Noor-e-Ishq- Harshdeep Kaur": the dash closes the show title and is
# followed by a space. Requiring spaces on both sides kept the whole string as the artist name.
# The lookbehind is what stops it cutting "Noor-e-Ishq" apart at its internal hyphens.
_DASH_SPLIT = re.compile(r'\s[-–—]\s|(?<=\S)[-–—]\s')

_CITY_ALIASES = {
    'bangalore': {'bengaluru'}, 'bengaluru': {'bangalore'},
    'delhi': {'new delhi', 'ncr'}, 'new delhi': {'delhi', 'ncr'},
    'gurgaon': {'gurugram'}, 'gurugram': {'gurgaon'},
}


def _listing_city_names(city):
    """Exact spellings that a source can append after a billed name.

    This is deliberately smaller than ``_CITY_TAIL``. The latter recognizes a city after
    ``in``/``at`` in free copy; this helper only removes an *exact trailing listing field*.
    It prevents an otherwise legitimate final word from silently disappearing.
    """
    primary = _fold(city or '').lower()
    return ({primary} | _CITY_ALIASES.get(primary, set())) if primary else set()


def _strip_listing_tail(fragment, city=None):
    """Remove only audited, terminal metadata from an explicit performer span."""
    f = _fold(fragment).strip()
    # District and similar routes append the listing city after a pipe. Do not treat any
    # arbitrary pipe suffix as metadata: it has to equal the source's city or a known alias.
    for known in sorted(_listing_city_names(city), key=len, reverse=True):
        f = re.sub(r'\s*\|\s*' + re.escape(known) + r'\s*$', '', f, flags=re.I)
    # KCC is a known venue brand, and Morning/Evening Show are schedule labels in the audited
    # primary listings. They are terminal-only so a real name containing these words survives.
    f = re.sub(r'\s*(?:[:|\-–—]\s*)?(?:kcc|morning show|evening show)\s*$', '', f,
               flags=re.I)
    return f.strip()

def _clean_fragment(frag, genre=None, city=None):
    """A candidate name, stripped of decoration. Returns '' if nothing survives.

    `genre` widens the noise list: 'bhajan' and 'sandhya' are furniture on a devotional bill,
    'b2b' and 'residency' on a club one, and neither belongs in anybody's name.
    """
    f = _strip_listing_tail(frag, city)
    f = re.sub(r'\(.*?\)|\[.*?\]', ' ', f)          # parenthetical asides
    f = _CITY_TAIL.sub(' ', f)                       # "... in Mumbai"
    f = re.sub(r'[\"“”‘’]', ' ', f)
    f = re.sub(r'\s*[|/•·]\s*', ' ', f)
    f = re.sub(r'\s+', ' ', f).strip(' -–—:;.,')
    if not f:
        return ''
    noise = _NOISE | genres.extra_noise(genre)
    toks = [t for t in re.split(r'\s+', f) if t]
    # Drop leading/trailing noise words but keep interior ones ("Singh", "Kaur" are fine).
    while toks and toks[0].lower().strip('.') in noise:
        toks.pop(0)
    while toks and toks[-1].lower().strip('.') in noise:
        toks.pop()
    return ' '.join(toks).strip()


def _looks_like_person(name):
    """A person's name: 1-4 tokens, mostly alphabetic, not a sentence."""
    if not name:
        return False
    toks = name.split()
    if not (1 <= len(toks) <= 4):
        return False
    if sum(len(t) for t in toks) < 3:
        return False
    alpha = sum(c.isalpha() or c.isspace() or c == "'" for c in name)
    return alpha / max(len(name), 1) > 0.85


def extract_artist(title, venue=None, genre=None, city=None):
    """Pull performer(s) out of a listing title.

    Returns dict(names=[...], role=..., lineup_size=int, confidence=float, reason=str).

    The confidence is the point of this function. A title like "Kanan Gill: Yes I'm Fine" is a
    clean solo headline and scores high; "Comedy Night" names nobody and scores zero, which
    routes it to review rather than into the data.
    """
    raw = _fold(title or '')
    low = raw.lower()
    if not raw:
        return dict(names=[], role='unknown', role_certain=False, lineup_size=0,
                    confidence=0.0, reason='empty title')

    # Vocabulary is genre-specific: 'open mic' marks a generic comedy bill, 'sundowner' a
    # generic club one. A single shared list mis-reads whichever genre it was not written for.
    #
    # MATCHED ON WORD BOUNDARIES, not as raw substrings. "jam" is in the comedy generic list and
    # sits inside "Dino James", "Jamshed" and "Jaipur" — plain `in` matching billed a solo
    # headliner as one name on a jam night.
    generic = any(re.search(r'\b' + re.escape(g) + r'\b', low)
                  for g in genres.generic_titles(genre))

    # "Promoter presents ARTIST" / "... ft. ARTIST" — the performer follows the marker.
    after = None
    # "by" matters far more than it looks, and was missing until real listings showed it up:
    # "Aise Kaise by Amit Tandon", "Love, Death & Ketchup by Varun Grover", "Unstoppable by
    # Dino James". Without it the SHOW NAME is read as the performer and the real artist is
    # lost entirely. Tested last so "hosted by" and "curated by" match first.
    for marker in (r'\bpresents\b', r'\bfeaturing\b', r'\bft\.?\b', r'\bfeat\.?\b',
                   r'\bhosted by\b', r'\bcurated by\b', r'\bby\b'):
        m = re.search(marker, low)
        if m:
            after = raw[m.end():]
            break
    # HighApe commonly appends the venue after the billed DJ:
    # "Ft DJ Senleo At Yeda Republic, Koramangala". The venue is not a second performer.
    if after is not None:
        after = re.split(r'\s+at\s+', after, maxsplit=1, flags=re.I)[0]
        after = _strip_listing_tail(after, city)


    if after is not None:
        seg, base_conf, why = after, 0.80, 'after presents/ft marker'
    elif ':' in raw:
        # "ARTIST: Show Name" is the dominant convention for a titled special.
        seg, base_conf, why = raw.split(':', 1)[0], 0.85, 'before colon'
    elif _DASH_SPLIT.search(raw):
        # THE ARTIST CAN BE ON EITHER SIDE OF A DASH, and real listings use both conventions:
        #   "SAMAY RAINA - Still Alive Tour"   artist first, show second
        #   "Noor-e-Ishq- Harshdeep Kaur"      show first, artist second
        # So try both and keep whichever half actually looks like a person. When both do, the
        # first wins, because artist-first is the commoner convention.
        left, right = _DASH_SPLIT.split(raw, 1)[0], _DASH_SPLIT.split(raw, 1)[-1]
        lok = _looks_like_person(_clean_fragment(left, genre, city))
        rok = _looks_like_person(_clean_fragment(right, genre, city))
        if lok or not rok:
            seg, base_conf, why = left, 0.70, 'before dash'
        else:
            seg, base_conf, why = right, 0.70, 'after dash'
    else:
        seg, base_conf, why = raw, 0.60, 'whole title'

    # ``Name & Friends`` is a billing formula, not two artists. Keep the named headliner and
    # never manufacture a generic artist called Friends.
    seg = re.sub(r'\s*(?:&|\band\b)\s+friends\s*$', '', seg, flags=re.I)
    parts = [p for p in _SPLIT_LINEUP.split(seg) if p.strip()]
    names, dropped = [], 0
    for p in parts:
        c = _clean_fragment(p, genre, city)
        if _looks_like_person(c):
            names.append(c.title())
        elif c:
            dropped += 1

    if not names:
        return dict(names=[], role='unknown', role_certain=False, lineup_size=0,
                    confidence=0.0, reason=f'no person-like name ({why})')

    # CONFIDENCE MEASURES THE NAME, NOT THE ROLE. These are different uncertainties and an
    # earlier version multiplied them together, which pushed "Comedy Night ft. <real person>"
    # below the trust floor and threw away a clean name over a billing ambiguity. Whether
    # somebody is headlining or doing twenty minutes is carried by `role`/`role_certain`;
    # whether we read their name correctly is carried by `confidence`.
    lineup = len(names)
    if lineup == 1:
        role = 'lineup' if generic else 'headline'
        role_certain = not generic
        conf = base_conf
    else:
        role, role_certain, conf = 'lineup', True, base_conf * 0.9
    if generic:
        why += ' + generic title (role uncertain)'

    # Retuned against 997 real listings, 2026-08-19.
    # The dropped-fragment penalty was 0.9, which put "Abish Mathew & His Many Talents Part 2"
    # at 0.54 against a 0.55 floor — a clean, correct name thrown away by one hundredth. A
    # discarded fragment beside a good name is normal (it is usually the show title), so the
    # penalty is now token.
    if dropped:
        conf *= 0.97
    # A SINGLE-TOKEN result is the real warning sign. "Soul India" reduces to "Soul", "Second
    # Nature" to nothing useful — these are event names being read as people. Genuine
    # one-word stage names exist but are rare enough that the trade is worth it.
    if len(names) == 1 and len(names[0].split()) == 1:
        conf *= 0.6
    if len(raw.split()) <= 1:
        conf *= 0.7

    return dict(names=names, role=role, role_certain=role_certain, lineup_size=lineup,
                confidence=round(min(conf, 0.98), 3), reason=why)


# ---------------------------------------------------------------- venue classification

def classify_venue(venue, city=None):
    """Venue string -> dict(band, rank, capacity_approx, matched, confidence).

    band is None when nothing matches. None means UNKNOWN and must never be treated as small;
    signals.py skips unknown rooms when measuring escalation rather than scoring them as zero.
    """
    v = load_venues()
    key = _fold(venue or '').lower()
    if not key:
        return dict(band=None, rank=None, capacity_approx=None, matched=None,
                    confidence=0.0, tba=False)

    # NOT-YET-ANNOUNCED is a different state from UNKNOWN-TO-US, and conflating them sends
    # somebody off to fix a venue map that has nothing wrong with it. "Venue To Be Announced"
    # appeared 11 times in the first crawl, and some sources put a bare city name where the
    # room should be. Both are the promoter withholding information, not our gap.
    tba = v.get('tba_markers') or {}
    if key in set(tba.get('exact', [])) or key in set(tba.get('bare_city', [])):
        return dict(band=None, rank=None, capacity_approx=None, matched='tba',
                    confidence=0.0, tba=True)

    # exact, then "venue, city" composite, then substring against known rooms
    cand = [key]
    if city:
        cand.append(f'{key}, {_fold(city).lower()}')
    for c in cand:
        if c in v['venues']:
            e = v['venues'][c]
            return dict(band=e['band'], rank=v['bands'][e['band']]['rank'],
                        capacity_approx=e.get('capacity_approx'), matched=c,
                        confidence=0.9 if e.get('confidence') != 'low' else 0.6, tba=False)
    for name, e in v['venues'].items():
        stem = name.split(',')[0].strip()
        if stem and stem in key:
            return dict(band=e['band'], rank=v['bands'][e['band']]['rank'],
                        capacity_approx=e.get('capacity_approx'), matched=name,
                        confidence=0.55, tba=False)

    for band, words in v['name_patterns']['ordered']:
        for w in words:
            if w in key:
                return dict(band=band, rank=v['bands'][band]['rank'],
                            capacity_approx=None, matched=f'pattern:{w}', confidence=0.35, tba=False)

    return dict(band=None, rank=None, capacity_approx=None, matched=None, confidence=0.0, tba=False)


def source_tier(source_key):
    s = load_sources()
    for e in s['sources']:
        if e['key'] == source_key:
            return e['tier'], s['tier_rank'][e['tier']]
    return None, None


def known_domains():
    return {e['domain'] for e in load_sources()['sources']}


# ---------------------------------------------------------------- normalisation

def _production_name(title):
    """A production's own name, tidied. The show IS the entity, so the title is the identity."""
    t = _fold(title)
    t = re.sub(r'\s*[-–—|]\s*(?:a play|the musical|live|tickets?)\s*$', '', t, flags=re.I)
    t = re.sub(r'\s*\(.*?\)\s*$', '', t)
    return t.strip(' -–—:;.,') or _fold(title)


def _structured_performer_names(value):
    """Clean a platform's explicit performer field without guessing a billing role.

    BookMyShow publishes schema.org performer values on many event details. That field is
    stronger identity evidence than free-text title parsing, but it does not say whether the
    person headlines or appears in a lineup. Identity confidence and role certainty therefore
    remain separate downstream.
    """
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        return []
    names, seen = [], set()
    for item in value:
        candidate = item.get('name') if isinstance(item, dict) else item
        if not isinstance(candidate, str):
            continue
        name = re.sub(r'\s+', ' ', candidate).strip(' -:;,.')
        # BMS occasionally appends a job label to the identity. Strip only this audited,
        # terminal qualifier; arbitrary parentheses can be part of a stage name.
        name = re.sub(r'\s*\((?:stand[ -]?up comedian|comedian|performer|artist)\)\s*$',
                      '', name, flags=re.I).strip()
        key = name.casefold()
        if (not name or len(name) > 120 or key in {'artist', 'artists', 'various artists',
                                                   'bookmyshow'} or key in seen):
            continue
        seen.add(key)
        names.append(name)
    return names


def normalise_event(raw, source_key, seen_at):
    """One scraped listing -> one canonical event row, or None if unusable.

    `raw` needs at minimum a title. url/venue/city/date/status/price/category are used when
    present, and `category` matters more than it looks — it is the PRIMARY genre signal, because
    a great many clean listings carry no genre word in their title at all.

    A platform_performers list, when present, supplies identity evidence directly from the
    platform's structured event record. It never supplies a ticket count and it never silently
    supplies a headline role.

    Rows below MIN_TRUST are returned with `trusted=False` so the caller can queue them for
    human review instead of dropping them silently — the parse failures are themselves data
    about which sources need a better adapter.

    TWO KINDS OF ROW COME OUT OF HERE. Most listings name a performer. Some name a PRODUCTION or
    a FESTIVAL — a play, a touring musical, a club brand — where there is no performer to
    extract and the show itself is the thing that rises or falls. Those skip name extraction
    entirely rather than being forced through it, which is what stops "Mughal-e-Azam: The
    Musical" entering the watchlist as an artist called Mughal.

    The output key is `entities`, not `artists`, precisely because it holds both kinds. A single
    field meaning two different things depending on a sibling field is the exact trap that
    `venue_exp` set in the settlement project; naming it honestly costs nothing.
    """
    title = (raw.get('title') or '').strip()
    if not title:
        return None

    g = genres.classify_genre(title, raw.get('venue'), raw.get('city'), raw.get('category'))
    etype = genres.entity_type_for(g['genre'], title, lineup_hint=bool(raw.get('lineup')))
    vb = classify_venue(raw.get('venue'), raw.get('city'))
    tier, trank = source_tier(source_key)

    if genres.extracts_artist(etype):
        ex = extract_artist(title, raw.get('venue'), g['genre'], raw.get('city'))
        structured_names = _structured_performer_names(raw.get('platform_performers'))
        identity_names = structured_names or ex['names']
        # Canonicalise against known artists so "Vipul Goyal Unleashed" and "Vipul Goyal" are
        # ONE entity. Fragmenting a performer splits the escalation trajectory this whole tool
        # is built to measure — three quiet acts instead of one rising one.
        entities, unkeyable = [], []
        for n in identity_names:
            canon, why = gazetteer.canonical(n)
            slug = slugify(canon)
            # An empty slug is not an identifier. Admitting it would merge every non-Latin
            # entity under the same dictionary key downstream, which invents a trajectory.
            if not slug:
                unkeyable.append(canon)
                continue
            e = dict(name=canon, slug=slug, kind='artist')
            if why:
                e['raw_name'], e['canonicalised'] = n, why
            entities.append(e)
        if structured_names:
            lineup = len(entities)
            if len(entities) > 1:
                role, role_certain = 'lineup', True
            elif len(entities) == 1:
                parsed_slugs = {
                    slugify(gazetteer.canonical(name)[0]) for name in ex['names']
                }
                if entities[0]['slug'] in parsed_slugs:
                    role, role_certain = ex['role'], ex['role_certain']
                else:
                    role, role_certain = 'unknown', False
            else:
                role, role_certain = 'unknown', False
            conf = 0.95 if entities else 0.0
            reason = ('platform structured performer field; billing role from title parser'
                      if role != 'unknown' else
                      'platform structured performer field; billing role unavailable')
        else:
            role, role_certain, lineup = ex['role'], ex['role_certain'], ex['lineup_size']
            conf, reason = ex['confidence'], ex['reason']
        if (source_key == 'bookmyshow' and not structured_names and entities
                and not all(gazetteer.exact_known(item['name']) for item in entities)):
            conf = min(conf, MIN_TRUST - 0.01)
            reason = (f'{reason}; BookMyShow title-only identity requires an exact gazetteer '
                      'match or explicit human review')
        if unkeyable:
            conf = 0.0
            reason = (f'{reason}; canonical slug unavailable for {", ".join(unkeyable)} — '
                      'held for review')
    else:
        # A production or festival is its own entity. Confidence is high because nothing is
        # being inferred — we are recording the title, not guessing a person out of it.
        nm = _production_name(title)
        slug = slugify(nm)
        entities = [dict(name=nm, slug=slug, kind=etype)] if slug else []
        role, role_certain, lineup = etype, True, 1
        conf, reason = 0.9, f'{etype}: title is the entity, no name extraction attempted'
        if not slug:
            conf = 0.0
            reason += '; canonical slug unavailable — held for review'

    return dict(
        source=source_key, source_tier=tier, source_tier_rank=trank,
        title=title, url=raw.get('url'),
        genre=g['genre'], genre_confidence=g['confidence'], genre_evidence=g['evidence'],
        entity_type=etype, entities=entities,
        role=role, role_certain=role_certain, lineup_size=lineup,
        parse_confidence=conf, parse_reason=reason,
        trusted=conf >= MIN_TRUST,
        venue=raw.get('venue'), city=raw.get('city'), category=raw.get('category'),
        venue_band=vb['band'], venue_rank=vb['rank'],
        venue_capacity_approx=vb['capacity_approx'], venue_match=vb['matched'],
        venue_confidence=vb['confidence'], venue_tba=vb.get('tba', False),
        date=raw.get('date'), status=(raw.get('status') or '').lower() or None,
        price_min=raw.get('price_min'), price_max=raw.get('price_max'),
        canonical_url=raw.get('canonical_url'), occurrence_key=raw.get('occurrence_key'),
        start_at=raw.get('start_at'), end_at=raw.get('end_at'),
        door_time=raw.get('door_time'),
        platform_event_type=raw.get('platform_event_type'),
        platform_performers=_structured_performer_names(raw.get('platform_performers')),
        seen_at=seen_at,
    )


# ---------------------------------------------------------------- self-test

def _selftest():
    cases = [
        ("Kanan Gill: Yes I'm Fine", ['Kanan Gill'], 'headline', True),
        ("SAMAY RAINA - Still Alive Tour", ['Samay Raina'], 'headline', True),
        ("Comedy Night ft. Anubhav Singh Bassi", ['Anubhav Singh Bassi'], 'lineup', True),
        ("Open Mic", [], 'unknown', False),
        ("Rahul Dua Live in Mumbai", ['Rahul Dua'], 'headline', True),
        ("Comedy Showcase ft. Aakash Gupta, Rahul Dua & Nishant Suri", None, 'lineup', True),

        # --- REGRESSION CASES, all taken from the first real crawl (2026-08-19, 997 listings).
        # Every one of these was parsed WRONG before the run, and each cost a real artist.
        ('Love, Death & Ketchup by Varun Grover', ['Varun Grover'], 'headline', True),
        ('Aise Kaise by Amit Tandon', ['Amit Tandon'], 'headline', True),
        ('Unstoppable by Dino James', ['Dino James'], 'headline', True),
        ('Noor-e-Ishq- Harshdeep Kaur', ['Harshdeep Kaur'], 'headline', True),
        # A KNOWN LIMITATION, pinned so it does not quietly get worse. With no separator between
        # the name and the show word, "Unleashed" survives: we get "Vipul Goyal Unleashed", not
        # "Vipul Goyal". The name is findable but the entity fragments from the clean spelling.
        # Fixing it properly needs an artist-name gazetteer, which the roster will eventually
        # supply. Until then this asserts we at least still capture the person.
        ('Vipul Goyal Unleashed Live in Borivali', ['Vipul Goyal Unleashed'], 'headline', True),
    ]
    ok = True
    for title, want_names, want_role, want_any in cases:
        got = extract_artist(title)
        hit = (got['names'] == want_names) if want_names is not None else bool(got['names'])
        role_ok = got['role'] == want_role
        any_ok = bool(got['names']) == want_any
        flag = 'ok ' if (hit and role_ok and any_ok) else 'FAIL'
        if flag == 'FAIL':
            ok = False
        print(f'  [{flag}] {title!r:55} -> {got["names"]} '
              f'role={got["role"]} conf={got["confidence"]}')

    dj = extract_artist('Midweek Madness Ft DJ Senleo At Yeda Republic, Koramangala',
                        genre='edm_club')
    dj_ok = dj['names'] == ['Senleo']
    ok = ok and dj_ok
    print(f'  [{"ok " if dj_ok else "FAIL"}] HighApe venue tail stripped'
          f' -> {dj["names"]}')

    edge_checks = [
        ('city tail stops after explicit by marker',
         extract_artist('NAMASTE TRUMP | A Comedy Show by Avinash Agarwal | Bengaluru',
                        genre='comedy', city='Bengaluru')['names'] == ['Avinash Agarwal']),
        ('KCC venue tail stops after ft marker',
         extract_artist('Trial Show For Baddie ft. Harpriya Bains: KCC',
                        genre='comedy', city='Bengaluru')['names'] == ['Harpriya Bains']),
        ('Morning Show schedule tail is not a name',
         extract_artist('Pure Veg Jokes by Saikiran - Morning Show',
                        genre='comedy', city='Bengaluru')['names'] == ['Saikiran']),
        ('Friends billing preserves only named headliner',
         extract_artist('Appurv Gupta & Friends : A Stand UP Comedy Show',
                        genre='comedy', city='Delhi')['names'] == ['Appurv Gupta']),
        ('exact pipe city alias is stripped',
         extract_artist('I Am Worth It Ft. Rajat Sood | Noida',
                        genre='comedy', city='Noida')['names'] == ['Rajat Sood']),
    ]
    for label, good in edge_checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and good
    # --- the trust floor is a separate question from what was extracted.
    # "Soul India" DOES yield a candidate ("Soul"), and that is fine — what matters is that the
    # confidence lands below MIN_TRUST so it goes to the review queue instead of the watchlist.
    # Asserting names==[] would test the wrong thing and break the moment the parser improved.
    print('\n  trust floor (MIN_TRUST = %.2f):' % MIN_TRUST)
    for title, want_trusted in [('Soul India', False),
                                ('Toast & Tunes at CinCin, Bandra', False),
                                ('Kanan Gill: Yes I\'m Fine', True),
                                ('Love, Death & Ketchup by Varun Grover', True)]:
        g = extract_artist(title)
        trusted = g['confidence'] >= MIN_TRUST
        good = trusted == want_trusted
        ok = ok and good
        print(f'  [{"ok " if good else "FAIL"}] {title[:42]:44} conf={g["confidence"]:.2f} '
              f'-> {"kept" if trusted else "review"}')

    # A non-Latin name may parse cleanly but still have no stable slug under the deliberately
    # ASCII-compatible bridge convention. It belongs in review, never under the empty key.
    unicode_artist = normalise_event(
        dict(title='ಯಕ್ಷ ಕನಸು - 2026', category='Comedy', url='https://fixture/unicode-artist',
             date='2026-09-01'), 'allevents', '2026-08-21')
    unicode_production = normalise_event(
        dict(title='মেঘের সঙ্গে চাঁদের দেখা', category='Theatre',
             url='https://fixture/unicode-production', date='2026-09-01'),
        'allevents', '2026-08-21')
    latin = normalise_event(
        dict(title='Kanan Gill: Yes I am Fine', category='Comedy', url='https://fixture/latin',
             date='2026-09-01'), 'allevents', '2026-08-21')
    just_go = normalise_event(
        dict(title='JUST GO WITH IT - An Improv Comedy Show', category='Comedy',
             url='https://fixture/just-go', date='2026-09-01'), 'allevents', '2026-08-21')
    symphonic = normalise_event(
        dict(title='Yuvan’s Walking Through The Rainbow - A Symphonic Experience',
             category='Concerts', url='https://fixture/symphonic', date='2026-09-01'),
        'allevents', '2026-08-21')
    slug_checks = [
        ('unkeyable non-Latin artist is review-only with no blank entity',
         not unicode_artist['trusted'] and not unicode_artist['entities'] and
         'canonical slug unavailable' in unicode_artist['parse_reason']),
        ('unkeyable non-Latin production is review-only with no blank entity',
         not unicode_production['trusted'] and not unicode_production['entities'] and
         'canonical slug unavailable' in unicode_production['parse_reason']),
        ('Latin canonical slug behavior remains accepted',
         latin['trusted'] and latin['entities'][0]['slug'] == 'kanan-gill'),
        ('improv format does not create an artist entity',
         just_go['entity_type'] == 'format' and just_go['entities'][0]['kind'] == 'format'),
        ('symphonic title does not create Symphonic Experience artist',
         symphonic['entity_type'] == 'production' and
         symphonic['entities'][0]['kind'] == 'production' and
         symphonic['entities'][0]['name'] != 'Symphonic Experience'),
    ]
    print('\n  canonical slug guard:')
    for label, good in slug_checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)

    structured_lineup = normalise_event(
        dict(title='BEST in STAND-UP @ Deccan', category='Comedy',
             platform_event_type='ComedyEvent',
             platform_performers=['Ankit Arora', 'Advit Mohunta', 'Ankit Arora'],
             url='https://in.bookmyshow.com/events/fixture/ET1', date='2026-09-01'),
        'bookmyshow', '2026-08-21')
    structured_solo = normalise_event(
        dict(title='A Night of Laughter', category='Comedy',
             platform_event_type='ComedyEvent',
             platform_performers=['Krishna Pandey (Stand Up Comedian)'],
             url='https://in.bookmyshow.com/events/fixture/ET2', date='2026-09-01'),
        'bookmyshow', '2026-08-21')
    structured_production = normalise_event(
        dict(title='Mughal-e-Azam: The Musical', category='Theatre',
             platform_performers=['Stage Actor'],
             url='https://in.bookmyshow.com/events/fixture/ET3', date='2026-09-01'),
        'bookmyshow', '2026-08-21')
    title_only_bms = normalise_event(
        dict(title='Golden Hits', category='Music',
             url='https://in.bookmyshow.com/events/fixture/ET4', date='2026-09-01'),
        'bookmyshow', '2026-08-21')
    structured_checks = [
        ('structured performer field creates a trusted deduplicated lineup',
         structured_lineup['trusted'] and structured_lineup['role'] == 'lineup' and
         [item['name'] for item in structured_lineup['entities']] ==
         ['Ankit Arora', 'Advit Mohunta']),
        ('structured solo identity is trusted while an unavailable role stays unknown',
         structured_solo['trusted'] and structured_solo['role'] == 'unknown' and
         not structured_solo['role_certain'] and
         structured_solo['entities'][0]['name'] == 'Krishna Pandey'),
        ('a production ignores performer credits as candidate identities',
         structured_production['entity_type'] == 'production' and
         structured_production['entities'][0]['kind'] == 'production'),
        ('unattested BookMyShow title-only identity is routed to review',
         not title_only_bms['trusted'] and
         'title-only identity requires' in title_only_bms['parse_reason']),
    ]
    print('\n  platform structured performers:')
    for label, good in structured_checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and bool(good)

    print('\n  venue classification:')
    for v, c in [('The Habitat', 'Mumbai'), ('Shanmukhananda Hall', 'Mumbai'),
                 ('Some Random Auditorium', 'Surat'), ('Blue Frog Basement', 'Pune'),
                 ('Totally Unknown Place', None)]:
        r = classify_venue(v, c)
        print(f'    {v!r:28} -> band={r["band"]} rank={r["rank"]} '
              f'via={r["matched"]} conf={r["confidence"]}')

    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())
