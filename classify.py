"""
Crypto-class classifier.

Given the text of a user's recent tweets, decide which "class" of Crypto
Twitter the user belongs to: DeFi / Perps / NFT / Trading / Shitposting /
Prediction Markets / General. Pure keyword counting — zero external API
calls, deterministic, free.

The output drives:
  • the `class` field on the analyze response (used to theme subject names
    on the frontend, and to assign the user to a classroom of peers),
  • the `class_breakdown` field (percent of tweets that hit each category,
    rendered as a horizontal bar on the report card).

Tweaking thresholds:
  • A tweet matches a class if any of that class's keywords appear in the
    lower-cased tweet text. A tweet may match more than one class.
  • The user's primary class is the one with the highest hit count, BUT
    only if it crossed `MIN_PRIMARY_HITS` (otherwise the user is
    "General" — they don't post enough crypto-specific content to be
    classified).
"""

from __future__ import annotations

import re
from typing import Iterable

# Match whole words only — "lp" shouldn't fire on "lpga", "gm" shouldn't
# fire on "gmt". We rely on standard word boundaries; multi-word phrases
# fall through to a substring check below.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*")

# Below the bar of "this user is identifiably crypto-Twitter", we leave
# them in General. ~5 hits across the sampled tweets is a soft signal that
# they post about something specific.
MIN_PRIMARY_HITS = 5

# Each class has:
#   - a stable id (used as the dict key on the wire)
#   - a human label
#   - an emoji used as the class crest
#   - a set of single-word keywords (matched on word boundary)
#   - a list of multi-word phrases (matched as case-folded substrings)
CLASSES: dict[str, dict] = {
    "defi": {
        "label": "DeFi Stream",
        "emoji": "🏦",
        "blurb": "Yield, lending, governance, protocol design.",
        "words": {
            "defi", "tvl", "apy", "apr", "yield", "yields", "farm", "farming",
            "vault", "vaults", "lp", "amm", "lending", "borrow", "borrowing",
            "collateral", "stablecoin", "stables", "rebase", "peg", "depeg",
            "liquidity", "lps", "swap", "uniswap", "aave", "compound", "maker",
            "dai", "usdc", "usdt", "frax", "curve", "balancer", "morpho",
            "pendle", "convex", "lido", "rocketpool", "eigenlayer", "ethena",
            "sky", "dao", "snapshot", "veto", "governance", "treasury",
            "staking", "stake", "restake", "restaking", "lst", "lrt",
        },
        "phrases": [
            "real yield", "money market", "money markets", "vote escrow",
            "smart contract", "yield farm", "stable pool", "interest rate",
            "liquid staking",
        ],
    },
    "perps": {
        "label": "Perp DEX Stream",
        "emoji": "📈",
        "blurb": "Leverage, funding, longs, shorts, positions.",
        "words": {
            "perp", "perps", "perpetual", "perpetuals", "leverage", "leveraged",
            "long", "longs", "short", "shorts", "longed", "shorted", "longing",
            "shorting", "funding", "liquidated", "liquidation", "rekt", "stop",
            "stoploss", "stops", "tp", "sl", "position", "positions", "size",
            "margin", "isolated", "cross", "hyperliquid", "hl", "drift", "gmx",
            "dydx", "bluefin", "vertex", "synfutures", "apex", "aevo",
            "lighter", "edgex", "paradex", "ostium", "ranger",
        },
        "phrases": [
            "funding rate", "funding rates", "open interest", "long squeeze",
            "short squeeze", "got liquidated", "got rekt", "10x long",
            "10x short", "sized in", "size up", "perp dex", "max long",
            "max short",
        ],
    },
    "nft": {
        "label": "NFT Stream",
        "emoji": "🖼️",
        "blurb": "Mints, floors, collections, jpegs.",
        "words": {
            "nft", "nfts", "mint", "mints", "minted", "minting", "floor",
            "floors", "jpeg", "jpegs", "pfp", "pfps", "rare", "rarity", "trait",
            "traits", "1of1", "1/1", "drop", "drops", "dropped", "blueprint",
            "magiceden", "opensea", "blur", "tensor", "magic", "punks",
            "cryptopunks", "milady", "ssfn", "redacted", "azuki", "doodles",
            "pudgy", "remilia", "ordinals", "inscriptions", "runes",
            "ercnft", "erc721", "erc1155", "openedition",
        },
        "phrases": [
            "floor price", "blue chip", "blue chips", "free mint", "free mints",
            "open edition", "1 of 1", "art block", "art blocks", "sweep the floor",
            "checks out", "mint pass", "mint price",
        ],
    },
    "trading": {
        "label": "Trading Floor",
        "emoji": "🕯️",
        "blurb": "Charts, setups, levels, calls.",
        "words": {
            "ta", "chart", "charts", "candle", "candles", "wick", "wicks",
            "support", "resistance", "trend", "trendline", "breakout", "breakouts",
            "breakdown", "pump", "dump", "pumps", "dumps", "rally", "fade",
            "fades", "scalp", "scalping", "swing", "swings", "hodl", "hodling",
            "buy", "buying", "sell", "selling", "bid", "ask", "ask",
            "flag", "wedge", "wedges", "triangle", "ema", "sma", "rsi", "macd",
            "fib", "fibs", "vpvr", "volume", "vol", "rejection", "reclaim",
            "reclaims", "tap", "taps", "leg", "legs",
        },
        "phrases": [
            "higher high", "higher low", "lower high", "lower low", "bull flag",
            "bear flag", "head and shoulders", "double top", "double bottom",
            "bollinger band", "moving average", "fib retracement", "fib level",
            "price action", "tape", "the tape", "btc dominance", "alt season",
            "altcoin season", "risk on", "risk off",
        ],
    },
    "shitposting": {
        "label": "Shitposting 101",
        "emoji": "🤡",
        "blurb": "GMs, copes, frens, lore.",
        "words": {
            "gm", "gn", "wagmi", "ngmi", "ser", "fren", "frens", "anon", "anons",
            "based", "cope", "copium", "hopium", "moon", "mooning", "mooned",
            "rekt", "absolutely", "literally", "kek", "lmao", "lol", "ratio",
            "ratiod", "midcurver", "smol", "huge", "gigachad", "chad", "jeet",
            "jeeted", "jeets", "bagholder", "bag", "bags", "btfd", "fud",
            "fudding", "shill", "shilling", "shitpost", "shitposting", "shitposter",
            "alpha", "betas", "uwu", "owo", "lfg", "ngl", "wtf", "tbh",
            "delulu", "midwit", "smolwit",
        },
        "phrases": [
            "gm fren", "gm frens", "gm anon", "gm ser", "few understand",
            "have fun staying poor", "we're so back", "it's so over",
            "this changes everything", "i am so cooked", "we are so back",
            "based and", "ngmi if", "wagmi if", "this is the way",
        ],
    },
    "prediction": {
        "label": "Prediction Markets",
        "emoji": "🎲",
        "blurb": "Odds, contracts, markets on outcomes.",
        "words": {
            "polymarket", "kalshi", "augur", "polymkt", "predictit", "manifold",
            "limitless", "stake", "wager", "wagering", "wagers", "odds", "edge",
            "ev", "+ev", "-ev", "yes", "no", "resolve", "resolved", "resolution",
            "contract", "contracts", "binary", "outcome", "outcomes", "election",
            "candidate", "primary", "fed", "fomc", "inflation", "cpi", "rate",
            "ratecut", "halving", "epoch",
        },
        "phrases": [
            "prediction market", "prediction markets", "polymarket odds",
            "kalshi market", "expected value", "implied probability",
            "buying yes", "buying no", "implied odds", "buy yes", "buy no",
        ],
    },
}


