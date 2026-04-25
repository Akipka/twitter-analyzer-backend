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

import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from flask import Flask, jsonify
from flask_cors import CORS


# ── Config ──────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("TWITTERAPI_IO_KEY", "").strip()
API_BASE = "https://api.twitterapi.io"

# Hard cap to keep cost predictable: ~$0.075 per analysis at $0.15/1k tweets.
MAX_TWEETS = int(os.environ.get("MAX_TWEETS", "500"))

# Window for stats (days). Older tweets are still fetched but ignored in stats.
WINDOW_DAYS = int(os.environ.get("WINDOW_DAYS", "30"))

# Per-request HTTP timeout to twitterapi.io.
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "20"))

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


def fetch_profile(username: str) -> dict[str, Any] | None:
    """Return raw `data` object from twitterapi.io profile endpoint, or None."""
    r = requests.get(
        f"{API_BASE}/twitter/user/info",
        params={"userName": username},
        headers=_headers(),
        timeout=HTTP_TIMEOUT,
    )
    if r.status_code != 200:
        log.warning("profile %s: HTTP %s %s", username, r.status_code, r.text[:200])
        return None
    payload = r.json()
    if payload.get("status") != "success":
        log.warning("profile %s: %s", username, payload.get("msg"))
        return None
    return payload.get("data") or None


def fetch_tweets(user_id: str, username: str, max_tweets: int) -> list[dict[str, Any]]:
    """Page through last_tweets including replies, capped at max_tweets."""
    out: list[dict[str, Any]] = []
    cursor = ""
    pages = 0
    while True:
        r = requests.get(
            f"{API_BASE}/twitter/user/last_tweets",
            params={
                "userId": user_id,
                "userName": username,
                "cursor": cursor,
                "includeReplies": "true",
            },
            headers=_headers(),
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            log.warning("tweets %s: HTTP %s %s", username, r.status_code, r.text[:200])
            break
        payload = r.json()
        if payload.get("status") != "success":
            log.warning("tweets %s: %s", username, payload.get("message"))
            break
        batch = payload.get("tweets") or []
        out.extend(batch)
        pages += 1
        if len(out) >= max_tweets:
            out = out[:max_tweets]
            break
        if not payload.get("has_next_page"):
            break
        cursor = payload.get("next_cursor") or ""
        if not cursor:
            break
        # safety: don't loop forever
        if pages >= 60:
            break
    return out


# ── Stats computation ───────────────────────────────────────────────────────


WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def compute_stats(tweets: list[dict[str, Any]], window_days: int) -> dict[str, Any]:
    """Compute activity stats for the report card.

    Window = last `window_days` days. Older tweets are excluded from per-window
    counters but still affect overall ratios when window is empty.
    """
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=window_days)

    in_window: list[dict[str, Any]] = []
    for t in tweets:
        dt = _parse_twitter_date(t.get("createdAt", ""))
        if dt is not None and dt >= cutoff:
            in_window.append(t)

    # If there's literally nothing in window, still let frontend render the
    # "expelled" verdict — but we report counts anyway.
    sample = in_window if in_window else tweets
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

    # Days span actually observed (min(window, observed range)).
    if earliest and latest:
        days_span = max((latest - earliest).total_seconds() / 86400.0, 1.0)
    else:
        days_span = float(window_days)
    days_span = min(days_span, float(window_days))

    activity_per_day = total / days_span if days_span > 0 else 0.0

    return {
        "window_days": window_days,
        "in_window": bool(in_window),
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


# ── Routes ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True, "has_key": bool(API_KEY)})


@app.get("/api/analyze/<username>")
def analyze(username: str) -> Any:
    started = time.time()
    username = username.strip().lstrip("@")
    if not USERNAME_RE.match(username):
        return jsonify({"error": "invalid_username", "message": "Username must be 1–15 alphanumeric/underscore characters."}), 400
    if not API_KEY:
        return jsonify({"error": "missing_key", "message": "TWITTERAPI_IO_KEY not configured on the server."}), 500

    profile_raw = fetch_profile(username)
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
    stats = compute_stats(tweets, WINDOW_DAYS)

    elapsed = round(time.time() - started, 2)
    log.info("analyze %s: %d tweets, %.2fs", username, len(tweets), elapsed)

    return jsonify({
        "profile": shape_profile(profile_raw),
        "stats": stats,
        "elapsed": elapsed,
    })


# ── Entrypoint ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
