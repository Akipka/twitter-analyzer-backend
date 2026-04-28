"""
Classmate roster — for each crypto class, keep a roster of accounts that
have been classified into it.

Storage is in-process (a dict). Restarting the dyno wipes any user-grown
roster; the SEED entries below are re-loaded on every boot so a freshly
deployed server already has a populated classroom for every class — the
first user who lands on a "Perps" class still sees a full roster.

Why not a DB? On Render's free tier we're not paying for one, and class
membership is a fun feature, not load-bearing data. If a user is
re-classified after the dyno restart, they just get re-added.
"""

from __future__ import annotations

import threading
import time
from typing import Iterable


# A traditional US public-school class is ~25 students. We cap each
# roster at this size; once full, the oldest entry rotates out (FIFO).
# This keeps memory bounded and the UI is built around exactly this size.
CLASS_SIZE = 28


# Seed accounts per class. Hand-curated to be recognisable Crypto Twitter
# personalities for each lane so that even an empty server produces a
# convincing classroom.
#
# These names are PUBLIC X handles. Avatars are fetched on-demand by the
# frontend through our /api/avatar/<handle> proxy, so we don't need any
# pre-stored image data.
SEEDS: dict[str, list[dict[str, str]]] = {
    "defi": [
        {"username": "VitalikButerin", "displayName": "Vitalik Buterin"},
        {"username": "haydenzadams",   "displayName": "Hayden Adams"},
        {"username": "StaniKulechov",  "displayName": "Stani Kulechov"},
        {"username": "andreCronjeTech","displayName": "Andre Cronje"},
        {"username": "RuneKek",        "displayName": "Rune Christensen"},
        {"username": "kaiynne",        "displayName": "Kain Warwick"},
        {"username": "santiagoroel",   "displayName": "Santiago R Santos"},
        {"username": "DefiIgnas",      "displayName": "Ignas | DeFi"},
        {"username": "DegenSpartan",   "displayName": "DegenSpartan"},
        {"username": "jadler0",        "displayName": "John Adler"},
        {"username": "DefiPrince_",    "displayName": "DeFi Prince"},
        {"username": "TheDeFiPlebs",   "displayName": "DeFi Plebs"},
    ],
    "perps": [
        {"username": "0xJeff",         "displayName": "0xJeff"},
        {"username": "GCRClassic",     "displayName": "GiganticRebirth"},
        {"username": "CL207",          "displayName": "Cobie's Cousin"},
        {"username": "0xLawliette",    "displayName": "Lawliette"},
        {"username": "trader1sz",      "displayName": "Trader1sz"},
        {"username": "byzgeneral",     "displayName": "Byzantine General"},
        {"username": "AlgodTrading",   "displayName": "Algod"},
        {"username": "CryptoKaleo",    "displayName": "Kaleo"},
        {"username": "HsakaTrades",    "displayName": "Hsaka"},
        {"username": "DonAlt",         "displayName": "DonAlt"},
        {"username": "0xfunkmaster",   "displayName": "FunkMaster"},
        {"username": "tradetheflow",   "displayName": "Trade The Flow"},
    ],
    "nft": [
        {"username": "punk6529",       "displayName": "punk6529"},
        {"username": "punk4156",       "displayName": "punk4156"},
        {"username": "beaniemaxi",     "displayName": "Beanie"},
        {"username": "FarokhMarket",   "displayName": "Farokh"},
        {"username": "ChrisCantino",   "displayName": "Chris Cantino"},
        {"username": "GMoneyNFT",      "displayName": "gmoney"},
        {"username": "loopifyyy",      "displayName": "Loopify"},
        {"username": "Cryptopathic",   "displayName": "Cryptopathic"},
        {"username": "CrypToadz",      "displayName": "CrypToadz"},
        {"username": "deezefi",        "displayName": "deeze"},
        {"username": "Pranksy",        "displayName": "Pranksy"},
        {"username": "kingrobbo",      "displayName": "King Robbo"},
    ],
    "trading": [
        {"username": "CryptoCred",     "displayName": "Cred"},
        {"username": "TheFlowHorse",   "displayName": "Flow"},
        {"username": "PostyXBT",       "displayName": "Posty"},
        {"username": "thedefiedge",    "displayName": "The DeFi Edge"},
        {"username": "RektCapital",    "displayName": "Rekt Capital"},
        {"username": "CryptoBullet1",  "displayName": "Crypto Bullet"},
        {"username": "Pentosh1",       "displayName": "Pentoshi"},
        {"username": "smartestmoney_", "displayName": "Smart Money"},
        {"username": "TraderXO",       "displayName": "TraderXO"},
        {"username": "CryptoMessiah",  "displayName": "Crypto Messiah"},
        {"username": "TheCryptoDog",   "displayName": "The Crypto Dog"},
        {"username": "HornHairs",      "displayName": "HornHairs"},
    ],
    "shitposting": [
        {"username": "CL207",          "displayName": "CL"},
        {"username": "0xngmi",         "displayName": "0xngmi"},
        {"username": "fiskantes",      "displayName": "Fiskantes"},
        {"username": "redphonecrypto", "displayName": "Red Phone Crypto"},
        {"username": "0xCygaar",       "displayName": "Cygaar"},
        {"username": "0x_Kun",         "displayName": "Kun"},
        {"username": "DegenerateNews", "displayName": "Degenerate News"},
        {"username": "MaxResnick1",    "displayName": "Max Resnick"},
        {"username": "RookieXBT",      "displayName": "RookieXBT"},
        {"username": "Tree_of_Alpha",  "displayName": "Tree of Alpha"},
        {"username": "0xQuit",         "displayName": "0xQuit"},
        {"username": "DefiWim",        "displayName": "DefiWim"},
    ],
    "prediction": [
        {"username": "shayne_coplan",  "displayName": "Shayne Coplan"},
        {"username": "domahhh",        "displayName": "Doma"},
        {"username": "Polymarket",     "displayName": "Polymarket"},
        {"username": "Kalshi",         "displayName": "Kalshi"},
        {"username": "DomerEnjoyer",   "displayName": "Domer"},
        {"username": "AlexTabarrok",   "displayName": "Alex Tabarrok"},
        {"username": "hjbarraza",      "displayName": "HJ Barraza"},
        {"username": "robinhanson",    "displayName": "Robin Hanson"},
        {"username": "Gambdan",        "displayName": "Gambdan"},
        {"username": "scaramucci",     "displayName": "Anthony Scaramucci"},
        {"username": "tylercowen",     "displayName": "Tyler Cowen"},
        {"username": "stevenkrieger",  "displayName": "Steven Krieger"},
    ],
    "general": [
        {"username": "balajis",        "displayName": "Balaji"},
        {"username": "naval",          "displayName": "Naval"},
        {"username": "elonmusk",       "displayName": "Elon Musk"},
        {"username": "saylor",         "displayName": "Michael Saylor"},
        {"username": "cz_binance",     "displayName": "CZ"},
        {"username": "brian_armstrong","displayName": "Brian Armstrong"},
        {"username": "dwr",            "displayName": "Dan Romero"},
        {"username": "jessepollak",    "displayName": "Jesse Pollak"},
        {"username": "AnnaShostya",    "displayName": "Anna Shostya"},
        {"username": "TimDraper",      "displayName": "Tim Draper"},
        {"username": "RaoulGMI",       "displayName": "Raoul Pal"},
        {"username": "APompliano",     "displayName": "Pomp"},
    ],
}


