"""
Crypto-class classifier.

Given the text of a user's recent tweets, decide which "class" of Crypto
Twitter the user belongs to. Pure keyword counting — zero external API
calls, deterministic, free.

Twelve classes are tracked plus a "general" fallback when no class
crosses the recognition bar. The output drives:

  • the `class` field on the analyze response (used to theme subject
    names on the frontend, and to assign the user to a classroom of
    peers),
  • the `class_breakdown` field (percent of tweets that hit each
    category, rendered as a horizontal bar on the report card).

Tweaking thresholds:
  • A tweet matches a class if any of that class's keywords appear in
    the lower-cased tweet text. A tweet may match more than one class.
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
# them in General. With a 30-tweet sample, ~4 hits is a soft signal that
# they post about something specific.
MIN_PRIMARY_HITS = 4

# Each class has:
#   - a stable id (used as the dict key on the wire)
#   - a human label
#   - an emoji used as the class crest
#   - a one-line blurb shown in the class assignment card
#   - a set of single-word keywords (matched on word boundary)
#   - a list of multi-word phrases (matched as case-folded substrings)
CLASSES: dict[str, dict] = {
    "defi": {
        "label": "DeFi Stream",
        "emoji": "🏦",
        "blurb": "Yield, lending, governance, protocol design.",
        "words": {
            "defi", "tvl", "apy", "apr", "yield", "yields", "vault", "vaults",
            "lp", "amm", "lending", "borrow", "borrowing", "collateral",
            "stablecoin", "stables", "rebase", "peg", "depeg", "liquidity",
            "lps", "swap", "uniswap", "aave", "compound", "maker", "dai",
            "usdc", "usdt", "frax", "curve", "balancer", "morpho", "pendle",
            "convex", "lido", "rocketpool", "ethena", "sky", "dao", "snapshot",
            "veto", "governance", "treasury", "mev", "flashloan",
        },
        "phrases": [
            "real yield", "money market", "money markets", "vote escrow",
            "smart contract", "yield farm", "stable pool", "interest rate",
            "ve token", "ve tokens", "permissionless lending",
        ],
    },
    "perps": {
        "label": "Perp DEX Stream",
        "emoji": "📈",
        "blurb": "Leverage, funding, longs, shorts, positions.",
        "words": {
            "perp", "perps", "perpetual", "perpetuals", "leverage", "leveraged",
            "long", "longs", "short", "shorts", "longed", "shorted", "longing",
            "shorting", "funding", "liquidated", "liquidation", "rekt",
            "stoploss", "stops", "tp", "sl", "position", "positions",
            "margin", "isolated", "hyperliquid", "hl", "drift", "gmx",
            "dydx", "bluefin", "vertex", "synfutures", "apex", "aevo",
            "lighter", "edgex", "paradex", "ostium", "ranger", "scalp",
            "scalping", "swing", "pnl",
        },
        "phrases": [
            "funding rate", "funding rates", "open interest", "long squeeze",
            "short squeeze", "got liquidated", "got rekt", "10x long",
            "10x short", "sized in", "size up", "perp dex", "max long",
            "max short", "pnl porn", "stop loss", "took profit",
        ],
    },
    "memecoins": {
        "label": "Memecoin Casino",
        "emoji": "🎰",
        "blurb": "Launches, pumps, drama, rugs.",
        "words": {
            "meme", "memes", "memecoin", "memecoins", "pumpfun", "pump",
            "pumps", "pumped", "pumping", "dump", "dumped", "dumping", "rug",
            "rugged", "rugpull", "rugged", "shitcoin", "shitcoins", "ape",
            "aped", "aping", "snipe", "sniped", "sniping", "wif", "bonk",
            "popcat", "doge", "shib", "pepe", "moodeng", "fartcoin", "trump",
            "melania", "billy", "chillguy", "goat", "ai16z", "ansem", "hotmom",
            "neiro", "michi", "moo", "bome", "myro", "smolting",
        },
        "phrases": [
            "pump.fun", "pump fun", "fresh launch", "new launch", "ca:",
            "contract address", "1 billion mc", "10m mc", "100m mc",
            "send it", "this is the one", "early call", "moonshot",
            "to the moon", "low cap gem", "dev sold", "dev rugged",
        ],
    },
    "nft": {
        "label": "NFT Atelier",
        "emoji": "🖼️",
        "blurb": "Mints, floors, collections, jpegs.",
        "words": {
            "nft", "nfts", "mint", "mints", "minted", "minting", "floor",
            "floors", "jpeg", "jpegs", "pfp", "pfps", "rare", "rarity",
            "trait", "traits", "drop", "drops", "dropped", "magiceden",
            "opensea", "blur", "tensor", "punks", "cryptopunks", "milady",
            "redacted", "azuki", "doodles", "pudgy", "remilia", "ordinals",
            "inscriptions", "runes", "erc721", "erc1155", "openedition",
            "highlight", "manifold", "foundation", "superrare",
        },
        "phrases": [
            "floor price", "blue chip", "blue chips", "free mint", "free mints",
            "open edition", "1 of 1", "art block", "art blocks", "sweep the floor",
            "mint pass", "mint price", "secondary market", "secondary sales",
        ],
    },
    "prediction": {
        "label": "Prediction Markets",
        "emoji": "🎲",
        "blurb": "Polymarket, Kalshi, odds on everything.",
        "words": {
            "polymarket", "kalshi", "augur", "polymkt", "predictit", "manifold",
            "limitless", "wager", "wagering", "wagers", "odds", "edge", "ev",
            "yes", "no", "resolve", "resolved", "resolution", "binary",
            "outcome", "outcomes", "election", "candidate", "primary", "fomc",
            "halving", "epoch", "trader",
        },
        "phrases": [
            "prediction market", "prediction markets", "polymarket odds",
            "kalshi market", "expected value", "implied probability",
            "buying yes", "buying no", "implied odds", "buy yes", "buy no",
            "+ev", "-ev", "fade the public", "sharp money", "kelly criterion",
        ],
    },
    "rwa": {
        "label": "RWA Desk",
        "emoji": "🏛️",
        "blurb": "Real-world assets, tokenization, T-bills.",
        "words": {
            "rwa", "rwas", "tokenization", "tokenize", "tokenized", "tokenizing",
            "ondo", "maple", "centrifuge", "goldfinch", "credix", "blackrock",
            "blackrocks", "buidl", "tbill", "tbills", "treasuries", "treasury",
            "treasurybills", "tradfi", "fintech", "credit", "lending",
            "private", "securitize", "securities", "etf", "etfs", "stocks",
            "tokenstocks", "stableyield", "yieldcoupon", "coupon", "coupons",
            "deed", "deeds", "title", "titles", "asset",
        },
        "phrases": [
            "real world assets", "real-world assets", "tokenized treasuries",
            "tokenized treasury", "tokenized stocks", "tokenized credit",
            "us treasuries", "money market fund", "private credit",
            "asset backed", "off chain yield", "off-chain yield",
        ],
    },
    "ai": {
        "label": "AI × Crypto Lab",
        "emoji": "🤖",
        "blurb": "AI agents, autonomous traders, model markets.",
        "words": {
            "ai", "agents", "agent", "agentic", "autonomous", "llm", "llms",
            "gpt", "claude", "anthropic", "openai", "deepseek", "deepmind",
            "model", "models", "inference", "inferences", "inferencing",
            "dataset", "datasets", "embedding", "embeddings", "rag", "vector",
            "bittensor", "tao", "fetch", "ocean", "render", "rndr", "akash",
            "io", "near", "virtuals", "agi", "asi", "neural", "transformer",
            "transformers", "diffusion", "fine", "tuning", "finetune",
            "finetuning", "ai16z", "elizaos", "swarm", "swarms",
        },
        "phrases": [
            "ai agent", "ai agents", "autonomous agent", "autonomous agents",
            "ai trader", "ai traders", "model marketplace", "compute network",
            "fine tuning", "fine-tune", "open source ai", "open-source ai",
            "ai x crypto", "ai meets crypto", "intelligent contract",
        ],
    },
    "airdrops": {
        "label": "Airdrop Farm",
        "emoji": "🪂",
        "blurb": "Points, eligibility, sybil checks, claims.",
        "words": {
            "airdrop", "airdrops", "claim", "claims", "claimed", "claiming",
            "claimable", "points", "point", "ptsfarm", "farm", "farming",
            "farmed", "farmer", "farmers", "season", "seasons", "epoch",
            "epochs", "tge", "wallet", "wallets", "sybil", "sybils",
            "eligibility", "eligible", "eligibility", "snapshot", "snapshots",
            "linea", "scroll", "zksync", "starknet", "blast", "manta",
            "berachain", "monad", "movement", "fuel", "hyperliquid", "lido",
            "ether", "etherfi", "kelp", "renzo", "puffer", "swell",
        },
        "phrases": [
            "points farming", "point farming", "airdrop farm", "farming season",
            "tge soon", "tge confirmed", "snapshot date", "first farmer",
            "max farm", "boost multiplier", "referral code", "refer code",
            "bonus points", "double points", "season 2", "season 3",
        ],
    },
    "socialfi": {
        "label": "SocialFi Lounge",
        "emoji": "📡",
        "blurb": "Creator economy, content monetization in crypto.",
        "words": {
            "socialfi", "creator", "creators", "tip", "tips", "tipped",
            "tipping", "subscriber", "subscribers", "follower", "followers",
            "monetize", "monetization", "monetized", "monetizing", "audience",
            "channel", "channels", "stream", "streams", "streaming", "podcast",
            "podcasts", "newsletter", "newsletters", "subscribe", "patron",
            "patrons", "supporter", "supporters", "lens", "farcaster", "fcast",
            "warpcast", "fc", "phaver", "drakula", "rep", "reputation",
            "engage", "engagement", "infofi", "kaito", "yapper", "yappers",
            "yap", "yapping",
        },
        "phrases": [
            "creator economy", "content monetization", "social token",
            "social tokens", "engagement farming", "tip jar", "paid posts",
            "paid newsletter", "fan token", "fan tokens", "creator coin",
            "creator coins", "lens protocol", "info-fi", "info fi",
        ],
    },
    "restaking": {
        "label": "Restaking Lab",
        "emoji": "🔁",
        "blurb": "EigenLayer, AVS, LRTs, shared security.",
        "words": {
            "restaking", "restake", "restaked", "restakes", "eigenlayer",
            "eigen", "eigenpod", "eigenpods", "avs", "avses", "operator",
            "operators", "lst", "lsts", "lrt", "lrts", "lido", "rocketpool",
            "swell", "renzo", "kelp", "etherfi", "puffer", "altlayer",
            "stader", "ankr", "redstone", "babylon", "symbiotic", "karak",
            "kelpdao", "stakestone", "ssv", "ethereum", "validator",
            "validators", "consensus", "slashing", "slashed",
        },
        "phrases": [
            "liquid staking", "liquid restaking", "shared security",
            "actively validated", "actively validated services", "av s",
            "operator set", "operator sets", "restaking points",
            "restaking yield", "eigen points", "rsETH", "ezETH", "weETH",
            "operator slashing", "lrt yield",
        ],
    },
    "l2": {
        "label": "Layer-2 Atelier",
        "emoji": "🛣️",
        "blurb": "Rollups, new L1s, scaling, app chains.",
        "words": {
            "l2", "l2s", "l1", "l1s", "rollup", "rollups", "optimism", "op",
            "arbitrum", "arb", "base", "scroll", "linea", "zksync", "zk",
            "starknet", "stark", "polygon", "matic", "blast", "manta",
            "mantle", "fraxtal", "kroma", "cyber", "boba", "metis", "celestia",
            "tia", "monad", "sei", "sui", "aptos", "movement", "berachain",
            "bera", "fuel", "tonchain", "ton", "kaspa", "kas", "near", "icp",
            "tron", "trx", "solana", "sol", "appchain", "appchains",
        },
        "phrases": [
            "layer 2", "layer two", "layer 1", "layer one", "data availability",
            "data-availability", "execution layer", "settlement layer",
            "modular blockchain", "monolithic chain", "app chain", "app-chain",
            "shared sequencer", "based rollup", "based rollups", "zk proof",
            "zk proofs", "validity proof", "fraud proof",
        ],
    },
    "macro": {
        "label": "Macro × Crypto",
        "emoji": "🌍",
        "blurb": "Fed, rates, ETFs, regulation, BTC as macro asset.",
        "words": {
            "macro", "fed", "fomc", "powell", "yellen", "treasury", "rate",
            "rates", "ratecut", "rates", "inflation", "cpi", "ppi", "pce",
            "gdp", "unemployment", "nfp", "jobs", "recession", "recessions",
            "qe", "qt", "tapering", "tightening", "easing", "rrp", "tga",
            "dxy", "spx", "nasdaq", "vix", "etf", "etfs", "ibit", "fbtc",
            "ethereum", "ether", "spotetf", "spotbtc", "spoteth", "blackrock",
            "fidelity", "vanguard", "regulation", "regulator", "regulators",
            "regulating", "regulated", "sec", "cftc", "treasury", "yields",
            "yield", "stocks", "equities", "bonds", "bond",
        },
        "phrases": [
            "interest rate", "rate cut", "rate cuts", "rate hike", "rate hikes",
            "balance sheet", "spot etf", "spot etfs", "spot btc etf",
            "spot eth etf", "fed pivot", "soft landing", "hard landing",
            "global liquidity", "monetary policy", "fiscal policy",
            "treasury yields", "10 year", "10-year", "two year", "2-year",
            "btc as collateral", "btc as macro", "store of value",
        ],
    },
}


def _tokenise(text: str) -> set[str]:
    """Return the set of lower-cased word tokens in `text`."""
    return {m.group(0).lower() for m in _WORD_RE.finditer(text)}


def classify_tweet(text: str) -> set[str]:
    """Return the set of class ids that this tweet matches.

    A tweet may be matched to zero, one or several classes. We don't try
    to pick a single best class per tweet — counts are aggregated across
    the user's whole sample.
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
    """Returns the stable list of class ids (excluding "general")."""
    return list(CLASSES.keys())
