"""Genre — because a comedy-shaped parser reads the rest of live entertainment as noise.

THREE THINGS BREAK OUTSIDE STAND-UP, AND THIS MODULE FIXES EACH.

1. THE VOCABULARY IS WRONG. "Open mic" and "showcase" mark a generic comedy bill; "night",
   "sundowner" and "presents" mark a generic club bill; a devotional listing is titled by the
   ritual ("Hanuman Chalisa Path") rather than the singer. One shared word-list mis-parses all
   of them, so each genre carries its own.

2. NOT EVERY EVENT HAS AN ARTIST. `Mughal-e-Azam: The Musical` is the product; the cast is
   incidental and changes. `Sunburn Arena` is a brand. Forcing those through name extraction
   invents performers called "Mughal" and "Sunburn". `entity_type` is decided BEFORE extraction
   and a production or a festival simply skips it.

3. A ROOM MEANS DIFFERENT THINGS. Climbing out of a 200-seat room is the whole story in comedy.
   For a club night, 200 people is the format and always will be. Each genre gets its own
   stature curve, so "big for what they do" is measured against the right yardstick.

WHAT THIS MODULE DELIBERATELY DOES NOT DO.
It does not modulate MOMENTUM. A room getting bigger is a room getting bigger, whatever the
genre, and there is no data yet to justify saying an escalation counts for less in one category
than another. Genre changes what "big" means (stature) and what is worth signing (export
weight); rate-of-change is left alone. If that turns out to be wrong, backtest.py is where it
should be discovered, not asserted here.

EXPORT WEIGHT IS A BUSINESS FACT, NOT A JUDGEMENT ON THE ART. It answers one narrow question:
would a North American diaspora audience buy a ticket to this? A Marathi play can be excellent
and still not tour; a bhajan act with no Indian press can fill a temple hall in Brampton.
"""
import re
import unicodedata

# Fallback when nothing matches. NEVER defaults to comedy — an unclassified event inheriting
# stand-up's thresholds is exactly the silent-wrong-answer this project is built to avoid.
UNKNOWN = 'unknown'

GENRES = (
    'comedy',
    'music_mainstream',   # Bollywood playback, Punjabi, mainstream pop — the big diaspora draw
    'music_unspecified',  # the platform said 'music' and nothing more
    'music_indie',        # indie, rock, fusion, hip-hop
    'music_classical',    # Hindustani, Carnatic, ghazal, qawwali
    'devotional',         # bhajan, kirtan, satsang, jagran
    'spoken_word',        # poetry, kavi sammelan, storytelling, mushaira
    'magic_variety',      # magicians, illusionists, circus, variety
    'theatre',            # plays and musicals
    'edm_club',           # DJ nights, club events, festivals
    'sport',
    UNKNOWN,
)

# How well each genre travels to a North American diaspora audience. Used to weight the ranking
# so that a fast-rising Marathi play does not outrank a comedian who could actually be booked.
# Nothing is hidden by this — low-weight genres are still tracked and still reported.
EXPORT_WEIGHT = {
    'comedy':           1.00,
    'music_mainstream': 1.00,
    'music_unspecified': 0.75,  # split the difference; we know it is music, not what kind
    'devotional':       0.80,   # temple and community halls book these constantly
    'spoken_word':      0.70,   # mushaira and kavi sammelan tour the diaspora well
    'music_classical':  0.60,   # small but real and loyal audience
    'music_indie':      0.50,
    'magic_variety':    0.50,   # travels, but rarely on the artist's own name
    'theatre':          0.40,   # touring a full production is a different business
    'edm_club':         0.20,   # a DJ plays a club, they do not "tour" in this sense
    'sport':            0.10,
    UNKNOWN:            None,   # held out of the ranking until classified
}

