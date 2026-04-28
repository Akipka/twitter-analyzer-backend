# Twitter Report Card — Backend

Flask API that fetches a user's profile and recent tweets from
[twitterapi.io](https://twitterapi.io/) and returns activity stats for the
frontend to render the "report card".

## Endpoints

| Method | Path                       | Description                                  |
| ------ | -------------------------- | -------------------------------------------- |
| GET    | `/health`                  | Liveness check + reports whether key is set. |
| GET    | `/api/analyze/<username>`  | Profile + stats for the last `MAX_TWEETS`.   |
| GET    | `/api/classmates/<class>`  | Roster of classmates for a Crypto School class. |

## Environment variables

| Name                  | Required | Default | Notes                                      |
| --------------------- | -------- | ------- | ------------------------------------------ |
| `TWITTERAPI_IO_KEY`   | yes      | —       | Get one at <https://twitterapi.io>.        |
| `CORS_ORIGINS`        | no       | `*`     | Comma-separated list of allowed origins.   |
| `MAX_TWEETS`          | no       | `30`    | Tweets pulled per analysis (count-based window). |
| `HTTP_TIMEOUT`        | no       | `20`    | Per-request timeout to twitterapi.io.      |
| `PORT`                | no       | `5000`  | Set automatically by Render.               |

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export TWITTERAPI_IO_KEY=your-key-here
python server.py
# → http://localhost:5000/health
```

## Deploy (Render)

`render.yaml` wires this up as a free-tier web service. After connecting the
repo on Render, set `TWITTERAPI_IO_KEY` and `CORS_ORIGINS` (your Vercel URL)
in the dashboard.

## Cost

twitterapi.io charges $0.15 per 1,000 tweets and $0.18 per 1,000 profiles.
With `MAX_TWEETS=30` a single analysis costs at most ≈ $0.0063 (one profile
lookup plus ≤ 2 page fetches), and the in-memory 24h cache serves repeat
lookups for $0.
