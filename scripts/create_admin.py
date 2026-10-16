"""Crea el primer usuario administrador del tablero.

Uso:
    python -m scripts.create_admin --email admin@unsa.edu.pe --name "Admin GSTI"
(la contraseña se pide por consola; mínimo 12 caracteres)
"""
import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from app.db import dispose_engine, get_sessionmaker
from app.models import StaffUser
from app.services.security import hash_password


async def main(email: str, name: str, role: str, password: str) -> None:
    async with get_sessionmaker()() as session:
        if await session.scalar(select(StaffUser).where(StaffUser.email == email.lower())):
            print(f"Ya existe {email}", file=sys.stderr)
            sys.exit(1)
        session.add(StaffUser(email=email.lower(), full_name=name, role=role, password_hash=hash_password(password)))
        await session.commit()
    await dispose_engine()
    print(f"Usuario {role} creado: {email}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--role", default="admin", choices=["admin", "clinician", "researcher"])
    args = ap.parse_args()
    pwd = os.environ.get("GSTI_ADMIN_PASSWORD") or getpass.getpass("Contraseña (mín. 12): ")
    if len(pwd) < 12:
        sys.exit("La contraseña debe tener al menos 12 caracteres")
    asyncio.run(main(args.email, args.name, args.role, pwd))