# Stature curve per genre: room band rank -> points out of 100.
# The shape differs because the ladder differs. A mainstream music act starts in rooms a comedian
# would consider a career peak, so its curve is flatter at the bottom; a classical or spoken-word
# act playing a 350-seat hall is already respectable, so its curve rises earlier.
ROOM_SCALE = {
    'comedy':           {1: 5,  2: 15, 3: 30, 4: 45, 5: 70, 6: 95},
    'music_mainstream': {1: 2,  2: 8,  3: 18, 4: 32, 5: 58, 6: 90},
    'music_unspecified': {1: 4, 2: 13, 3: 27, 4: 44, 5: 68, 6: 93},
    'music_indie':      {1: 6,  2: 18, 3: 35, 4: 55, 5: 78, 6: 96},
    'music_classical':  {1: 8,  2: 24, 3: 45, 4: 65, 5: 85, 6: 97},
    'devotional':       {1: 4,  2: 12, 3: 26, 4: 44, 5: 68, 6: 93},
    'spoken_word':      {1: 8,  2: 24, 3: 45, 4: 66, 5: 86, 6: 97},
    'magic_variety':    {1: 6,  2: 18, 3: 36, 4: 56, 5: 79, 6: 96},
    'theatre':          {1: 8,  2: 22, 3: 42, 4: 62, 5: 83, 6: 96},
    'edm_club':         {1: 3,  2: 10, 3: 25, 4: 46, 5: 73, 6: 95},
    'sport':            {1: 2,  2: 6,  3: 15, 4: 30, 5: 55, 6: 90},
    UNKNOWN:            {1: 5,  2: 15, 3: 30, 4: 45, 5: 70, 6: 95},   # comedy-shaped, but unused
}

# Titles that name an EVENT rather than a performer, per genre. Presence does not discard the
# listing — it marks the billing as uncertain so `role` can say "lineup" instead of "headline".
GENERIC_TITLES = {
    'comedy': ('open mic', 'open-mic', 'comedy night', 'comedy nights', 'improv night', 'jam',
               'showcase', 'new material', 'trial night', 'roast battle'),
    'music_mainstream': ('live in concert', 'music festival', 'unplugged night', 'tribute night',
                         'bollywood night', 'retro night'),
    'music_indie': ('gig night', 'band night', 'open jam', 'music festival', 'indie night'),
    'music_unspecified': ('live in concert', 'music night', 'unplugged night'),
    'music_classical': ('baithak', 'sabha', 'music conference', 'classical evening',
                        'sangeet sammelan'),
    'devotional': ('bhajan sandhya', 'jagran', 'satsang', 'kirtan', 'chalisa path', 'aarti',
                   'mata ki chowki', 'sundarkand'),
    'spoken_word': ('mushaira', 'kavi sammelan', 'poetry night', 'open mic', 'storytelling night'),
    'magic_variety': ('magic show', 'variety night', 'circus'),
    'theatre': ('play', 'the musical', 'natak', 'drama festival', 'theatre festival'),
    'edm_club': ('night', 'sundowner', 'ladies night', 'new year bash', 'nye', 'brunch',
                 'after party', 'techno night', 'bollywood night'),
    'sport': ('match', 'tournament', 'league', 'marathon'),
    UNKNOWN: (),
}

# Words that are never part of a performer's name, per genre, on top of the shared set.
EXTRA_NOISE = {
    'music_mainstream': {'concert', 'unplugged', 'symphony', 'orchestra', 'tribute', 'medley'},
    'music_indie': {'band', 'gig', 'collective', 'project', 'sessions'},
    'music_unspecified': {'concert', 'unplugged', 'symphony', 'orchestra', 'medley'},
    'music_classical': {'baithak', 'sabha', 'sammelan', 'gharana', 'jugalbandi', 'recital'},
    'devotional': {'bhajan', 'sandhya', 'jagran', 'satsang', 'kirtan', 'chalisa', 'path',
                   'aarti', 'chowki', 'sundarkand', 'katha', 'mandir', 'temple'},
    'spoken_word': {'mushaira', 'kavi', 'sammelan', 'poetry', 'shayari', 'storytelling'},
    'magic_variety': {'magic', 'magician', 'illusion', 'illusionist', 'circus', 'variety'},
    'theatre': {'play', 'musical', 'natak', 'drama', 'act', 'production'},
    'edm_club': {'dj', 'night', 'sundowner', 'party', 'bash', 'brunch', 'set', 'b2b',
                 'presents', 'residency'},
    'sport': {'match', 'vs', 'tournament', 'league', 'cup', 'marathon'},
}

# ------------------------------------------------------------------ detection

def _fold(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s).strip().lower()

def _contains_term(text, term):
    """Word-bounded phrase match; raga must not match venue Pragati Maidan."""
    term = _fold(term)
    if not term:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", _fold(text)) is not None


