"""
Twitter Report Card — Backend API.

Fetches user profile and recent tweets from twitterapi.io, computes activity
stats, and returns JSON for the frontend to render the report card.

Endpoints:
  GET /health                 → liveness check
  GET /api/analyze/<username> → profile + stats + raw counts (no grading)

Grading is done on the frontend so it can be tweaked without redeploying
the backend.

Run locally:  python server.py
On Render:    gunicorn server:app
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, jsonify
from flask_cors import CORS

import classify
import classmates


# ── Config ──────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
API_BASE = "https://api.twitterapi.io"

# Hard cap on tweets per analysis. twitterapi.io returns ~20 tweets per page.
# We grab the most recent MAX_TWEETS regardless of date — a count-based window.
# 30 is the default because it's one classroom-sized sample that keeps every
# analysis request well inside Render's 30s gunicorn timeout (1–3s on paid tier).
MAX_TWEETS = int(os.environ.get("MAX_TWEETS", "30"))

# Per-request HTTP timeout to twitterapi.io.
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "20"))

# Paid twitterapi.io plans don't have a cooldown. Free tier enforced 1/5s.
# We send at most 2 page requests per analyze, so even on free this stays quick.
MIN_REQUEST_INTERVAL = float(os.environ.get("MIN_REQUEST_INTERVAL", "0.1"))

# When DEMO_FALLBACK=true, return deterministic synthetic data if the upstream
# API is out of credits or unauthorized. Useful for previewing the UI without
# spending money.
DEMO_FALLBACK = os.environ.get("DEMO_FALLBACK", "false").lower() in ("1", "true", "yes")

# In-process cache for /api/analyze responses. The same username is served
# from the cache for ANALYZE_CACHE_TTL seconds (default 24h). Drastically
# reduces twitterapi.io spend for popular profiles. Pass ?nocache=1 to bypass.
ANALYZE_CACHE_TTL = int(os.environ.get("ANALYZE_CACHE_TTL", str(60 * 60 * 24)))
ANALYZE_CACHE_MAX = int(os.environ.get("ANALYZE_CACHE_MAX", "5000"))

CORS_ORIGINS = os.environ.get("CORS_ORIGINS", "*")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("report-card")


# ── App ────────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app, origins=CORS_ORIGINS.split(",") if CORS_ORIGINS != "*" else "*")


# ── Helpers ────────────────────────────────────────────────────────────────


def _headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY, "Accept": "application/json"}


_last_request_at: float = 0.0

# When the upstream rejects us for billing/auth reasons, remember that for a
# while so we don't waste the 5s rate-limit delay (and a network round trip)
# on every subsequent request. Cleared after UPSTREAM_FAIL_TTL seconds.
UPSTREAM_FAIL_TTL = 60.0
_upstream_block_until: float = 0.0
_upstream_block_code: str = ""


def _rate_limited_get(url: str, params: dict[str, Any]) -> requests.Response:
    """GET that respects MIN_REQUEST_INTERVAL and retries once on HTTP 429.

    If we recently saw an out-of-credits / auth error, short-circuit by raising
    the cached error instead of paying for the rate-limit wait.
    """
    global _last_request_at, _upstream_block_until, _upstream_block_code
    if _upstream_block_until > time.time() and _upstream_block_code:
        if _upstream_block_code == "out_of_credits":
            raise UpstreamError(
                "out_of_credits",
                "twitterapi.io says the API account is out of credits. Top up at twitterapi.io.",
                http_status=402,
            )
        if _upstream_block_code == "upstream_auth":
            raise UpstreamError(
                "upstream_auth",
                "twitterapi.io rejected the API key. Check TWITTERAPI_IO_KEY on the server.",
                http_status=502,
            )
    wait = MIN_REQUEST_INTERVAL - (time.time() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)
    _last_request_at = time.time()
    if r.status_code == 429:
        # backoff a little longer than the documented interval, retry once
        time.sleep(MIN_REQUEST_INTERVAL + 1.0)
        r = requests.get(url, params=params, headers=_headers(), timeout=HTTP_TIMEOUT)
        _last_request_at = time.time()
    if r.status_code == 402:
        _upstream_block_until = time.time() + UPSTREAM_FAIL_TTL
        _upstream_block_code = "out_of_credits"
    elif r.status_code in (401, 403):
        _upstream_block_until = time.time() + UPSTREAM_FAIL_TTL
        _upstream_block_code = "upstream_auth"
    return r


def _parse_twitter_date(s: str) -> datetime | None:
    """Parse 'Thu Dec 13 08:41:26 +0000 2007' or ISO-ish formats."""
    if not s:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


class UpstreamError(Exception):
    """Raised when twitterapi.io returns an error we want to forward."""

    def __init__(self, code: str, message: str, http_status: int = 502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def fetch_profile(username: str) -> dict[str, Any] | None:
    """Return raw `data` object from twitterapi.io profile endpoint.

    Returns None on a clean 404 ("user does not exist"). Raises UpstreamError
    for other failures (out of credits, auth, server errors) so the caller can
    surface a useful message instead of pretending the user doesn't exist.
    """
    r = _rate_limited_get(f"{API_BASE}/twitter/user/info", {"userName": username})
    if r.status_code == 402:
        raise UpstreamError(
            "out_of_credits",
            "twitterapi.io says the API account is out of credits. Top up at twitterapi.io.",
            http_status=402,
        )
    if r.status_code in (401, 403):
        raise UpstreamError(
            "upstream_auth",
            "twitterapi.io rejected the API key. Check TWITTERAPI_IO_KEY on the server.",
            http_status=502,
        )
    if r.status_code == 429:
        raise UpstreamError(
            "rate_limited",
            "twitterapi.io rate limit hit. Try again in a few seconds.",
            http_status=429,
        )
    if r.status_code != 200:
        log.warning("profile %s: HTTP %s %s", username, r.status_code, r.text[:200])
        raise UpstreamError(
            "upstream_error",
            f"twitterapi.io returned HTTP {r.status_code}.",
            http_status=502,
        )
    payload = r.json()
    if payload.get("status") != "success":
        log.warning("profile %s: %s", username, payload.get("msg"))
        return None
    return payload.get("data") or None


def fetch_tweets(
    user_id: str,
    username: str,
    max_tweets: int,
) -> list[dict[str, Any]]:
    """Pull the most recent ``max_tweets`` tweets (including replies).

    twitterapi.io returns ~20 tweets per page, so we fetch 1–2 pages max and
    stop as soon as we have enough. No date filtering — a user who posts once
    a month contributes their last 30 tweets over ~2.5 years, a firehose user
    over a single day. Downstream grading normalises by the observed span.
    """
    out: list[dict[str, Any]] = []
    cursor = ""
    pages = 0
    # Safety: at most 3 page fetches even if the API is being stingy.
    while len(out) < max_tweets and pages < 3:
        r = _rate_limited_get(
            f"{API_BASE}/twitter/user/last_tweets",
            {
                "userId": user_id,
                "userName": username,
                "cursor": cursor,
                "includeReplies": "true",
            },
        )
        if r.status_code != 200:
            log.warning("tweets %s: HTTP %s %s", username, r.status_code, r.text[:200])
            break
        payload = r.json()
        # The /twitter/user/last_tweets endpoint returns {tweets, has_next_page,
        # next_cursor} directly (no `status: success` wrapper), unlike
        # /twitter/user/info. We only treat the response as an error if a
        # status field is explicitly present AND set to something other than
        # success — otherwise we just take whatever tweets came back.
        status = payload.get("status")
        if status and status != "success":
            log.warning("tweets %s: %s", username, payload.get("msg") or payload.get("message"))
            break
        # `tweets` may live at the top level (per the OpenAPI spec) or under
        # `data` (older response shape); accept both.
        data = payload.get("data") or {}
        batch = data.get("tweets") or payload.get("tweets") or []
        if not batch:
            # Empty page → nothing more to fetch.
            log.info("tweets %s: empty page (page=%s, keys=%s)", username, pages, list(payload.keys()))
            break
        out.extend(batch)
        pages += 1
        if len(out) >= max_tweets:
            break
        # has_next_page may live at top level or under `data`.
        has_next = payload.get("has_next_page")
        if has_next is None:
            has_next = data.get("has_next_page")
        if not has_next:
            break
        cursor = payload.get("next_cursor") or data.get("next_cursor") or ""
        if not cursor:
            break
    return out[:max_tweets]


# ── Stats computation ───────────────────────────────────────────────────────


WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def compute_stats(tweets: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute activity stats for the report card.

    The sample is ``tweets`` as returned by ``fetch_tweets`` — i.e. the user's
    most recent ~30 tweets. Time window is implicit: we report ``days_span``
    (elapsed time between the oldest and newest tweet in the sample) so the
    frontend can normalise rates without any hardcoded window.
    """
    sample = tweets
    posts = 0
    replies = 0
    likes = 0
    retweets = 0
    views = 0
    quotes = 0
    reply_counts = 0
    text_lens: list[int] = []
    word_lens: list[int] = []
    all_words: list[str] = []
    earliest: datetime | None = None
    latest: datetime | None = None

    for t in sample:
        is_reply = bool(t.get("isReply") or t.get("inReplyToId") or t.get("inReplyToUserId"))
        if is_reply:
            replies += 1
        else:
            posts += 1
        likes += int(t.get("likeCount") or 0)
        retweets += int(t.get("retweetCount") or 0)
        views += int(t.get("viewCount") or 0)
        quotes += int(t.get("quoteCount") or 0)
        reply_counts += int(t.get("replyCount") or 0)
        text = (t.get("text") or "").strip()
        if text:
            text_lens.append(len(text))
            words = WORD_RE.findall(text.lower())
            word_lens.append(len(words))
            all_words.extend(w for w in words if len(w) > 3)
        dt = _parse_twitter_date(t.get("createdAt", ""))
        if dt:
            if earliest is None or dt < earliest:
                earliest = dt
            if latest is None or dt > latest:
                latest = dt

    total = posts + replies
    n = max(total, 1)

    # Lexical diversity = unique non-trivial words / total such words.
    unique_word_ratio = (len(set(all_words)) / max(len(all_words), 1)) if all_words else 0.0
    avg_chars = sum(text_lens) / n if text_lens else 0.0
    avg_words = sum(word_lens) / n if word_lens else 0.0

    avg_likes = likes / n
    avg_rt = retweets / n
    avg_views = views / n
    avg_quotes = quotes / n
    avg_replies_received = reply_counts / n

    # Engagement = likes + 2*RT + 3*quote + 0.5*replies_received per tweet.
    engagement = avg_likes + 2 * avg_rt + 3 * avg_quotes + 0.5 * avg_replies_received

    # Days span actually observed. Floor at 1.0 to avoid div-by-zero-ish
    # spikes (30 tweets posted in 10 minutes shouldn't read as 4320/day).
    if earliest and latest:
        days_span = max((latest - earliest).total_seconds() / 86400.0, 1.0)
    else:
        days_span = 1.0

    activity_per_day = total / days_span if days_span > 0 else 0.0
    posts_per_day = posts / days_span if days_span > 0 else 0.0
    replies_per_day = replies / days_span if days_span > 0 else 0.0

    return {
        # Kept for backward compatibility with older clients. Reflects the
        # observed span rounded up — not a configured 30-day window.
        "window_days": int(round(days_span)),
        "in_window": True,
        "tweets_analyzed": len(sample),
        "posts": posts,
        "replies": replies,
        "total": total,
        "avg_likes": round(avg_likes, 2),
        "avg_retweets": round(avg_rt, 2),
        "avg_views": round(avg_views, 0),
        "avg_quotes": round(avg_quotes, 2),
        "avg_replies_received": round(avg_replies_received, 2),
        "engagement_score": round(engagement, 2),
        "avg_chars": round(avg_chars, 1),
        "avg_words": round(avg_words, 1),
        "unique_word_ratio": round(unique_word_ratio, 3),
        "activity_per_day": round(activity_per_day, 2),
        "posts_per_day": round(posts_per_day, 2),
        "replies_per_day": round(replies_per_day, 2),
        "days_span": round(days_span, 1),
        "earliest": earliest.isoformat() if earliest else None,
        "latest": latest.isoformat() if latest else None,
    }


