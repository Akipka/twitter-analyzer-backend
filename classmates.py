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

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Iterable


def _coerce_int(value: Any) -> int:
    """Best-effort int coercion for twitterapi.io stat fields, which can
    arrive as strings or None depending on the upstream response. Anything
    that fails to parse counts as zero so it trips the zombie filter."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

log = logging.getLogger(__name__)


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
                    "avatarUrl": "",
                    "seeded": True,
                    "addedAt": 0.0,
                }
                for s in seeds
            ]
            _ROSTERS[cid] = roster


_init_rosters()


def add_member(
    class_id: str,
    username: str,
    display_name: str,
    avatar_url: str = "",
) -> None:
    """Append `username` to `class_id`'s roster.

    Deduplicates case-insensitively; if the user is already on the roster,
    they're moved to the front (so freshly-analyzed users appear first).
    Once the roster reaches CLASS_SIZE, oldest non-seed entries are evicted.
    `avatar_url` is the user's twimg profile_image URL (from
    twitterapi.io's `profilePicture` field) — emitted directly in the
    classmates response so the frontend can avoid the unavatar.io proxy.
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
            "avatarUrl": avatar_url or "",
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


def set_member_avatar(class_id: str, username: str, avatar_url: str) -> None:
    """Update the cached avatarUrl for an existing roster member.

    Called by `validate_class_seeds` after a successful profile lookup so
    seeded entries pick up their real twimg avatar URL without ever needing
    to hit our /api/avatar proxy."""
    if class_id not in _ROSTERS or not avatar_url:
        return
    norm = username.lstrip("@").lower()
    with _LOCK:
        for r in _ROSTERS[class_id]:
            if r["username"].lower() == norm:
                r["avatarUrl"] = avatar_url
                break


def get_roster(class_id: str) -> list[dict]:
    """Snapshot of the roster for `class_id`. Sorted: real users first
    (most-recent additions on top), seeded "celebrity" entries below.

    Filters out seeded entries that have been validated as dead via
    `mark_seed_dead`. Real (user-added) entries are always kept; the
    user analysed real, so by definition they exist on Twitter."""
    with _LOCK:
        roster = list(_ROSTERS.get(class_id, []))
    real = [r for r in roster if not r["seeded"]]
    seed = [
        r for r in roster
        if r["seeded"] and not _is_seed_dead(r["username"])
    ]
    real.sort(key=lambda r: -r.get("addedAt", 0.0))
    return real + seed


# ── Seed validation ────────────────────────────────────────────────────────

# We persist the dead-seeds list on disk so a Render redeploy doesn't have to
# re-validate from scratch. /tmp survives a single dyno's lifetime, which is
# plenty given seed validation runs at most once per dyno in practice.
_SEED_STATUS_PATH = os.environ.get(
    "SEED_STATUS_PATH",
    "/tmp/seed_status.json",
)
_SEED_STATUS_LOCK = threading.Lock()
# username (lowercase) -> True if confirmed dead/suspended/404.
_SEED_DEAD: dict[str, bool] = {}
# class_id -> True once that class's seeds have been validated this process.
_SEEDS_VALIDATED: dict[str, bool] = {}


def _load_seed_status() -> None:
    """Best-effort load of cached dead-seeds from disk."""
    global _SEED_DEAD
    try:
        with open(_SEED_STATUS_PATH, "r", encoding="utf-8") as fp:
            data = json.load(fp) or {}
        if isinstance(data, dict):
            _SEED_DEAD = {
                k.lower(): bool(v) for k, v in data.items() if isinstance(k, str)
            }
            log.info("seed_status: loaded %d entries from %s", len(_SEED_DEAD), _SEED_STATUS_PATH)
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("seed_status: failed to load %s: %s", _SEED_STATUS_PATH, exc)


def _save_seed_status() -> None:
    """Best-effort save of dead-seeds list to disk. Silently ignores errors."""
    try:
        with open(_SEED_STATUS_PATH, "w", encoding="utf-8") as fp:
            json.dump(_SEED_DEAD, fp)
    except Exception as exc:
        log.warning("seed_status: failed to save %s: %s", _SEED_STATUS_PATH, exc)


_load_seed_status()


def _is_seed_dead(username: str) -> bool:
    return _SEED_DEAD.get(username.lower(), False)


def mark_seed_dead(username: str) -> None:
    """Mark a seed handle as confirmed dead. Persists to disk."""
    with _SEED_STATUS_LOCK:
        _SEED_DEAD[username.lower()] = True
        _save_seed_status()


def validate_class_seeds(
    class_id: str,
    profile_fetcher: Callable[[str], dict | None],
) -> None:
    """Validate every seed handle in `class_id`'s roster against
    `profile_fetcher` (typically server.fetch_profile). Marks each handle
    that returns None / raises as dead, and persists the result.

    Idempotent within a single process — does nothing on subsequent calls
    for the same class. Designed to be called inline from the analyze
    handler the first time a roster for a given class is requested.
    """
    with _SEED_STATUS_LOCK:
        if _SEEDS_VALIDATED.get(class_id):
            return
        _SEEDS_VALIDATED[class_id] = True
    seeds = SEEDS.get(class_id, [])
    for s in seeds:
        username = s["username"]
        if _is_seed_dead(username):
            continue
        try:
            profile = profile_fetcher(username)
        except Exception as exc:
            log.info("seed_validate: %s lookup raised %s; skipping", username, exc)
            continue
        if profile is None:
            log.info("seed_validate: %s is dead, marking", username)
            mark_seed_dead(username)
            continue
        # Beyond plain 404, also drop seeds that twitterapi.io still returns
        # but are clearly zombie / abandoned handles: default egg-avatar,
        # zero followers, or zero posts. These show up as empty grey tiles
        # in the classroom and erode trust in the roster.
        avatar_url = profile.get("profilePicture") or profile.get("avatarUrl") or ""
        followers = _coerce_int(profile.get("followers"))
        statuses = _coerce_int(profile.get("statusesCount"))
        is_default_avatar = (
            not avatar_url
            or "default_profile_images" in avatar_url.lower()
        )
        if is_default_avatar or followers < 50 or statuses < 5:
            log.info(
                "seed_validate: %s is zombie (avatar=%s followers=%d statuses=%d), marking",
                username,
                "default" if is_default_avatar else "ok",
                followers,
                statuses,
            )
            mark_seed_dead(username)
            continue
        # Live seed: pick up the real avatar URL so the frontend can render
        # it directly without going through the unavatar proxy.
        set_member_avatar(class_id, username, avatar_url)


def all_class_ids() -> Iterable[str]:
    return _ROSTERS.keys()
