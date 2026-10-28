# Scrob, Scrunchee fork

This is a fork of [ellite/scrob](https://github.com/ellite/scrob) (GPL-3). It tracks upstream and adds a small set of API endpoints and web pages used by [Scrunchee](https://github.com/migingermohawk/scrunchee), a Jellyfin-first media app, and by other clients that talk to Scrob with an API key. Everything else is upstream Scrob, unchanged.

Upstream version tracked: **v2.34.2**.

## What it adds

Each change is its own small branch cut from upstream `main`, so it can be offered upstream on its own.

| Change | What | Offered upstream |
|---|---|---|
| API key auth | `GET /auth/me`, comment create/edit/delete/list and `PATCH /profile/me` accept an API key as well as a JWT | not yet |
| Follow lists | `GET /profile/{id}/following-list` and `/followers-list`, paginated | not yet |
| Watch state | `DELETE /history/drop/show` by TMDB id; `GET /history/watched-ids`; `GET /media/{type}/{tmdb_id}/watchers` | not yet |
| Rating lookup | `GET /profile/{user_id}/rating` for one person's rating of one title | not yet |
| Rating insights | `rating_distribution_movies` and `_shows` on the stats; the person's own rating on their recent comments | not yet |
| Recommendations | `GET /media/recommended`, seeded from what the person collected and rated | not yet |
| People pages (fork only) | search without a query, `is_follower`, a People directory, full Following and Followers pages, Discover in the main navigation | no |

## Running it

Use `docker-compose.scrunchee.yml`. It is the standard compose file with the image and the secrets taken from the environment:

```
SCROB_IMAGE=ghcr.io/<owner>/scrob:scrunchee
POSTGRES_PASSWORD=<a password>
SECRET_KEY=<openssl rand -hex 32>
docker compose -f docker-compose.scrunchee.yml up -d
```

Scrob migrates its database on start, and a newer upstream may add migrations that cannot be undone, so take a `pg_dump` before switching an existing install to this image, and try it first on a copy.

## Testing

```
cd backend
python -m pytest tests                 # the whole suite, on in-memory SQLite (about 40 seconds)
pip install pgserver
./run_postgres_tests.sh                # the fork's own tests, on a real PostgreSQL
```

The suite uses SQLite because upstream's tests do. SQLite is not PostgreSQL, so `run_postgres_tests.sh` runs the fork's tests again on a real PostgreSQL 16 (bundled by `pgserver`, no Docker needed) whose schema is built from the Alembic migrations. Some of upstream's own SQLite-based tests rely on SQLite not checking foreign keys and do not pass there, so only the fork's tests are run this way.

## The image

`.github/workflows/scrunchee-image.yml` builds the image on GitHub and publishes it to the GitHub container registry as `ghcr.io/<owner>/scrob:scrunchee` (the latest build of the `scrunchee` branch), `:sha-<commit>` (that exact commit) and, for a tag named `scrunchee-v*`, a tag of that name. It runs the backend tests and the undefined-names check first, builds `linux/amd64` from the standard `Dockerfile`, and signs in with the built-in `GITHUB_TOKEN`, so no password is stored. The version shown on Scrob's About page is the upstream release it is based on plus the commit, for example `v2.34.2-scrunchee.1a2b3c4`.

One-time setup on the GitHub fork:

1. Push `main`, `scrunchee` and the upstream tags (`git push origin --tags`; the version comes from the latest `v*` tag).
2. In the fork's Actions page, disable upstream's own workflows by file name (`docker-x64.yml`, `docker.yml`, `release.yml`), or they run on the fork as well.
3. After the first run, open the package (Packages in the repository sidebar) and either make it public or give the machine that pulls it a read-only access token.

## Branches

- `main` is a copy of upstream `main`, only ever fast-forwarded.
- `scrunchee` is `main` plus the changes above. The image is built from it.

## About this fork

The changes were written with Claude Code (Anthropic), tested against a real instance and reviewed by the maintainer of this fork. Commits carry a `Co-Authored-By` line. Credit for Scrob itself goes to [ellite](https://github.com/ellite) and its contributors.