def _tokenise(text: str) -> set[str]:
    """Return the set of lower-cased word tokens in `text`."""
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)}


def classify_tweet(text: str) -> set[str]:
    """Return the set of class ids that this tweet matches.

    A tweet may be matched to zero, one or several classes. We don't try to
    pick a single best class per tweet — counts are aggregated across the
    user's whole sample.
    """
    if not text:
        return set()
    tokens = _tokenise(text)
    lower = text.lower()
    hits: set[str] = set()
    for cid, spec in CLASSES.items():
        if tokens & spec["words"]:
            hits.add(cid)
            continue
        for phrase in spec["phrases"]:
            if phrase in lower:
                hits.add(cid)
                break
    return hits


def classify_user(tweet_texts: Iterable[str]) -> dict:
    """Aggregate per-tweet matches into a primary class + percentage breakdown.

    Returns a dict shaped like:
        {
          "primary": "defi",          # or "general" if no class crossed the bar
          "label": "DeFi Stream",
          "emoji": "🏦",
          "blurb": "...",
          "confidence": 0.42,          # primary_hits / total_hits (0..1)
          "breakdown": [               # always sorted desc by `share`
            {"id": "defi",  "label": "DeFi Stream",   "share": 0.42, "hits": 17},
            {"id": "perps", "label": "Perp DEX",      "share": 0.18, "hits": 7},
            ...
          ],
          "tweets_classified": 73,     # tweets that hit at least one class
          "tweets_total": 130,
        }
    """
    counts: dict[str, int] = {cid: 0 for cid in CLASSES}
    classified = 0
    total = 0
    for text in tweet_texts:
        total += 1
        hits = classify_tweet(text)
        if hits:
            classified += 1
            for cid in hits:
                counts[cid] += 1

    total_hits = sum(counts.values())
    breakdown: list[dict] = []
    for cid, hits in counts.items():
        spec = CLASSES[cid]
        breakdown.append({
            "id": cid,
            "label": spec["label"],
            "emoji": spec["emoji"],
            "share": (hits / total_hits) if total_hits else 0.0,
            "hits": hits,
        })
    breakdown.sort(key=lambda x: (-x["share"], -x["hits"], x["id"]))

    primary_id = breakdown[0]["id"] if breakdown and breakdown[0]["hits"] >= MIN_PRIMARY_HITS else "general"
    if primary_id == "general":
        primary_label = "General Studies"
        primary_emoji = "🎓"
        primary_blurb = "Posts a bit of everything — no clear specialty yet."
        confidence = 0.0
    else:
        spec = CLASSES[primary_id]
        primary_label = spec["label"]
        primary_emoji = spec["emoji"]
        primary_blurb = spec["blurb"]
        confidence = (counts[primary_id] / total_hits) if total_hits else 0.0

    return {
        "primary": primary_id,
        "label": primary_label,
        "emoji": primary_emoji,
        "blurb": primary_blurb,
        "confidence": round(confidence, 3),
        "breakdown": [
            {**b, "share": round(b["share"], 3)} for b in breakdown
        ],
        "tweets_classified": classified,
        "tweets_total": total,
    }


def all_class_ids() -> list[str]:
    return ["general", *CLASSES.keys()]
