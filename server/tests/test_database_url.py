from app.db.session import normalize_database_url


def test_normalizes_postgresql_url_for_asyncpg() -> None:
    url = "postgresql://user:password@example.com/database?sslmode=require&channel_binding=require"

    assert normalize_database_url(url) == (
        "postgresql+asyncpg://user:password@example.com/database?ssl=require"
    )


def test_keeps_async_and_sqlite_urls_unchanged() -> None:
    async_url = "postgresql+asyncpg://user:password@example.com/database"
    sqlite_url = "sqlite+aiosqlite:///./talkto.db"

    assert normalize_database_url(async_url) == async_url
    assert normalize_database_url(sqlite_url) == sqlite_url
