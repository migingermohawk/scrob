#!/usr/bin/env bash
# Run the fork's own tests against a real PostgreSQL (bundled by `pip install pgserver`,
# no Docker needed). See pg_test_plugin.py. Extra arguments go to pytest.
#
#   cd backend && pip install pgserver && ./run_postgres_tests.sh
#
# Only the fork's tests are run this way. Some of upstream's SQLite-based tests rely on
# SQLite not checking foreign keys and do not pass on PostgreSQL.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
SCROB_TEST_DB=postgres exec python -m pytest -p pg_test_plugin -q \
  tests/test_auth_me_api_key.py \
  tests/test_comments_api_key.py \
  tests/test_profile_bio_api_key.py \
  tests/test_follow_lists.py \
  tests/test_watched_ids.py \
  tests/test_undrop_show_by_tmdb_id.py \
  tests/test_media_watchers.py \
  tests/test_user_rating_lookup.py \
  tests/test_rating_distribution_by_type.py \
  tests/test_profile_recent_comments_rating.py \
  tests/test_recommended_endpoint.py \
  tests/test_recommendation_seeds_use_ratings.py \
  tests/test_stats_postgres.py \
  "$@"
