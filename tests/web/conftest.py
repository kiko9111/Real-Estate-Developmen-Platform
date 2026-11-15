import re

import pytest
from fastapi.testclient import TestClient

from webapp.config import Settings
from webapp.main import create_app
from webapp.models import User
from webapp.security import hash_password

PASSWORD = "correct horse battery"


@pytest.fixture
def app():
    s = Settings(database_url="sqlite://", secret_key="test-secret", anthropic_api_key="")
    a = create_app(s)
    db = a.state.Session()
    for email, role in (("admin@example.com", "admin"), ("analyst@example.com", "analyst"),
                        ("viewer@example.com", "viewer")):
        db.add(User(email=email, name=role.title(), role=role, password_hash=hash_password(PASSWORD)))
    db.commit()
    db.close()
    return a


def csrf_of(client) -> str:
    html = client.get("/login").text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


class Client:
    """A TestClient that signs in and adds the CSRF token to every POST."""

    def __init__(self, app, email=None):
        self.c = TestClient(app)
        self.token = csrf_of(self.c)
        if email:
            r = self.c.post("/login", data={"email": email, "password": PASSWORD, "csrf_token": self.token},
                            follow_redirects=False)
            assert r.status_code == 303, r.text
            self.token = self._token_from("/account")

    def _token_from(self, url):
        html = self.c.get(url).text
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, data=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.token)
        kw.setdefault("follow_redirects", False)
        return self.c.post(url, data=data, **kw)


@pytest.fixture
def admin(app):
    return Client(app, "admin@example.com")


@pytest.fixture
def analyst(app):
    return Client(app, "analyst@example.com")


@pytest.fixture
def viewer(app):
    return Client(app, "viewer@example.com")
