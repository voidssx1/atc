"""Idempotently import the bundled starter DSA roadmap and resources.

Run after Alembic migrations with DATABASE_URL pointing to the target database.
"""
import json
from pathlib import Path
from sqlalchemy.orm import Session

from app.db.session import get_session_maker
from app.services.data_transfer import data_transfer_service

BACKEND_DIR = Path(__file__).resolve().parents[1]
SEEDS_DIR = BACKEND_DIR / "seeds"


def seed_initial_content(db: Session) -> dict:
    roadmap = json.loads((SEEDS_DIR / "initial_roadmap.json").read_text(encoding="utf-8"))
    resources_csv = (SEEDS_DIR / "initial_resources.csv").read_text(encoding="utf-8")
    roadmap_stats = data_transfer_service.import_roadmap_json_with_stats(db, roadmap)
    resource_stats = data_transfer_service.import_resources_csv_with_stats(db, resources_csv)
    return {"roadmap": roadmap_stats, "resources": resource_stats}


def main() -> int:
    session_factory = get_session_maker()
    with session_factory() as db:
        stats = seed_initial_content(db)
    print(json.dumps(stats, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