def shape_profile(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        "userName": p.get("userName"),
        "displayName": p.get("name") or p.get("userName"),
        "avatarUrl": p.get("profilePicture") or "",
        "coverUrl": p.get("coverPicture") or "",
        "description": p.get("description") or "",
        "location": p.get("location") or "",
        "followers": int(p.get("followers") or 0),
        "following": int(p.get("following") or 0),
        "statusesCount": int(p.get("statusesCount") or 0),
        "isBlueVerified": bool(p.get("isBlueVerified")),
        "createdAt": p.get("createdAt"),
        "url": p.get("url") or f"https://x.com/{p.get('userName', '')}",
    }


# ── Demo data (used when upstream is out of credits) ───────────────────────


def _demo_avatar_url(username: str) -> str:
    # unavatar.io fetches the public Twitter/X avatar by handle (no auth needed)
    # and falls back to a generic placeholder if the user doesn't exist.
    return f"https://unavatar.io/twitter/{username}?fallback=https%3A%2F%2Fabs.twimg.com%2Fsticky%2Fdefault_profile_images%2Fdefault_profile_400x400.png"


def _seeded_rng(username: str) -> random.Random:
    seed = int(hashlib.sha256(username.lower().encode("utf-8")).hexdigest()[:12], 16)
    return random.Random(seed)


