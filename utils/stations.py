'''"STACJA NADANIA" of a telegram: a historical, long-gone Warsaw terminus per carrier.

Edit CARRIER_STATIONS / OTHERS_POOL to change the mapping; names are used as written.
'''

from __future__ import annotations

import logging
import unicodedata

log = logging.getLogger(__name__)

CARRIER_STATIONS = {
    "IC": "W-WA PETERSBURSKA",   # PKP Intercity – long-distance trunk line
    "KS": "W-WA WIEDEŃSKA",      # Koleje Śląskie – the Warsaw–Vienna Railway led to Silesia
    "KM": "W-WA TERESPOLSKA",    # Koleje Mazowieckie – Siedlce line = the old Terespol Railway
    "ŁKA": "W-WA KALISKA",       # Łódzka Kolej Aglomeracyjna – Warsaw–Kalisz Railway via Łódź
    "KD": "W-WA KALISKA",        # Koleje Dolnośląskie – westbound gateway
    "PR": "W-WA KOWELSKA",       # Polregio – Vistula Railway (Kolej Nadwiślańska)
}
# Mixed-carrier ("others") telegrams and unknown carriers draw from here.
OTHERS_POOL = [
    "W-WA WIEDEŃSKA",
    "W-WA PETERSBURSKA",
    "W-WA TERESPOLSKA",
    "W-WA KOWELSKA",
    "W-WA KALISKA",
]
OTHERS = "others"

# NFKD does not decompose these into letter + accent
_NO_DECOMPOSITION = str.maketrans({"ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D"})


def normalize_code(code: str) -> str:
    """Carrier code without case, diacritics or surrounding space: 'ŁKA', 'łka', ' LKA ' -> 'lka'."""
    decomposed = unicodedata.normalize("NFKD", code.translate(_NO_DECOMPOSITION))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip().casefold()


def station_for(carrier: str | None, seed: int) -> str:
    """Station of origin for a telegram about `carrier`.

    `carrier` is a carrier code; None or "others" means a mixed-carrier telegram.
    Those, and unknown codes (logged), get a station from OTHERS_POOL chosen by
    `seed` - the telegram's own seed, so a re-render gives the same station.
    """
    if carrier is not None and normalize_code(carrier) != OTHERS:
        known = {normalize_code(code): station for code, station in CARRIER_STATIONS.items()}
        station = known.get(normalize_code(carrier))
        if station is not None:
            return station
        log.warning("no STACJA NADANIA configured for carrier %r, using the others pool", carrier)
    return OTHERS_POOL[seed % len(OTHERS_POOL)]