# ── Runtime store ──────────────────────────────────────────────────────────

# class_id -> list of {username, displayName, addedAt, seeded}.
# Most-recently-added first.
_ROSTERS: dict[str, list[dict]] = {}
_LOCK = threading.Lock()


def _init_rosters() -> None:
    """Hot-load seeds into the runtime store. Called once at import."""
    with _LOCK:
        for cid, seeds in SEEDS.items():
            roster = [
                {
                    "username": s["username"],
                    "displayName": s["displayName"],
                    "seeded": True,
                    "addedAt": 0.0,
                }
                for s in seeds
            ]
            _ROSTERS[cid] = roster


_init_rosters()


def add_member(class_id: str, username: str, display_name: str) -> None:
    """Append `username` to `class_id`'s roster.

    Deduplicates case-insensitively; if the user is already on the roster,
    they're moved to the front (so freshly-analyzed users appear first).
    Once the roster reaches CLASS_SIZE, oldest non-seed entries are evicted.
    """
    if class_id not in _ROSTERS:
        return
    norm = username.lstrip("@").lower()
    if not norm:
        return
    with _LOCK:
        roster = _ROSTERS[class_id]
        # Remove any existing instance (case-insensitive).
        roster = [r for r in roster if r["username"].lower() != norm]
        roster.insert(0, {
            "username": username.lstrip("@"),
            "displayName": display_name or username.lstrip("@"),
            "seeded": False,
            "addedAt": time.time(),
        })
        # Evict oldest non-seed if oversized.
        while len(roster) > CLASS_SIZE:
            for i in range(len(roster) - 1, -1, -1):
                if not roster[i]["seeded"]:
                    roster.pop(i)
                    break
            else:
                # All entries are seeded; just trim from tail.
                roster.pop()
        _ROSTERS[class_id] = roster


def get_roster(class_id: str) -> list[dict]:
    """Snapshot of the roster for `class_id`. Sorted: real users first
    (most-recent additions on top), seeded "celebrity" entries below."""
    with _LOCK:
        roster = list(_ROSTERS.get(class_id, []))
    real = [r for r in roster if not r["seeded"]]
    seed = [r for r in roster if r["seeded"]]
    real.sort(key=lambda r: -r.get("addedAt", 0.0))
    return real + seed


def all_class_ids() -> Iterable[str]:
    return _ROSTERS.keys()