def demo_response(username: str) -> dict[str, Any]:
    """Build a deterministic fake profile + stats so the UI can be evaluated
    without burning twitterapi.io credits. Different usernames yield
    different (but stable) report cards.

    Mirrors the real pipeline: we pretend to have pulled the user's last
    ``MAX_TWEETS`` tweets and spread them over a random span (from an hour-long
    firehose to a multi-month lurker).
    """
    rng = _seeded_rng(username)
    followers = rng.choice([42, 230, 1_400, 8_900, 41_000, 220_000, 1_300_000])
    statuses = rng.randint(120, 50_000)
    # Total is pinned at MAX_TWEETS (the real flow is count-limited).
    total = MAX_TWEETS
    reply_share = rng.uniform(0.1, 0.75)
    replies = int(round(total * reply_share))
    posts = total - replies
    # Span over which the 30 tweets were posted. Low end = frequent poster,
    # high end = casual lurker whose last 30 tweets stretch back ~7 months.
    days_span = round(rng.uniform(0.5, 220.0), 1)
    avg_likes = round(rng.uniform(0.5, max(2.0, followers / 4000)), 2)
    avg_rt = round(avg_likes * rng.uniform(0.05, 0.18), 2)
    avg_views = round(avg_likes * rng.uniform(40, 220), 0)
    avg_quotes = round(avg_likes * rng.uniform(0.01, 0.05), 2)
    avg_replies = round(avg_likes * rng.uniform(0.05, 0.4), 2)
    avg_words = round(rng.uniform(6.0, 38.0), 1)
    unique_ratio = round(rng.uniform(0.32, 0.82), 3)
    activity_per_day = round(total / days_span, 2)
    posts_per_day = round(posts / days_span, 2)
    replies_per_day = round(replies / days_span, 2)
    engagement = round(avg_likes + 2 * avg_rt + 3 * avg_quotes + 0.5 * avg_replies, 2)
    profile = {
        "id": str(abs(hash(username)) % 10**18),
        "userName": username,
        "displayName": username.replace("_", " ") + " (demo)",
        "avatarUrl": _demo_avatar_url(username),
        "coverUrl": "",
        "description": "Synthetic profile — demo mode.",
        "location": "",
        "followers": followers,
        "following": rng.randint(50, 2000),
        "statusesCount": statuses,
        "isBlueVerified": rng.random() < 0.25,
        "createdAt": "2018-01-01T00:00:00.000000Z",
        "url": f"https://x.com/{username}",
    }
    stats = {
        "window_days": int(round(days_span)),
        "in_window": True,
        "tweets_analyzed": total,
        "posts": posts,
        "replies": replies,
        "total": total,
        "avg_likes": avg_likes,
        "avg_retweets": avg_rt,
        "avg_views": avg_views,
        "avg_quotes": avg_quotes,
        "avg_replies_received": avg_replies,
        "engagement_score": engagement,
        "avg_chars": round(avg_words * 6.2, 1),
        "avg_words": avg_words,
        "unique_word_ratio": unique_ratio,
        "activity_per_day": activity_per_day,
        "posts_per_day": posts_per_day,
        "replies_per_day": replies_per_day,
        "days_span": days_span,
        "earliest": None,
        "latest": None,
    }
    # Pick a deterministic class based on the username hash, so the demo UI
    # has something interesting to show for the classroom feature without
    # actually running the keyword classifier on real text.
    class_ids = list(classify.CLASSES.keys())
    primary = rng.choice(class_ids + ["general"])  # 1/(N+1) chance of "general"
    if primary == "general":
        primary_label = "General Studies"
        primary_emoji = "🎓"
        primary_blurb = "Posts a bit of everything — no clear specialty yet."
    else:
        spec = classify.CLASSES[primary]
        primary_label = spec["label"]
        primary_emoji = spec["emoji"]
        primary_blurb = spec["blurb"]

    # Synthesise a plausible breakdown: primary gets a chunky share, the rest
    # split a long tail. Always sums to 1.0.
    raw_shares = {cid: rng.uniform(0.05, 0.20) for cid in class_ids}
    if primary != "general":
        raw_shares[primary] = rng.uniform(0.40, 0.65)
    s = sum(raw_shares.values())
    breakdown = sorted(
        ({
            "id": cid,
            "label": classify.CLASSES[cid]["label"],
            "emoji": classify.CLASSES[cid]["emoji"],
            "share": round(raw_shares[cid] / s, 3),
            "hits": int(raw_shares[cid] / s * total) if total else 0,
        } for cid in class_ids),
        key=lambda x: -x["share"],
    )
    classification = {
        "primary": primary,
        "label": primary_label,
        "emoji": primary_emoji,
        "blurb": primary_blurb,
        "confidence": round(raw_shares.get(primary, 0.0) / s, 3) if primary != "general" else 0.0,
        "breakdown": breakdown,
        "tweets_classified": int(total * 0.6),
        "tweets_total": total,
    }
    classmates.add_member(primary, username, profile["displayName"])

    return {
        "profile": profile,
        "stats": stats,
        "classification": classification,
        "elapsed": 0.0,
        "demo": True,
    }


