import uuid
from datetime import datetime, timedelta, timezone
from typing import Generator
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.core.security import generate_session_token, hash_password_sync, hash_session_token
from app.db.base import Base
from app.main import app
from app.models.user import StudentProfile, User, UserRole, UserSession

# In-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    from app.core.config import settings
    orig_smtp = settings.SMTP_HOST
    orig_resend = settings.RESEND_API_KEY
    orig_brevo = settings.BREVO_API_KEY
    orig_iters = settings.PBKDF2_ITERATIONS

    settings.SMTP_HOST = None
    settings.RESEND_API_KEY = None
    settings.BREVO_API_KEY = None
    settings.PBKDF2_ITERATIONS = 1000

    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)

    settings.SMTP_HOST = orig_smtp
    settings.RESEND_API_KEY = orig_resend
    settings.BREVO_API_KEY = orig_brevo
    settings.PBKDF2_ITERATIONS = orig_iters


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def tech_admin_user(db_session: Session) -> User:
    user = User(
        email="techadmin@atc.bennett.edu.in",
        username="techadmin",
        hashed_password=hash_password_sync("adminpassword123"),
        role=UserRole.TECH_ADMIN,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def tech_admin_headers(db_session: Session, tech_admin_user: User) -> dict:
    token = generate_session_token(48)
    session = UserSession(
        user_id=tech_admin_user.id,
        token=hash_session_token(token),
        enforce_single_session=True,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        is_revoked=False,
    )
    db_session.add(session)
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def student_user(db_session: Session) -> User:
    user = User(
        email="student@bennett.edu.in",
        username="student1",
        hashed_password=hash_password_sync("studentpassword123"),
        role=UserRole.STUDENT,
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    db_session.flush()

    profile = StudentProfile(
        user_id=user.id,
        full_name="Student Tester",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def student_headers(db_session: Session, student_user: User) -> dict:
    token = generate_session_token(48)
    session = UserSession(
        user_id=student_user.id,
        token=hash_session_token(token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
        is_revoked=False,
    )
    db_session.add(session)
    db_session.commit()
    return {"Authorization": f"Bearer {token}"}
