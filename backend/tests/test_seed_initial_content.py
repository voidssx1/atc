from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from scripts.seed_initial_content import seed_initial_content


def test_seed_initial_content_is_repeatable():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        with SessionLocal() as db:
            first = seed_initial_content(db)
            second = seed_initial_content(db)

        assert first["roadmap"]["imported_topics"] == 4
        assert first["roadmap"]["imported_subtopics"] == 8
        assert first["resources"]["imported_count"] == 8
        assert second["roadmap"]["imported_topics"] == 0
        assert second["roadmap"]["imported_subtopics"] == 0
        assert second["resources"]["imported_count"] == 0
        assert second["resources"]["skipped_count"] == 8
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
