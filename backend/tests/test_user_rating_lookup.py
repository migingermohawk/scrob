import os
import unittest
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from db import get_db
from models.follows import Follow
from models.global_settings import GlobalSettings
from models.media import Media
from models.profile import PrivacyLevel, UserProfileData
from models.ratings import Rating
from models.users import User
from routers import profile as profile_router


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


TABLES = [User, UserProfileData, Follow, GlobalSettings, Media, Rating]


class UserRatingLookupTests(unittest.IsolatedAsyncioTestCase):
    """GET /profile/{user_id}/rating: one person's rating for one title, behind
    the same privacy rules as the rest of the profile."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            for model in TABLES:
                await conn.run_sync(model.__table__.create)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as s:
            for uid, name, privacy in [
                (1, "alice", PrivacyLevel.public),
                (2, "bob", PrivacyLevel.private),
                (3, "carol", PrivacyLevel.friends_only),
            ]:
                s.add(User(id=uid, email=f"{name}@example.com", username=name, api_key=f"k-{name}"))
                s.add(UserProfileData(user_id=uid, privacy_level=privacy))
            s.add(Media(id=1, tmdb_id=100, media_type="movie", title="A Movie", release_date="2020-01-01"))
            s.add(Media(id=2, tmdb_id=200, media_type="series", title="A Show"))
            s.add(Media(id=3, tmdb_id=101, media_type="movie", title="Unrated Movie"))
            when = datetime(2026, 1, 1)
            s.add(Rating(user_id=1, media_id=1, rating=8.5, review="Great", rated_at=when))
            s.add(Rating(user_id=1, media_id=2, rating=7.0, rated_at=when))  # whole show
            s.add(Rating(user_id=1, media_id=2, season_number=2, rating=9.0, rated_at=when))  # season 2
            s.add(Rating(user_id=2, media_id=1, rating=5.0, rated_at=when))
            s.add(Rating(user_id=3, media_id=1, rating=6.0, rated_at=when))
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

    async def _get(self, user_id, key="k-bob", **params):
        return await self.client.get(f"/profile/{user_id}/rating", params={"api_key": key, **params})

    async def test_returns_the_rating_and_the_title(self):
        resp = await self._get(1, tmdb_id=100, media_type="movie")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["rating"], 8.5)
        self.assertEqual(body["review"], "Great")
        self.assertEqual(body["user_id"], 1)
        self.assertEqual(body["media"]["tmdb_id"], 100)
        self.assertEqual(body["media"]["title"], "A Movie")

    async def test_a_show_rating_and_a_season_rating_are_separate(self):
        whole = (await self._get(1, tmdb_id=200, media_type="series")).json()
        season = (await self._get(1, tmdb_id=200, media_type="series", season_number=2)).json()
        self.assertEqual((whole["rating"], whole["season_number"]), (7.0, None))
        self.assertEqual((season["rating"], season["season_number"]), (9.0, 2))

    async def test_404_when_the_person_has_not_rated_it(self):
        resp = await self._get(1, tmdb_id=101, media_type="movie")
        self.assertEqual(resp.status_code, 404)

    async def test_404_when_the_title_is_unknown(self):
        resp = await self._get(1, tmdb_id=999, media_type="movie")
        self.assertEqual(resp.status_code, 404)

    async def test_400_for_an_invalid_media_type(self):
        resp = await self._get(1, tmdb_id=100, media_type="not-a-type")
        self.assertEqual(resp.status_code, 400)

    async def test_a_private_profile_is_forbidden_to_other_people_but_not_to_its_owner(self):
        other = await self._get(2, key="k-alice", tmdb_id=100, media_type="movie")
        self.assertEqual(other.status_code, 403)
        owner = await self._get(2, key="k-bob", tmdb_id=100, media_type="movie")
        self.assertEqual(owner.status_code, 200)
        self.assertEqual(owner.json()["rating"], 5.0)

    async def test_a_friends_only_profile_needs_a_mutual_follow(self):
        resp = await self._get(3, key="k-alice", tmdb_id=100, media_type="movie")
        self.assertEqual(resp.status_code, 403)
        async with self.Session() as s:
            s.add(Follow(follower_id=1, following_id=3))
            s.add(Follow(follower_id=3, following_id=1))
            await s.commit()
        resp = await self._get(3, key="k-alice", tmdb_id=100, media_type="movie")
        self.assertEqual(resp.status_code, 200, resp.text)


if __name__ == "__main__":
    unittest.main()
