"""Fixtures: base de datos de prueba recién migrada y cliente de la API.

Requiere un PostgreSQL accesible con un superusuario. Por defecto usa el socket local;
con Docker: TEST_ADMIN_DSN=postgresql://gsti_owner:<pwd>@localhost:5432/postgres
La API corre con el rol gsti_api, así las pruebas también validan los permisos.
"""
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
ADMIN_DSN = os.environ.get("TEST_ADMIN_DSN", "postgresql://postgres@/postgres?host=/tmp")
TEST_DB = "gsti_test"
API_PWD = "test_api_pwd"


def _dsn(db: str, user: str | None = None, pwd: str | None = None) -> str:
    info = psycopg.conninfo.conninfo_to_dict(ADMIN_DSN)
    info["dbname"] = db
    if user:
        info["user"] = user
        info["password"] = pwd or ""
    return psycopg.conninfo.make_conninfo(**info)


def _sqlalchemy_url(dsn: str, driver: str) -> str:
    i = psycopg.conninfo.conninfo_to_dict(dsn)
    auth = i["user"] + (f":{i['password']}" if i.get("password") else "")
    host = i.get("host", "localhost")
    if host.startswith("/"):
        return f"postgresql+{driver}://{auth}@/{i['dbname']}?host={host}"
    return f"postgresql+{driver}://{auth}@{host}:{i.get('port', 5432)}/{i['dbname']}"


@pytest.fixture(scope="session")
def db_dsn():
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        for role in ("gsti_api", "gsti_analyst"):
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                c.execute(f"CREATE ROLE {role} LOGIN")
        c.execute(f"ALTER ROLE gsti_api PASSWORD '{API_PWD}'")
        c.execute(f"CREATE DATABASE {TEST_DB}")

    owner_dsn = _dsn(TEST_DB)
    env = {**os.environ, "MIGRATION_DATABASE_URL": _sqlalchemy_url(owner_dsn, "psycopg")}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, check=True,
                   capture_output=True)
    yield owner_dsn


@pytest.fixture(scope="session")
def app_client(db_dsn):
    os.environ["DATABASE_URL"] = _sqlalchemy_url(_dsn(TEST_DB, "gsti_api", API_PWD), "asyncpg")
    os.environ["DB_NULL_POOL"] = "true"
    os.environ["IDENTITY_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-123"
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def sql(db_dsn):
    """Ejecuta SQL directo (como dueño) para verificar el estado de la BD."""
    with psycopg.connect(db_dsn, autocommit=True) as c:
        def run(q, *a):
            cur = c.execute(q, a or None)
            return cur.fetchall() if cur.description else cur.rowcount

        yield run


@pytest.fixture(scope="session")
def staff_tokens(app_client, db_dsn):
    from app.services.security import hash_password

    pwd = "una-clave-larga-123"
    tokens = {}
    with psycopg.connect(db_dsn, autocommit=True) as c:
        for role in ("admin", "clinician", "researcher", "service"):
            email = f"{role}@test.pe"
            c.execute("INSERT INTO admin.staff_users (email, full_name, role, password_hash) "
                      "VALUES (%s, %s, %s, %s) ON CONFLICT (email) DO NOTHING",
                      (email, role.title(), role, hash_password(pwd)))
            r = app_client.post("/api/v1/auth/login", json={"email": email, "password": pwd})
            assert r.status_code == 200, r.text
            tokens[role] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return tokens


@pytest.fixture
def enrolled(app_client, staff_tokens):
    """Participante nuevo con un teléfono registrado. Devuelve (code, headers_dispositivo, enrolamiento)."""
    r = app_client.post("/api/v1/participants", headers=staff_tokens["researcher"],
                        json={"consent_version": "v1", "cohort": "test", "age_band": "21-23"})
    assert r.status_code == 201, r.text
    body = r.json()
    r = app_client.post("/api/v1/devices/register", json={"enrollment_code": body["enrollment_code"]})
    assert r.status_code == 201, r.text
    return body["participant"]["code"], {"Authorization": f"Bearer {r.json()['device_token']}"}, body


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