# ── Routes ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> Any:
    return jsonify({
        "ok": True,
        "has_key": bool(API_KEY),
        "demo_fallback": DEMO_FALLBACK,
        "cache_size": len(_ANALYZE_CACHE),
        "cache_ttl_seconds": ANALYZE_CACHE_TTL,
    })


# Per-username analyze cache. Maps lower(username) → (cached_at, payload).
# Only successful real (non-demo) responses are cached.
_ANALYZE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_get(username: str) -> dict[str, Any] | None:
    entry = _ANALYZE_CACHE.get(username.lower())
    if not entry:
        return None
    cached_at, payload = entry
    if time.time() - cached_at > ANALYZE_CACHE_TTL:
        _ANALYZE_CACHE.pop(username.lower(), None)
        return None
    age = int(time.time() - cached_at)
    out = dict(payload)
    out["cached"] = True
    out["cached_age_seconds"] = age
    return out


def _cache_put(username: str, payload: dict[str, Any]) -> None:
    if len(_ANALYZE_CACHE) >= ANALYZE_CACHE_MAX:
        oldest = min(_ANALYZE_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _ANALYZE_CACHE.pop(oldest, None)
    _ANALYZE_CACHE[username.lower()] = (time.time(), payload)


# Tiny in-process LRU cache for avatar bytes. Avatars rarely change, and the
# upstream (unavatar.io) is rate-limited / occasionally slow. Cache size and
# TTL are intentionally small so memory stays bounded on Render's free tier.
_AVATAR_CACHE: dict[str, tuple[float, bytes, str]] = {}
_AVATAR_CACHE_MAX = 256
_AVATAR_CACHE_TTL = 60 * 60 * 6  # 6 hours


@app.get("/api/avatar/<username>")
def avatar(username: str) -> Any:
    """Proxy a user's X profile avatar with proper CORS so the frontend can
    embed it in html2canvas-rendered images. Falls back to a 1x1 transparent
    PNG on failure so the frontend's local initials avatar can take over."""
    from flask import Response

    username = username.strip().lstrip("@").lower()
    if not USERNAME_RE.match(username):
        return jsonify({"error": "invalid_username"}), 400

    now = time.time()
    cached = _AVATAR_CACHE.get(username)
    if cached and now - cached[0] < _AVATAR_CACHE_TTL:
        _, body, content_type = cached
        return Response(
            body,
            mimetype=content_type,
            headers={
                "Cache-Control": "public, max-age=21600",
                "Access-Control-Allow-Origin": "*",
            },
        )

    upstream = f"https://unavatar.io/twitter/{username}"
    try:
        r = requests.get(upstream, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code != 200 or not r.content:
            raise RuntimeError(f"unavatar status {r.status_code}")
        content_type = r.headers.get("Content-Type", "image/png").split(";")[0].strip()
        if not content_type.startswith("image/"):
            content_type = "image/png"
        body = r.content
    except Exception as exc:  # noqa: BLE001
        # Any upstream error → 404 so the frontend's onError fires and the
        # local initials-avatar takes over.
        log.info("avatar proxy upstream error for %s: %s", username, exc)
        return Response(
            "",
            status=404,
            headers={"Access-Control-Allow-Origin": "*"},
        )

    if len(_AVATAR_CACHE) >= _AVATAR_CACHE_MAX:
        # cheap eviction: drop the oldest entry
        oldest = min(_AVATAR_CACHE.items(), key=lambda kv: kv[1][0])[0]
        _AVATAR_CACHE.pop(oldest, None)
    _AVATAR_CACHE[username] = (now, body, content_type)

    return Response(
        body,
        mimetype=content_type,
        headers={
            "Cache-Control": "public, max-age=21600",
            "Access-Control-Allow-Origin": "*",
        },
    )


@app.get("/api/classmates/<class_id>")
def classmates_route(class_id: str) -> Any:
    """Return the roster for a class.

    Includes the seed celebrities plus any users who have actually been
    classified into this class on this server instance. The frontend uses
    /api/avatar/<username> to load each member's avatar — no avatar data
    is shipped here.
    """
    cid = class_id.strip().lower()
    valid_classes = set(classify.all_class_ids())
    if cid not in valid_classes:
        return jsonify({
            "error": "unknown_class",
            "message": f"Unknown class '{class_id}'. Valid: {sorted(valid_classes)}",
        }), 404

    if cid == "general":
        label = "General Studies"
        emoji = "🎓"
    else:
        spec = classify.CLASSES[cid]
        label = spec["label"]
        emoji = spec["emoji"]

    return jsonify({
        "class": cid,
        "label": label,
        "emoji": emoji,
        "members": classmates.get_roster(cid),
        "size_cap": classmates.CLASS_SIZE,
    })


@app.get("/api/analyze/<username>")
def analyze(username: str) -> Any:
    from flask import request

    started = time.time()
    username = username.strip().lstrip("@")
    if not USERNAME_RE.match(username):
        return jsonify({"error": "invalid_username", "message": "Username must be 1–15 alphanumeric/underscore characters."}), 400
    if not API_KEY:
        return jsonify({"error": "missing_key", "message": "TWITTERAPI_IO_KEY not configured on the server."}), 500

    nocache = request.args.get("nocache", "").lower() in ("1", "true", "yes")
    if not nocache:
        cached = _cache_get(username)
        if cached is not None:
            log.info("cache hit %s (age=%ss)", username, cached.get("cached_age_seconds"))
            return jsonify(cached)

    try:
        profile_raw = fetch_profile(username)
    except UpstreamError as exc:
        if DEMO_FALLBACK and exc.code in ("out_of_credits", "upstream_auth"):
            log.info("demo fallback for %s (reason: %s)", username, exc.code)
            # Demo responses are NOT cached — they're synthetic and free anyway.
            return jsonify(demo_response(username))
        return jsonify({"error": exc.code, "message": exc.message}), exc.http_status
    if not profile_raw:
        return jsonify({"error": "not_found", "message": f"User @{username} not found, suspended, or private."}), 404

    if profile_raw.get("unavailable"):
        return jsonify({
            "error": "unavailable",
            "message": profile_raw.get("message") or "Account is unavailable.",
            "profile": shape_profile(profile_raw),
        }), 200

    user_id = profile_raw.get("id") or ""
    tweets = fetch_tweets(user_id, username, MAX_TWEETS)
    stats = compute_stats(tweets)

    # Classify the user into a Crypto Twitter class based on tweet text.
    # Pure keyword counting — no external calls, deterministic, free.
    classification = classify.classify_user(
        (t.get("text") or "") for t in tweets
    )

    profile_shaped = shape_profile(profile_raw)
    classmates.add_member(
        classification["primary"],
        profile_shaped["userName"] or username,
        profile_shaped["displayName"] or username,
    )

    elapsed = round(time.time() - started, 2)
    log.info(
        "analyze %s: %d tweets, class=%s (%.0f%%), %.2fs",
        username, len(tweets), classification["primary"],
        100 * classification["confidence"], elapsed,
    )

    payload = {
        "profile": profile_shaped,
        "stats": stats,
        "classification": classification,
        "elapsed": elapsed,
    }
    _cache_put(username, payload)
    return jsonify(payload)


# ── Entrypoint ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
