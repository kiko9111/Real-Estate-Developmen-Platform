import json

from .conftest import PASSWORD, Client, csrf_of
from .helpers import LAND


def test_pages_need_login(app):
    c = Client(app)
    for url in ("/", "/market", "/lands/new", "/users", "/account"):
        r = c.get(url, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login"), url


def test_wrong_password_is_refused(app):
    c = Client(app)
    r = c.post("/login", {"email": "admin@example.com", "password": "nope"})
    assert r.status_code == 401
    assert "wrong" in r.text


def test_login_is_case_insensitive_and_redirects_safely(app):
    c = Client(app)
    r = c.post("/login", {"email": "ADMIN@example.com", "password": PASSWORD, "next": "//evil.example"})
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_post_without_csrf_token_is_refused(admin):
    r = admin.c.post("/lands", data={"data": json.dumps(LAND)}, follow_redirects=False)
    assert r.status_code == 403


def test_viewer_can_read_but_not_change(app, admin, viewer):
    r = admin.post("/lands", {"data": json.dumps(LAND)})
    lid = r.headers["location"].rsplit("/", 1)[1]
    assert viewer.get(f"/lands/{lid}").status_code == 200
    assert viewer.post("/lands", {"data": json.dumps(LAND)}).status_code == 403
    assert viewer.post(f"/lands/{lid}/status", {"status": "go"}).status_code == 403
    assert viewer.get("/lands/new").status_code == 403


def test_only_admin_manages_users(admin, analyst):
    assert analyst.get("/users").status_code == 403
    r = admin.post("/users", {"name": "New Person", "email": "new@example.com", "role": "viewer",
                              "password": "short"})
    assert r.status_code == 303
    assert "new@example.com" not in admin.get("/users").text      # refused: password too short
    admin.post("/users", {"name": "New Person", "email": "new@example.com", "role": "viewer",
                          "password": "long enough pw"})
    assert "new@example.com" in admin.get("/users").text


def test_deactivated_user_cannot_sign_in(app, admin):
    db = app.state.Session()
    from webapp.models import User
    uid = db.query(User).filter_by(email="analyst@example.com").one().id
    db.close()
    admin.post(f"/users/{uid}", {"role": "analyst"})            # no "active" box = deactivated
    c = Client(app)
    r = c.post("/login", {"email": "analyst@example.com", "password": PASSWORD})
    assert r.status_code == 401


def test_admin_cannot_lock_themselves_out(app, admin):
    db = app.state.Session()
    from webapp.models import User
    me = db.query(User).filter_by(email="admin@example.com").one()
    db.close()
    admin.post(f"/users/{me.id}", {"role": "viewer", "active": "1"})
    db = app.state.Session()
    assert db.get(User, me.id).role == "admin"
    db.close()


def test_change_password(app, analyst):
    r = analyst.post("/account/password", {"current": PASSWORD, "new": "a brand new password"})
    assert r.status_code == 303
    c = Client(app)
    assert c.post("/login", {"email": "analyst@example.com", "password": "a brand new password"}).status_code == 303


def test_password_hashes_are_salted():
    from webapp.security import hash_password, verify_password
    a, b = hash_password("same password"), hash_password("same password")
    assert a != b and verify_password("same password", a) and not verify_password("other", a)


def test_every_rendered_form_carries_a_token(app, admin):
    import re
    from .helpers import make_land
    lid = make_land(admin)
    for url in ("/", f"/lands/{lid}", f"/lands/{lid}/conditions", f"/lands/{lid}/screen", "/users", "/account",
                "/market?location=x"):
        html = admin.get(url).text
        tokens = re.findall(r'name="csrf_token" value="([^"]*)"', html)
        assert tokens and all(tokens), url
