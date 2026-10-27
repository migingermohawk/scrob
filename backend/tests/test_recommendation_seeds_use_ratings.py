import os
import random
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from routers.media import _pick_recommendation_seeds


class RecommendationSeedTests(unittest.TestCase):
    """The person's own ratings steer which titles seed a recommendation."""

    def test_low_rated_title_never_seeds(self):
        for seed in range(50):
            picked = _pick_recommendation_seeds([1, 2, 3], [], {2: 3.0}, 3, random.Random(seed))
            self.assertNotIn(2, picked)
            self.assertEqual(sorted(picked), [1, 3])

    def test_unrated_and_middling_titles_still_seed(self):
        picked = _pick_recommendation_seeds([1, 2], [], {2: 6.0}, 2, random.Random(0))
        self.assertEqual(sorted(picked), [1, 2])

    def test_loved_title_not_recently_collected_can_seed(self):
        picked = _pick_recommendation_seeds([], [9], {9: 9.5}, 3, random.Random(0))
        self.assertEqual(picked, [9])

    def test_loved_titles_are_picked_more_often(self):
        wins = 0
        for seed in range(400):
            picked = _pick_recommendation_seeds([1, 2], [], {1: 9.0}, 1, random.Random(seed))
            wins += picked == [1]
        # 3:1 odds, so around 300 of 400; far from the 200 of an even draw.
        self.assertGreater(wins, 260)

    def test_no_duplicates_and_respects_count(self):
        picked = _pick_recommendation_seeds([1, 2, 3, 4], [2, 5], {2: 9.0, 5: 8.0}, 3, random.Random(1))
        self.assertEqual(len(picked), 3)
        self.assertEqual(len(set(picked)), 3)

    def test_empty_pool(self):
        self.assertEqual(_pick_recommendation_seeds([], [], {}, 3, random.Random(0)), [])


if __name__ == "__main__":
    unittest.main()
