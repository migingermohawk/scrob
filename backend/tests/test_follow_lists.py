import os
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from db import get_db
from models.collection import Collection
from models.events import WatchEvent
from models.follows import Follow
from models.global_settings import GlobalSettings
from models.media import Media
from models.profile import PrivacyLevel, UserProfileData
from models.ratings import Rating
from models.show import Show
from models.users import User
from routers import profile as profile_router


# The profile model uses Postgres-only JSONB columns; compile them as JSON so the
# tables can be created in an in-memory SQLite database.
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


TABLES = [User, UserProfileData, Follow, GlobalSettings, Media, Show, WatchEvent, Collection, Rating]


class FollowListTests(unittest.IsolatedAsyncioTestCase):
    """GET /profile/{id}/following-list and /followers-list: a paginated list of
    directory cards (the same shape /profile/search returns), behind the same
    privacy rules as the rest of the profile."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            for model in TABLES:
                await conn.run_sync(model.__table__.create)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as s:
            # 1 alice (public), 2 bob (public), 3 carol (private), 4 dave (public)
            for uid, name, privacy in [
                (1, "alice", PrivacyLevel.public),
                (2, "bob", PrivacyLevel.public),
                (3, "carol", PrivacyLevel.private),
                (4, "dave", PrivacyLevel.public),
            ]:
                s.add(User(id=uid, email=f"{name}@example.com", username=name, api_key=f"k-{name}"))
                s.add(UserProfileData(user_id=uid, display_name=name.title(), privacy_level=privacy))
            await s.commit()
            base = datetime(2026, 1, 1)
            # alice follows bob, then dave (dave is newer); bob follows alice
            s.add(Follow(follower_id=1, following_id=2, created_at=base))
            s.add(Follow(follower_id=1, following_id=4, created_at=base + timedelta(days=1)))
            s.add(Follow(follower_id=2, following_id=1, created_at=base))
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

    async def test_following_list_returns_cards_newest_first(self):
        resp = await self.client.get("/profile/1/following-list", params={"api_key": "k-alice"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual([r["username"] for r in body["results"]], ["dave", "bob"])
        self.assertEqual((body["page"], body["total_pages"], body["total_results"]), (1, 1, 2))
        card = body["results"][0]
        for key in (
            "id", "display_name", "avatar_url", "country", "movies_watched", "shows_watched",
            "total_collected", "total_rated", "follower_count", "is_following", "is_self",
        ):
            self.assertIn(key, card)
        self.assertTrue(card["is_following"], "alice follows dave")
        self.assertEqual(card["follower_count"], 1)

    async def test_followers_list_is_the_reverse_direction(self):
        resp = await self.client.get("/profile/1/followers-list", params={"api_key": "k-alice"})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual([r["username"] for r in resp.json()["results"]], ["bob"])

    async def test_is_following_is_from_the_viewers_point_of_view(self):
        resp = await self.client.get("/profile/1/following-list", params={"api_key": "k-bob"})
        by_name = {r["username"]: r for r in resp.json()["results"]}
        self.assertTrue(by_name["bob"]["is_self"])
        self.assertFalse(by_name["bob"]["is_following"])
        self.assertFalse(by_name["dave"]["is_following"], "bob does not follow dave")

    async def test_an_empty_list_is_one_empty_page(self):
        resp = await self.client.get("/profile/4/following-list", params={"api_key": "k-alice"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["results"], [])
        self.assertEqual(resp.json()["total_pages"], 1)

    async def test_paging_splits_after_24_people(self):
        async with self.Session() as s:
            for i in range(30):
                uid = 100 + i
                s.add(User(id=uid, email=f"u{i}@example.com", username=f"user{i:02d}", api_key=f"k-u{i}"))
                s.add(UserProfileData(user_id=uid, privacy_level=PrivacyLevel.public))
                s.add(Follow(follower_id=uid, following_id=4))
            await s.commit()
        first = (await self.client.get("/profile/4/followers-list", params={"api_key": "k-dave"})).json()
        second = (await self.client.get("/profile/4/followers-list", params={"api_key": "k-dave", "page": 2})).json()
        self.assertEqual(len(first["results"]), 24)
        self.assertEqual(len(second["results"]), 7, "30 new followers plus alice, 24 on the first page")
        self.assertEqual((first["total_pages"], first["total_results"]), (2, 31))
        ids = [r["id"] for r in first["results"] + second["results"]]
        self.assertEqual(len(ids), len(set(ids)), "no one appears on two pages")

    async def test_a_private_profile_is_forbidden_to_other_people_but_not_to_its_owner(self):
        other = await self.client.get("/profile/3/followers-list", params={"api_key": "k-alice"})
        self.assertEqual(other.status_code, 403)
        owner = await self.client.get("/profile/3/followers-list", params={"api_key": "k-carol"})
        self.assertEqual(owner.status_code, 200)

    async def test_an_unknown_profile_is_404(self):
        resp = await self.client.get("/profile/999/following-list", params={"api_key": "k-alice"})
        self.assertEqual(resp.status_code, 404)

    async def test_search_still_returns_the_same_cards(self):
        resp = await self.client.get("/profile/search", params={"q": "bo", "api_key": "k-alice"})
        self.assertEqual(resp.status_code, 200, resp.text)
        results = resp.json()["results"]
        self.assertEqual([r["username"] for r in results], ["bob"])
        self.assertTrue(results[0]["is_following"])
        self.assertEqual(sorted(results[0]), sorted(
            ["id", "username", "display_name", "avatar_url", "country", "movies_watched", "shows_watched",
             "total_collected", "total_rated", "follower_count", "is_following", "is_self"]
        ))


if __name__ == "__main__":
    unittest.main()
