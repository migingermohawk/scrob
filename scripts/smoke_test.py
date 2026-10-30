#!/usr/bin/env python3
"""Smoke test for the Scrunchee fork's additions, against a running Scrob.

    python3 scripts/smoke_test.py http://host:7331 <api key>
    python3 scripts/smoke_test.py http://host:7331 <api key> --write     # also posts and deletes one comment

The address is the one you open in a browser (the web server, which forwards API calls
under /api/proxy). Use --api-prefix '' to talk straight to the backend instead. The API
key is the one on your Scrob account page. Without --write it only reads, apart from one
harmless call that asks to undrop a show Scrob has never heard of.

Prints one line per check and exits 1 if any failed. Needs only Python 3.
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

CARD_KEYS = {
    "id", "username", "display_name", "avatar_url", "country", "movies_watched", "shows_watched",
    "total_collected", "total_rated", "follower_count", "is_following", "is_follower", "is_self",
}
results: list[tuple[bool, str, str]] = []


def call(base, prefix, key, method, path, params=None, body=None):
    query = urllib.parse.urlencode({**(params or {}), "api_key": key})
    url = f"{base}{prefix}{path}?{query}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode()
            return resp.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, text


def check(name, ok, detail=""):
    results.append((bool(ok), name, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (f"   {detail}" if detail and not ok else ""))


def is_page(body):
    return isinstance(body, dict) and isinstance(body.get("results"), list) and {"page", "total_pages", "total_results"} <= set(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base", help="address of the Scrob web server, e.g. http://host:7331")
    ap.add_argument("key", help="a Scrob API key")
    ap.add_argument("--api-prefix", default="/api/proxy")
    ap.add_argument("--write", action="store_true", help="also create and delete one comment")
    ap.add_argument("--skip-web", action="store_true", help="do not check the web pages (when talking straight to the backend)")
    args = ap.parse_args()
    base, prefix, key = args.base.rstrip("/"), args.api_prefix, args.key

    def api(method, path, params=None, body=None):
        return call(base, prefix, key, method, path, params, body)

    status, me = api("GET", "/auth/me")
    check("GET /auth/me works with an API key", status == 200 and isinstance(me, dict) and "id" in me, f"{status} {me}")
    if status != 200 or not isinstance(me, dict) or "id" not in me:
        print("\nCannot continue without knowing who the key belongs to.")
        return 1
    uid = me["id"]

    status, body = api("GET", f"/profile/{uid}/following-list")
    check("following list is a page", status == 200 and is_page(body), f"{status} {str(body)[:120]}")
    status, body = api("GET", f"/profile/{uid}/followers-list")
    check("followers list is a page", status == 200 and is_page(body), f"{status} {str(body)[:120]}")

    status, body = api("GET", "/profile/search")
    ok = status == 200 and is_page(body) and all(CARD_KEYS <= set(c) for c in body["results"])
    check("search with no query lists people, with is_follower on each card", ok, f"{status} {str(body)[:120]}")
    check("the key's own account is in the directory", ok and any(c["id"] == uid and c["is_self"] for c in body["results"]) or (ok and body["total_results"] > 24))

    status, body = api("GET", "/history/watched-ids")
    check(
        "watched-ids returns four lists of ids",
        status == 200 and isinstance(body, dict)
        and all(isinstance(body.get(k), list) for k in ("movies", "shows", "dropped_movies", "dropped_shows")),
        f"{status} {str(body)[:120]}",
    )

    status, body = api("GET", "/media/movie/550/watchers")
    check("watchers for a movie is a list", status == 200 and isinstance(body, dict) and isinstance(body.get("watchers"), list), f"{status} {str(body)[:120]}")
    status, body = api("GET", "/media/episode/550/watchers")
    check("watchers refuses an episode (400)", status == 400, f"{status}")

    status, body = api("GET", f"/profile/{uid}/rating", {"tmdb_id": 550, "media_type": "movie"})
    check("rating lookup answers (200 with a rating, or 404 when not rated)", status in (200, 404), f"{status} {str(body)[:120]}")
    if status == 200:
        check("rating lookup shape", isinstance(body, dict) and "rating" in body and "media" in body)
    status, body = api("GET", f"/profile/{uid}/rating", {"tmdb_id": 550, "media_type": "nope"})
    check("rating lookup refuses a bad media type (400)", status == 400, f"{status}")

    status, body = api("GET", f"/profile/{uid}/stats")
    check(
        "stats has the combined and the movie and show rating splits",
        status == 200 and isinstance(body, dict)
        and all(isinstance(body.get(k), list) for k in ("rating_distribution", "rating_distribution_movies", "rating_distribution_shows")),
        f"{status} {str(body)[:120]}",
    )

    status, body = api("GET", f"/profile/{uid}")
    comments = body.get("recent_comments") if isinstance(body, dict) else None
    check("profile loads and recent comments carry user_rating", status == 200 and isinstance(comments, list) and all("user_rating" in c for c in comments), f"{status}")

    status, body = api("GET", "/media/recommended")
    check("recommended answers (a list, empty if there is no TMDB key or collection)", status == 200 and isinstance(body, dict) and isinstance(body.get("results"), list), f"{status} {str(body)[:120]}")

    status, body = api("DELETE", "/history/drop/show", {"tmdb_id": 987654321})
    check("undrop a show by an unknown TMDB id is a harmless no-op", status == 200, f"{status} {str(body)[:120]}")

    if args.write:
        status, created = api("POST", "/comments", body={"media_type": "movie", "tmdb_id": 550, "content": "smoke test, safe to delete"})
        check("posting a comment works with an API key", status == 200 and isinstance(created, dict) and "id" in created, f"{status} {str(created)[:120]}")
        if status == 200 and isinstance(created, dict) and "id" in created:
            status, _ = api("DELETE", f"/comments/{created['id']}")
            check("deleting it works with an API key", status in (200, 204), f"{status}")

    for page in () if args.skip_web else ("/login", "/people"):
        try:
            req = urllib.request.Request(base + page)
            opener = urllib.request.build_opener(type("NoRedirect", (urllib.request.HTTPRedirectHandler,), {"redirect_request": lambda *a, **k: None}))
            try:
                code = opener.open(req, timeout=30).status
            except urllib.error.HTTPError as e:
                code = e.code
        except Exception as e:  # noqa: BLE001
            code = str(e)
        check(f"web page {page} does not error", code in (200, 301, 302, 303, 307, 308), f"{code}")

    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
