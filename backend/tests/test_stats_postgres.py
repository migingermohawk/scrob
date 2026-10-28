import os
import unittest
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import get_db
from models.events import WatchEvent
from models.media import Media
from models.profile import PrivacyLevel, UserProfileData
from models.ratings import Rating
from models.users import User
from routers import profile as profile_router

ON_POSTGRES = os.environ.get("SCROB_TEST_DB") == "postgres"


@unittest.skipUnless(
    ON_POSTGRES,
    "the stats endpoint uses PostgreSQL-only date functions; run with SCROB_TEST_DB=postgres -p pg_test_plugin",
)
class UserStatsOnPostgresTests(unittest.IsolatedAsyncioTestCase):
    """GET /profile/{id}/stats, whole response, on a real PostgreSQL."""

    async def asyncSetUp(self):
        # Under SCROB_TEST_DB=postgres this "sqlite" URL is replaced by a real
        # PostgreSQL database built from the migrations (see pg_test_plugin.py).
        self.engine = create_async_engine("sqlite+aiosqlite://")
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as s:
            s.add(User(id=1, email="a@example.com", username="alice", api_key="k-alice"))
            s.add(UserProfileData(user_id=1, privacy_level=PrivacyLevel.public))
            s.add(Media(id=1, tmdb_id=100, media_type="movie", title="Movie One", runtime=100))
            s.add(Media(id=2, tmdb_id=101, media_type="movie", title="Movie Two", runtime=90))
            s.add(Media(id=3, tmdb_id=200, media_type="series", title="A Show"))
            await s.commit()
            s.add(WatchEvent(user_id=1, media_id=1, completed=True, watched_at=datetime(2026, 1, 5, 20, 0)))
            s.add(WatchEvent(user_id=1, media_id=2, completed=True, watched_at=datetime(2026, 2, 9, 21, 0)))
            s.add(Rating(user_id=1, media_id=1, rating=8.0))
            s.add(Rating(user_id=1, media_id=2, rating=8.0))
            s.add(Rating(user_id=1, media_id=3, rating=6.5))
            await s.commit()

        app = FastAPI()
        app.include_router(profile_router.router, prefix="/profile")

        async def _override_get_db():
            async with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = _override_get_db
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.addAsyncCleanup(self.engine.dispose)

    async def test_the_whole_stats_response_is_built(self):
        resp = await self.client.get("/profile/1/stats", params={"api_key": "k-alice"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["rating_distribution"], [{"rating": 6.5, "count": 1}, {"rating": 8.0, "count": 2}])

    async def test_ratings_are_split_by_movies_and_shows(self):
        body = (await self.client.get("/profile/1/stats", params={"api_key": "k-alice"})).json()
        self.assertEqual(body["rating_distribution_movies"], [{"rating": 8.0, "count": 2}])
        self.assertEqual(body["rating_distribution_shows"], [{"rating": 6.5, "count": 1}])


if __name__ == "__main__":
    unittest.main()
