# Immutable Keyword Planner run measurements

Each successful live monthly pull writes one compact JSON manifest here. It records the run time,
Google month, explicit Search + Partners network, language, India/USA/Canada targets, approved
keyword, mapping outcome, and latest value/state per artist and geography. It deliberately does
not duplicate the retained 48-month histories in `data/demand.json`.

Files are append-only measurement evidence. A duplicate run timestamp is rejected rather than
overwritten. Manifests are atomically written and reject credential-like fields; `data/config.json`
and API responses containing credentials are never copied here.