import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from routers import profile as profile_router


class FormatRatingDistributionTests(unittest.TestCase):
    """The stats response sends each rating distribution as a list of
    {"rating", "count"}; the combined one and the movie and show ones share
    this builder."""

    def test_rows_become_rating_count_dicts_with_float_ratings(self):
        rows = [(7, 3), (8.5, 1)]
        self.assertEqual(
            profile_router._format_rating_distribution(rows),
            [{"rating": 7.0, "count": 3}, {"rating": 8.5, "count": 1}],
        )

    def test_no_rows_is_an_empty_list(self):
        self.assertEqual(profile_router._format_rating_distribution([]), [])


if __name__ == "__main__":
    unittest.main()