# Ordered most-specific first; the FIRST hit wins. Order is the whole design here — "night"
# appears in club listings and devotional ones alike, so devotional's ritual words are tested
# before the generic club vocabulary gets a chance to claim them.
_RULES = [
    ('devotional', ('bhajan', 'kirtan', 'satsang', 'jagran', 'chalisa', 'sundarkand', 'aarti',
                    'mata ki chowki', 'bhakti', 'katha', 'devotional', 'shrimad', 'bhagwat',
                    'naam', 'gurbani', 'shabad', 'qawwali night')),
    ('spoken_word', ('mushaira', 'kavi sammelan', 'poetry', 'shayari', 'storytelling', 'spoken word',
                     'kavita', 'open mic poetry')),
    ('music_classical', ('hindustani', 'carnatic', 'classical', 'ghazal', 'qawwali', 'thumri',
                         'raag', 'raga', 'sitar', 'tabla', 'santoor', 'sarod', 'baithak',
                         'jugalbandi', 'bharatanatyam', 'kathak', 'odissi')),
    ('magic_variety', ('magic', 'magician', 'illusion', 'illusionist', 'mentalist', 'circus',
                       'acrobat', 'juggler', 'variety show')),
    ('theatre', ('play', 'natak', 'the musical', 'drama', 'theatre', 'theater', 'rangmanch',
                 'ekaanki', 'prayog')),
    ('edm_club', ('dj ', ' dj', 'edm', 'techno', 'house music', 'trance', 'sundowner',
                  'nightclub', 'club night', 'rave', 'b2b', 'afterparty', 'after party',
                  'sunburn', 'nye', 'new year bash', 'ladies night')),
    ('comedy', ('comedy', 'stand up', 'standup', 'stand-up', 'improv', 'roast', 'open mic',
                'humour', 'humor', 'comic')),
    ('music_mainstream', ('bollywood', 'playback', 'punjabi', 'sufi night', 'filmi',
                          'live band tour', 'symphony')),
    ('music_indie', ('indie', 'band', 'rock', 'metal', 'hip hop', 'hip-hop', 'rap', 'fusion',
                     'jazz', 'blues', 'folk', 'acoustic', 'gig')),
    ('sport', ('vs ', ' vs', 'match', 'tournament', 'premier league', 'marathon', 'championship')),
]

# What a platform's own category label maps to. Trusted ABOVE title keywords — the platform
# knows what it filed the event under, and a title is just marketing copy.
CATEGORY_MAP = {
    'comedy': 'comedy', 'stand-up comedy': 'comedy', 'standup': 'comedy',
    'music': 'music_unspecified', 'concerts': 'music_mainstream', 'gigs': 'music_indie',
    'theatre': 'theatre', 'plays': 'theatre', 'drama': 'theatre',
    'nightlife': 'edm_club', 'parties': 'edm_club', 'clubbing': 'edm_club',
    'devotional': 'devotional', 'spiritual': 'devotional',
    'workshop': UNKNOWN, 'workshops': UNKNOWN,
    'sport': 'sport', 'sports': 'sport',
}


# Words that sound like a genre and are not. Every kind of performer uses them: comedians play
# "live in concert", singers do "world tour", magicians do "unplugged". An earlier version had
# these under music_mainstream and duly classified Zakir Khan, a stand-up comedian, as a
# mainstream musician. They are matched against and explicitly ignored.
NEUTRAL_TOUR_WORDS = ('live in concert', 'world tour', 'india tour', 'unplugged', 'tour',
                      'concert', 'live', 'show', 'on stage', 'experience')


