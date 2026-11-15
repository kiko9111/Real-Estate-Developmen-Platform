"""Command line for the web app.

python -m webapp serve [--host 0.0.0.0] [--port 8000]
python -m webapp create-user EMAIL NAME [--role admin|analyst|viewer]   (asks for the password)
python -m webapp seed-demo        adds one invented demo land with a screening, a study and comparables
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import func, select


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="webapp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    cu = sub.add_parser("create-user")
    cu.add_argument("email")
    cu.add_argument("name")
    cu.add_argument("--role", default="analyst", choices=["admin", "analyst", "viewer"])
    cu.add_argument("--password", help="for scripts; otherwise you are asked")
    sub.add_parser("seed-demo")
    a = ap.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn
        uvicorn.run("webapp.app:app", host=a.host, port=a.port)
        return 0

    from .config import Settings
    from .db import Base, make_engine, make_sessionmaker
    from .models import User
    from .security import check_password_rules, hash_password

    settings = Settings()
    engine = make_engine(settings.database_url)
    Base.metadata.create_all(engine)
    db = make_sessionmaker(engine)()

    if a.cmd == "create-user":
        pw = a.password or getpass.getpass("Password: ")
        if err := check_password_rules(pw):
            print(err, file=sys.stderr)
            return 1
        if db.scalar(select(User).where(func.lower(User.email) == a.email.lower())):
            print("A user with that email already exists.", file=sys.stderr)
            return 1
        db.add(User(email=a.email.lower(), name=a.name, role=a.role, password_hash=hash_password(pw)))
        db.commit()
        print(f"Created {a.role} {a.email}")
        return 0

    if a.cmd == "seed-demo":
        from .demo import seed_demo
        land = seed_demo(db)
        print(f"Demo land created: /lands/{land.id}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
