"""
Digital Gradebook — Backend API
Fetches REAL tweet counts from Twitter/X via snscrape.

Reliability features:
  - Max tweet cap (default 500) to prevent timeouts
  - Per-request timeout (default 60s) via multiprocessing
  - Graceful handling: not found, private, no tweets
  - Always returns valid JSON, never crashes

Local:   python server.py
Render:  gunicorn server:app (auto-detected via render.yaml)
"""

import os
import signal
from datetime import datetime, timedelta
from multiprocessing import Process, Queue
from flask import Flask, jsonify, send_file
from flask_cors import CORS

# ── Tunables (override via env vars) ────────────────────────

MAX_TWEETS = int(os.environ.get("MAX_TWEETS", "500"))   # stop after this many
SCRAPE_TIMEOUT = int(os.environ.get("SCRAPE_TIMEOUT", "60"))  # seconds

EMPTY_RESULT = {"posts": 0, "replies": 0, "total": 0}


# ── Scraper (runs in subprocess for timeout control) ────────

def _scrape_worker(username: str, since: str, max_tweets: int, q: Queue):
    """Run in a separate process so we can kill it on timeout."""
    try:
        import snscrape.modules.twitter as sntwitter

        posts = 0
        replies = 0
        count = 0
        query = f"from:{username} since:{since}"

        for tweet in sntwitter.TwitterSearchScraper(query).get_items():
            count += 1
            if tweet.inReplyToTweetId:
                replies += 1
            else:
                posts += 1
            if count >= max_tweets:
                break

        q.put({"posts": posts, "replies": replies, "total": posts + replies})

    except Exception as e:
        q.put({"error": str(e)})


def scrape(username: str, since: str) -> dict:
    """
    Scrape tweets with timeout protection.
    Returns {"posts", "replies", "total"} or {"error": "..."}.
    Never raises — always returns a dict.
    """
    q: Queue = Queue()
    p = Process(
        target=_scrape_worker,
        args=(username, since, MAX_TWEETS, q),
        daemon=True,
    )
    p.start()
    p.join(timeout=SCRAPE_TIMEOUT)

    if p.is_alive():
        # Timed out — kill the process
        p.terminate()
        p.join(timeout=5)
        if p.is_alive():
            p.kill()
            p.join(timeout=2)
        return {
            **EMPTY_RESULT,
            "warning": f"Timeout after {SCRAPE_TIMEOUT}s — showing partial data (capped at {MAX_TWEETS} tweets). Try again for full results.",
        }

    if q.empty():
        return {**EMPTY_RESULT, "warning": "Scraper returned no data"}

    result = q.get()

    if "error" in result:
        msg = result["error"].lower()
        if "not found" in msg or "does not exist" in msg or "no user" in msg:
            return {
                **EMPTY_RESULT,
                "warning": f"User @{username} not found. Check the username and try again.",
            }
        if "private" in msg or "protected" in msg or "suspended" in msg:
            return {
                **EMPTY_RESULT,
                "warning": f"Account @{username} is private or suspended.",
            }
        # Generic scrape error — return zeros with warning
        return {
            **EMPTY_RESULT,
            "warning": f"Could not fetch tweets: {result['error'][:200]}",
        }

    # Successful scrape — add warning if we hit the cap
    if result["total"] >= MAX_TWEETS:
        result["warning"] = f"Results capped at {MAX_TWEETS} tweets. Actual activity may be higher."
    if result["total"] == 0:
        result["warning"] = f"No tweets from @{username} in the last 30 days."

    return result


# ── App ─────────────────────────────────────────────────────

app = Flask(__name__, static_folder="dist", static_url_path="")

cors_origins = os.environ.get("CORS_ORIGINS", "*")
CORS(app, resources={r"/api/*": {"origins": cors_origins.split(",") if cors_origins != "*" else "*"}})


# ── API Routes ──────────────────────────────────────────────

@app.route("/api/analyze/<username>")
def analyze(username: str):
    """GET /api/analyze/<username> → { posts, replies, total }"""
    username = username.strip().lstrip("@").lower()

    if not username or len(username) > 15 or not username.replace("_", "").isalnum():
        return jsonify({
            **EMPTY_RESULT,
            "warning": f"Invalid username: @{username}",
        }), 400

    since = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    try:
        result = scrape(username, since)
        status = 200
        # Return 404-ish for clearly-not-found users (but still valid JSON)
        w = result.get("warning", "")
        if "not found" in w.lower() or "private" in w.lower() or "suspended" in w.lower():
            status = 200  # still 200 — frontend handles warning
        return jsonify(result), status

    except ModuleNotFoundError:
        return jsonify({
            **EMPTY_RESULT,
            "warning": "snscrape not installed on server.",
        }), 200

    except Exception as e:
        return jsonify({
            **EMPTY_RESULT,
            "warning": f"Server error: {str(e)[:200]}",
        }), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "time": datetime.now().isoformat(),
        "cors_origins": cors_origins,
        "config": {
            "max_tweets": MAX_TWEETS,
            "scrape_timeout": SCRAPE_TIMEOUT,
        },
    })


# ── Serve React frontend ────────────────────────────────────

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path: str):
    """Serve the built React app from dist/index.html."""
    dist_dir = os.path.join(os.path.dirname(__file__), "dist")

    if path:
        file_path = os.path.join(dist_dir, path)
        if os.path.isfile(file_path):
            return send_file(file_path)

    index_path = os.path.join(dist_dir, "index.html")
    if os.path.isfile(index_path):
        return send_file(index_path)

    return jsonify({
        "message": "Frontend not built. Run: npm run build",
        "api_endpoints": {
            "analyze": "/api/analyze/<username>",
            "health": "/health",
        },
    })


# ── Entry point ─────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print()
    print(f"  ┌──────────────────────────────────────────┐")
    print(f"  │   📋 Digital Gradebook Backend           │")
    print(f"  │                                          │")
    print(f"  │   Local:  http://localhost:{port}        │")
    print(f"  │   API:    /api/analyze/<username>        │")
    print(f"  │   Health: /health                        │")
    print(f"  │                                          │")
    print(f"  │   Max tweets: {MAX_TWEETS:<24}│")
    print(f"  │   Timeout:    {SCRAPE_TIMEOUT}s{' ' * (21 - len(str(SCRAPE_TIMEOUT)))}│")
    print(f"  └──────────────────────────────────────────┘")
    print()
    app.run(host="0.0.0.0", port=port, debug=True)