def classify_genre(title, venue=None, city=None, source_category=None):
    """-> dict(genre, confidence, evidence).

    THE PLATFORM'S OWN CATEGORY IS THE PRIMARY SIGNAL, and title keywords are only a fallback.
    That ordering is not a preference, it is forced by the data: a large share of clean listings
    — "Kanan Gill: Yes I'm Fine", "Zakir Khan Live in Concert" — contain no genre word at all,
    and the words they do contain are shared across every kind of performer. The platform, by
    contrast, had to file the event under something.

    So the crawler MUST capture the category it found the listing under. Where it did not, this
    returns `unknown`, which is held out of the ranking and surfaced for review. That is the
    honest outcome: "Zakir Khan Live in Concert" really is ambiguous on its face, and guessing
    would eventually file a tabla maestro as a stand-up.
    """
    if source_category:
        key = _fold(source_category)
        if key in CATEGORY_MAP and CATEGORY_MAP[key] != UNKNOWN:
            return dict(genre=CATEGORY_MAP[key], confidence=0.85,
                        evidence=f'platform category "{source_category}"')

    title_text, venue_text = _fold(title), _fold(venue)
    for genre, words in _RULES:
        for w in words:
            in_title = _contains_term(title_text, w)
            in_venue = _contains_term(venue_text, w)
            if in_title or in_venue:
                where = 'title' if in_title else 'venue'
                return dict(genre=genre, confidence=0.7 if in_title else 0.45,
                            evidence=f'keyword "{w.strip()}" in {where}')
    return dict(genre=UNKNOWN, confidence=0.0, evidence='no genre keyword or category matched')


# ------------------------------------------------------------------ entity type

# Brands and franchises that are events, not people. A festival sells on its own name and the
# lineup rotates, so attributing it to a "performer" is meaningless.
_FESTIVAL_MARKERS = ('sunburn', 'nh7', 'weekender', 'lollapalooza', 'echoes of earth',
                     'ziro festival', 'magnetic fields', 'supersonic', 'festival', 'fest ',
                     'carnival', 'expo')

_PRODUCTION_MARKERS = ('the musical', ' - a play', 'a play by', 'natak', 'presents the play',
                       'symphonic experience')

_NON_PERFORMANCE_MARKERS = ('fan event', 'fan screening', 'listening party')


# These are event formats when no person is explicitly billed. They are intentionally phrases,
# not a blanket ``show`` rule: "Kanan Gill: Yes I'm Fine" remains an artist-led special.
_UNNAMED_FORMAT = re.compile(
    r'\b(?:lineup|line-up|showcase|improv(?:\s+comedy)?\s+show|'
    r'comedy\s+at\b|tribute\s+to\b)', re.I)


def _has_explicit_performer(title):
    """Conservative escape hatch before an otherwise generic format marker.

    This is classification, not an identity attestation: model.py still has to parse the name
    and can still route the row to review. It merely avoids throwing away explicit ``ft``/``by``
    bills, ``Name Live``, and the common ``Name & Friends`` billing form.
    """
    t = _fold(title)
    if re.search(r'\b(?:ft\.?|feat\.?|featuring|by)\s+(?:dj\s+)?[a-z]', t):
        return True
    if re.match(r"^[a-z][a-z .'-]{1,55}\s+live\b", t):
        return True
    return re.match(r"^[a-z][a-z .'-]{1,55}\s*&\s*friends\b", t) is not None

def entity_type_for(genre, title, lineup_hint=None):
    """artist | production | dj_night | festival.

    Decided BEFORE name extraction. A production or a festival never goes through the parser,
    which is the whole point — it is what stops "Mughal-e-Azam: The Musical" being recorded as
    an artist named Mughal.

    Genre alone is not enough. Theatre is usually a production but "Naseeruddin Shah in ..." is
    artist-led; club nights are usually the venue's brand but a named DJ headlining is an artist.
    So markers in the title override the genre default.
    """
    t = _fold(title)
    if any(_contains_term(t, m) for m in _NON_PERFORMANCE_MARKERS):
        return 'fan_event'
    if any(m in t for m in _FESTIVAL_MARKERS):
        return 'festival'
    if any(m in t for m in _PRODUCTION_MARKERS):
        return 'production'
    if _UNNAMED_FORMAT.search(t) and not _has_explicit_performer(title):
        return 'format'
    if genre == 'theatre':
        # Artist-led theatre: "<Name> in <Play>" / "<Name>'s <Play>".
        return 'artist' if re.search(r'\b(?:in|presents|starring)\b', t) and lineup_hint \
               else 'production'
    if genre == 'edm_club':
        return 'dj_night'
    if genre == 'sport':
        return 'production'
    return 'artist'


def extracts_artist(entity_type):
    """Only these entity types get run through name extraction."""
    return entity_type in ('artist', 'dj_night')


def room_scale(genre):
    return ROOM_SCALE.get(genre or UNKNOWN, ROOM_SCALE[UNKNOWN])


def export_weight(genre):
    return EXPORT_WEIGHT.get(genre or UNKNOWN, None)


