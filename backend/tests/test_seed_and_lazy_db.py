import json
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.config import settings
from app.db.base import Base
from app.main import app
from app.services.data_transfer import data_transfer_service

SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


def test_seed_dataset_files_import_and_serve_cleanly():
    """Validate prepared initial roadmap JSON & resources CSV in an isolated SQLite DB."""
    roadmap_path = SEEDS_DIR / "initial_roadmap.json"
    resources_path = SEEDS_DIR / "initial_resources.csv"

    assert roadmap_path.exists()
    assert resources_path.exists()

    roadmap_payload = json.loads(roadmap_path.read_text(encoding="utf-8"))
    csv_payload = resources_path.read_text(encoding="utf-8")

    iso_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=iso_engine)
    Base.metadata.create_all(bind=iso_engine)
    db = TestingSession()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    orig_overrides = dict(app.dependency_overrides)
    try:
        imported_topics = data_transfer_service.import_roadmap_json(db, roadmap_payload)
        imported_resources = data_transfer_service.import_resources_csv(db, csv_payload)

        assert imported_topics == 4
        assert imported_resources == 8

        app.dependency_overrides[get_db] = override_get_db
        with TestClient(app) as client:
            topics_res = client.get("/api/v1/roadmap/topics")
            assert topics_res.status_code == 200
            topics = topics_res.json()
            assert [t["slug"] for t in topics] == [
                "arrays-and-hashing",
                "two-pointers-sliding-window",
                "trees-and-graphs",
                "dynamic-programming",
            ]
            # Parent-child nesting preserved for Two Pointers subtopics
            two_ptr_subs = topics[1]["subtopics"]
            assert len(two_ptr_subs) == 1
            assert two_ptr_subs[0]["slug"] == "converging-fast-slow-pointers"
            assert len(two_ptr_subs[0]["children"]) == 1
            assert two_ptr_subs[0]["children"][0]["slug"] == "variable-sliding-window"

            resources_res = client.get("/api/v1/resources/")
            assert resources_res.status_code == 200
            assert len(resources_res.json()) == 8
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(orig_overrides)
        db.close()
        Base.metadata.drop_all(bind=iso_engine)
        iso_engine.dispose()


def test_clerk_exchange_and_webhook_skip_db_on_invalid_inputs(client):
    """Ensure invalid auth exchange & webhook calls never open a DB session."""
    orig_secret = settings.CLERK_WEBHOOK_SECRET
    settings.CLERK_WEBHOOK_SECRET = "whsec_test_secret_value_for_unit_check"
    try:
        with (
            patch("app.api.v1.auth.resolve_request_db_session") as mock_auth_db,
            patch("app.api.v1.webhooks.resolve_request_db_session") as mock_wh_db,
        ):
            r_missing = client.post("/api/v1/auth/clerk/exchange", json={})
            assert r_missing.status_code == 401

            r_bad_jwt = client.post(
                "/api/v1/auth/clerk/exchange", json={"token": "not-a-jwt"}
            )
            assert r_bad_jwt.status_code == 401

            r_bad_wh = client.post("/api/v1/webhooks/clerk", json={})
            assert r_bad_wh.status_code == 400

            assert mock_auth_db.call_count == 0
            assert mock_wh_db.call_count == 0
    finally:
        settings.CLERK_WEBHOOK_SECRET = orig_secret
