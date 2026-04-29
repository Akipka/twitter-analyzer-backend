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

Seed handles below are conservatively well-known, currently-active
Crypto Twitter personalities. The frontend filters classmates whose
avatar fails to load (deactivated / suspended accounts), so a few stale
entries here don't show up as ghost smileys in the grid.
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
SEEDS: dict[str, list[dict[str, str]]] = {
    "defi": [
        {"username": "VitalikButerin",  "displayName": "vitalik.eth"},
        {"username": "haydenzadams",    "displayName": "Hayden Adams"},
        {"username": "StaniKulechov",   "displayName": "Stani Kulechov"},
        {"username": "RuneKek",         "displayName": "Rune"},
        {"username": "DefiIgnas",       "displayName": "Ignas | DeFi"},
        {"username": "DegenSpartan",    "displayName": "DegenSpartan"},
        {"username": "sassal0x",        "displayName": "Anthony Sassano"},
        {"username": "santiagoroel",    "displayName": "Santiago R Santos"},
        {"username": "kaiynne",         "displayName": "Kain Warwick"},
        {"username": "0xMert_",         "displayName": "mert"},
        {"username": "Mira_Network",    "displayName": "Mira"},
        {"username": "TheDeFiPlebs",    "displayName": "DeFi Plebs"},
    ],
    "perps": [
        {"username": "GCRClassic",      "displayName": "GCR"},
        {"username": "CL207",           "displayName": "CL"},
        {"username": "byzgeneral",      "displayName": "Byzantine General"},
        {"username": "AlgodTrading",    "displayName": "Algod"},
        {"username": "CryptoKaleo",     "displayName": "Kaleo"},
        {"username": "HsakaTrades",     "displayName": "Hsaka"},
        {"username": "DonAlt",          "displayName": "DonAlt"},
        {"username": "Pentosh1",        "displayName": "Pentoshi"},
        {"username": "0xfunkmaster",    "displayName": "FunkMaster"},
        {"username": "CryptoCred",      "displayName": "Cred"},
        {"username": "RektCapital",     "displayName": "Rekt Capital"},
        {"username": "TheFlowHorse",    "displayName": "Flow"},
    ],
    "memecoins": [
        {"username": "ansemtrades",     "displayName": "Ansem"},
        {"username": "alon",            "displayName": "alon.sol"},
        {"username": "pumpdotfun",      "displayName": "pump.fun"},
        {"username": "MustStopMurad",   "displayName": "Murad"},
        {"username": "AltcoinGordon",   "displayName": "Gordon"},
        {"username": "blknoiz06",       "displayName": "Ansem (sol)"},
        {"username": "Cryptoinsightuk", "displayName": "Crypto Insight UK"},
        {"username": "0xRamonos",       "displayName": "Ramonos"},
        {"username": "shaw88",          "displayName": "Shaw"},
        {"username": "Cobratate",       "displayName": "Andrew Tate"},
        {"username": "MemeCoinSeason",  "displayName": "Memecoin Season"},
        {"username": "trader1sz",       "displayName": "Trader1sz"},
    ],
    "nft": [
        {"username": "punk6529",        "displayName": "punk6529"},
        {"username": "punk4156",        "displayName": "punk4156"},
        {"username": "FarokhMarket",    "displayName": "Farokh"},
        {"username": "ChrisCantino",    "displayName": "Chris Cantino"},
        {"username": "GMoneyNFT",       "displayName": "gmoney"},
        {"username": "loopifyyy",       "displayName": "Loopify"},
        {"username": "Cryptopathic",    "displayName": "Cryptopathic"},
        {"username": "deezefi",         "displayName": "deeze"},
        {"username": "Pranksy",         "displayName": "Pranksy"},
        {"username": "beaniemaxi",      "displayName": "Beanie"},
        {"username": "kingrobbo",       "displayName": "King Robbo"},
        {"username": "0xQuit",          "displayName": "0xQuit"},
    ],
    "prediction": [
        {"username": "shayne_coplan",   "displayName": "Shayne Coplan"},
        {"username": "Polymarket",      "displayName": "Polymarket"},
        {"username": "Kalshi",          "displayName": "Kalshi"},
        {"username": "DomerEnjoyer",    "displayName": "Domer"},
        {"username": "AlexTabarrok",    "displayName": "Alex Tabarrok"},
        {"username": "robinhanson",     "displayName": "Robin Hanson"},
        {"username": "tylercowen",      "displayName": "Tyler Cowen"},
        {"username": "scaramucci",      "displayName": "Anthony Scaramucci"},
        {"username": "stevenkrieger",   "displayName": "Steven Krieger"},
        {"username": "domahhh",         "displayName": "Doma"},
        {"username": "hjbarraza",       "displayName": "HJ Barraza"},
        {"username": "Gambdan",         "displayName": "Gambdan"},
    ],
    "rwa": [
        {"username": "OndoFinance",     "displayName": "Ondo Finance"},
        {"username": "Securitize",      "displayName": "Securitize"},
        {"username": "centrifuge",      "displayName": "Centrifuge"},
        {"username": "goldfinch_fi",    "displayName": "Goldfinch"},
        {"username": "MapleFinance",    "displayName": "Maple Finance"},
        {"username": "BackedFi",        "displayName": "Backed"},
        {"username": "BlackRock",       "displayName": "BlackRock"},
        {"username": "MakerDAO",        "displayName": "Sky / Maker"},
        {"username": "ChainlinkLabs",   "displayName": "Chainlink"},
        {"username": "PaxosGlobal",     "displayName": "Paxos"},
        {"username": "circle",          "displayName": "Circle"},
        {"username": "RWA_xyz",         "displayName": "rwa.xyz"},
    ],
    "ai": [
        {"username": "shawmakesmagic",  "displayName": "Shaw"},
        {"username": "ai16zdao",        "displayName": "ai16z"},
        {"username": "virtuals_io",     "displayName": "Virtuals Protocol"},
        {"username": "elizaOS",         "displayName": "ElizaOS"},
        {"username": "bittensor_",      "displayName": "Bittensor"},
        {"username": "FetchAI_Labs",    "displayName": "Fetch.ai"},
        {"username": "OceanProtocol",   "displayName": "Ocean Protocol"},
        {"username": "rendernetwork",   "displayName": "Render Network"},
        {"username": "akashnet_",       "displayName": "Akash Network"},
        {"username": "ilblackdragon",   "displayName": "Illia Polosukhin"},
        {"username": "NEARProtocol",    "displayName": "NEAR Protocol"},
        {"username": "0xPrismatic",     "displayName": "Prismatic"},
    ],
    "airdrops": [
        {"username": "CryptoTxn",       "displayName": "CryptoTxn"},
        {"username": "ardizor",         "displayName": "Ardizor"},
        {"username": "thedefinetwork",  "displayName": "The DeFi Network"},
        {"username": "splinter0n",      "displayName": "splinter"},
        {"username": "0xCheeezzyyyy",   "displayName": "Cheeezzyyyy"},
        {"username": "0xtaetaehoho",    "displayName": "tae"},
        {"username": "wublockchain",    "displayName": "Wu Blockchain"},
        {"username": "DeFiStable",      "displayName": "DeFi Stable"},
        {"username": "CryptoLabsRes",   "displayName": "Crypto Labs"},
        {"username": "kornouttv",       "displayName": "Kornouttv"},
        {"username": "Hyperliquid",     "displayName": "Hyperliquid"},
        {"username": "etherfi",         "displayName": "ether.fi"},
    ],
    "socialfi": [
        {"username": "dwr",             "displayName": "Dan Romero"},
        {"username": "v",               "displayName": "Varun Srinivasan"},
        {"username": "stani",           "displayName": "Stani (Lens)"},
        {"username": "lensprotocol",    "displayName": "Lens Protocol"},
        {"username": "farcaster_xyz",   "displayName": "Farcaster"},
        {"username": "warpcast",        "displayName": "Warpcast"},
        {"username": "KaitoAI",         "displayName": "Kaito AI"},
        {"username": "yupio",           "displayName": "Yup"},
        {"username": "phaver",          "displayName": "Phaver"},
        {"username": "Drakula_app",     "displayName": "Drakula"},
        {"username": "Web3SocialClub",  "displayName": "Web3 Social Club"},
        {"username": "0xKofi",          "displayName": "Kofi"},
    ],
    "restaking": [
        {"username": "eigenlayer",      "displayName": "EigenLayer"},
        {"username": "sreeramkannan",   "displayName": "Sreeram Kannan"},
        {"username": "etherfi",         "displayName": "ether.fi"},
        {"username": "Renzo_Protocol",  "displayName": "Renzo"},
        {"username": "puffer_finance",  "displayName": "Puffer"},
        {"username": "swellnetworkio",  "displayName": "Swell"},
        {"username": "kelpdao",         "displayName": "Kelp DAO"},
        {"username": "Karak_Network",   "displayName": "Karak"},
        {"username": "symbioticfi",     "displayName": "Symbiotic"},
        {"username": "babylon_chain",   "displayName": "Babylon"},
        {"username": "stakestone_",     "displayName": "Stakestone"},
        {"username": "lidofinance",     "displayName": "Lido"},
    ],
    "l2": [
        {"username": "0xPolygon",       "displayName": "Polygon"},
        {"username": "arbitrum",        "displayName": "Arbitrum"},
        {"username": "Optimism",        "displayName": "Optimism"},
        {"username": "base",            "displayName": "Base"},
        {"username": "zksync",          "displayName": "zkSync"},
        {"username": "StarkWareLtd",    "displayName": "StarkWare"},
        {"username": "ScrollZKP",       "displayName": "Scroll"},
        {"username": "LineaBuild",      "displayName": "Linea"},
        {"username": "MonadXYZ",        "displayName": "Monad"},
        {"username": "CelestiaOrg",     "displayName": "Celestia"},
        {"username": "berachain",       "displayName": "Berachain"},
        {"username": "solana",          "displayName": "Solana"},
    ],
    "macro": [
        {"username": "RaoulGMI",        "displayName": "Raoul Pal"},
        {"username": "saylor",          "displayName": "Michael Saylor"},
        {"username": "APompliano",      "displayName": "Pomp"},
        {"username": "WClementeIII",    "displayName": "Will Clemente"},
        {"username": "scaramucci",      "displayName": "Anthony Scaramucci"},
        {"username": "RyanSAdams",      "displayName": "Ryan Sean Adams"},
        {"username": "MartyBent",       "displayName": "Marty Bent"},
        {"username": "balajis",         "displayName": "Balaji"},
        {"username": "TimDraper",       "displayName": "Tim Draper"},
        {"username": "novogratz",       "displayName": "Mike Novogratz"},
        {"username": "MarkYusko",       "displayName": "Mark Yusko"},
        {"username": "lynaldencontact", "displayName": "Lyn Alden"},
    ],
    "general": [
        {"username": "balajis",         "displayName": "Balaji"},
        {"username": "naval",           "displayName": "Naval"},
        {"username": "elonmusk",        "displayName": "Elon Musk"},
        {"username": "saylor",          "displayName": "Michael Saylor"},
        {"username": "cz_binance",      "displayName": "CZ"},
        {"username": "brian_armstrong", "displayName": "Brian Armstrong"},
        {"username": "dwr",             "displayName": "Dan Romero"},
        {"username": "jessepollak",     "displayName": "Jesse Pollak"},
        {"username": "TimDraper",       "displayName": "Tim Draper"},
        {"username": "RaoulGMI",        "displayName": "Raoul Pal"},
        {"username": "APompliano",      "displayName": "Pomp"},
        {"username": "VitalikButerin",  "displayName": "vitalik.eth"},
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