def generic_titles(genre):
    """This genre's event-name phrases, plus comedy's, since bills mix."""
    return tuple(GENERIC_TITLES.get(genre, ())) + tuple(GENERIC_TITLES['comedy'])


def extra_noise(genre):
    return EXTRA_NOISE.get(genre, set())


def _selftest():
    cases = [
        # --- title alone carries the genre
        ('Hanuman Chalisa Path with Hansraj Raghuwanshi', None, None, 'devotional', 'artist'),
        ('Mughal-e-Azam: The Musical', 'NCPA', None, 'theatre', 'production'),
        ('Sunburn Arena ft. Alan Walker', None, None, 'edm_club', 'festival'),
        ('DJ Chetas Live', 'Hard Rock Cafe', None, 'edm_club', 'dj_night'),
        ('Ustad Amjad Ali Khan — Sarod Recital', None, None, 'music_classical', 'artist'),
        ('The Great Indian Magic Show', None, None, 'magic_variety', 'artist'),
        ('Kavi Sammelan 2027', None, None, 'spoken_word', 'artist'),

        ('Taylor Swift: Fan Event', None, None, UNKNOWN, 'fan_event'),
        ('Health Expo', 'Pragati Maidan', None, UNKNOWN, 'festival'),
        # --- title alone is genuinely ambiguous: these MUST come back unknown, not guessed.
        # "Zakir Khan" is a stand-up comedian and "Zakir Hussain" a tabla maestro; "live in
        # concert" is used by both. An earlier build guessed music here and was wrong.
        ("Kanan Gill: Yes I'm Fine", None, None, UNKNOWN, 'artist'),
        ('Zakir Khan Live in Concert', None, None, UNKNOWN, 'artist'),
        ('Some Event Nobody Named Well', None, None, UNKNOWN, 'artist'),

        # --- the same ambiguous titles, resolved by the platform's own category
        ("Kanan Gill: Yes I'm Fine", None, 'Comedy', 'comedy', 'artist'),
        ('Zakir Khan Live in Concert', None, 'Stand-Up Comedy', 'comedy', 'artist'),
        ('Arijit Singh Live in Concert', None, 'Concerts', 'music_mainstream', 'artist'),
        ('JUST GO WITH IT - An Improv Comedy Show', None, 'Comedy', 'comedy', 'format'),
        ('Yuvan’s Walking Through The Rainbow - A Symphonic Experience', None, 'Concerts', 'music_mainstream', 'production'),
        ('Comedy Showcase ft. Aakash Gupta', None, 'Comedy', 'comedy', 'artist'),
        ('Kanan Gill Live', None, 'Comedy', 'comedy', 'artist'),
    ]
    ok = True
    print('  genre + entity classification')
    for title, venue, cat, want_g, want_e in cases:
        g = classify_genre(title, venue, None, cat)
        e = entity_type_for(g['genre'], title)
        good = g['genre'] == want_g and e == want_e
        ok = ok and good
        tag = f'[{cat}]' if cat else ''
        print(f'  [{"ok " if good else "FAIL"}] {(title[:38] + " " + tag)[:46]:46} -> '
              f'{g["genre"]:17} {e:11} conf={g["confidence"]}  ({g["evidence"][:32]})')

    print('\n  export weights (higher = travels better)')
    for gname in sorted(EXPORT_WEIGHT, key=lambda k: -(EXPORT_WEIGHT[k] or -1)):
        w = EXPORT_WEIGHT[gname]
        print(f'    {gname:18} {"held out" if w is None else f"{w:.2f}"}')

    checks = [
        ('unknown never inherits comedy weight', EXPORT_WEIGHT[UNKNOWN] is None),
        ('every genre has a room scale', all(g in ROOM_SCALE for g in GENRES)),
        ('every genre has an export weight', all(g in EXPORT_WEIGHT for g in GENRES)),
        ('productions skip extraction', not extracts_artist('production')),
        ('festivals skip extraction', not extracts_artist('festival')),
        ('dj nights DO extract', extracts_artist('dj_night')),
    ]
    print()
    for label, good in checks:
        print(f'  [{"ok " if good else "FAIL"}] {label}')
        ok = ok and good
    print(f'\n  {"ALL CHECKS PASS" if ok else "SELF-TEST FAILED"}')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(_selftest())
