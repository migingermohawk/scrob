import inspect
import os
import unittest
from datetime import timedelta

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import dependencies
from core.security import create_access_token
from dependencies import DEVICE_TOKEN_TYPE, get_current_user_or_api_key_strict
from models.profile import UserProfileData
from models.users import User
from routers import auth


# UserProfileData's genre/etc. columns are Postgres-only JSONB - SQLite has
# no such type, so table creation fails outright without this. Standard
# SQLAlchemy pattern for running Postgres-JSONB models against a SQLite
# test DB: compile JSONB as SQLite's own JSON affinity instead, rather than
# hand-maintaining a parallel stub table that could drift from the real
# model's column list over time.
@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_for_sqlite(element, compiler, **kw):
    return "JSON"


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


class MeRouteWiringTests(unittest.TestCase):
    """GET /auth/me was JWT-only via get_current_user, so a client holding only
    a static API key had no way to resolve "who am I". Route-level
    check only (no DB) - the dependency's own request-handling behavior is
    covered by GetCurrentUserOrApiKeyStrictTests below, called directly
    against a real in-memory session rather than through the full HTTP/
    response-serialization path, which would need UserProfileData's
    Postgres-only JSONB columns creatable in SQLite too (they aren't)."""

    def test_me_route_uses_the_strict_or_api_key_dependency(self):
        sig = inspect.signature(auth.read_users_me)
        current_user_default = sig.parameters["current_user"].default
        self.assertIs(current_user_default.dependency, get_current_user_or_api_key_strict)


class GetCurrentUserOrApiKeyStrictTests(unittest.IsolatedAsyncioTestCase):
    """Exercises the dependency itself directly (matching test_oauth_device.py's
    own established pattern for these auth-dependency checks) against a real
    in-memory SQLite session - only the User table is needed since nothing
    here reads .display_name/serializes through schemas.User, which is the
    only thing that would need UserProfileData's relationship loadable."""

    async def asyncSetUp(self):
        self.engine = _make_engine()
        async with self.engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            # get_current_user_or_api_key_strict's JWT branch selectinloads
            # User.profile (mirroring get_current_user), which issues a real
            # eager-load query against this table regardless of whether the
            # test itself ever reads .display_name - needs to exist even
            # though it's never populated here.
            await conn.run_sync(UserProfileData.__table__.create)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

        async with self.Session() as s:
            s.add(User(id=1, email="a@b.c", username="alice", api_key="k-alice"))
            await s.commit()

        self.addAsyncCleanup(self.engine.dispose)

    async def test_accepts_a_static_api_key_with_no_jwt_at_all(self):
        async with self.Session() as s:
            user = await get_current_user_or_api_key_strict(db=s, token=None, api_key="k-alice", x_api_key=None)
        self.assertEqual(user.username, "alice")

    async def test_accepts_the_x_api_key_header_too(self):
        async with self.Session() as s:
            user = await get_current_user_or_api_key_strict(db=s, token=None, api_key=None, x_api_key="k-alice")
        self.assertEqual(user.username, "alice")

    async def test_still_works_with_a_real_session_jwt(self):
        token = create_access_token(subject=1, expires_delta=timedelta(hours=1))
        async with self.Session() as s:
            user = await get_current_user_or_api_key_strict(db=s, token=token, api_key=None, x_api_key=None)
        self.assertEqual(user.username, "alice")

    async def test_rejects_an_invalid_api_key(self):
        async with self.Session() as s:
            with self.assertRaises(Exception) as ctx:
                await get_current_user_or_api_key_strict(db=s, token=None, api_key="not-a-real-key", x_api_key=None)
        self.assertEqual(getattr(ctx.exception, "status_code", None), 401)

    async def test_rejects_no_credentials_at_all(self):
        async with self.Session() as s:
            with self.assertRaises(Exception) as ctx:
                await get_current_user_or_api_key_strict(db=s, token=None, api_key=None, x_api_key=None)
        self.assertEqual(getattr(ctx.exception, "status_code", None), 401)

    async def test_rejects_a_device_scoped_token_with_403_even_though_it_also_accepts_api_key(self):
        # The device-token exclusion for account/security endpoints must
        # survive unchanged - the api_key acceptance is additive, not a
        # loosening of it. Fails if
        # this dependency is ever rewritten to reuse get_optional_user's
        # more permissive JWT resolution (the bug it was deliberately NOT
        # built on top of).
        token = create_access_token(
            subject=1,
            expires_delta=timedelta(hours=1),
            extra_claims={"type": DEVICE_TOKEN_TYPE, "scope": "write", "jti": "999"},
        )
        async with self.Session() as s:
            with self.assertRaises(Exception) as ctx:
                await get_current_user_or_api_key_strict(db=s, token=token, api_key=None, x_api_key=None)
        self.assertEqual(getattr(ctx.exception, "status_code", None), 403)

    async def test_device_token_rejection_is_not_bypassed_by_also_passing_a_valid_api_key(self):
        # The device-token check must run before the api_key fallback is
        # even considered - a caller can't dodge the 403 by tacking on a
        # real key alongside the device token.
        token = create_access_token(
            subject=1,
            expires_delta=timedelta(hours=1),
            extra_claims={"type": DEVICE_TOKEN_TYPE, "scope": "write", "jti": "999"},
        )
        async with self.Session() as s:
            with self.assertRaises(Exception) as ctx:
                await get_current_user_or_api_key_strict(db=s, token=token, api_key="k-alice", x_api_key=None)
        self.assertEqual(getattr(ctx.exception, "status_code", None), 403)


if __name__ == "__main__":
    unittest.main()
