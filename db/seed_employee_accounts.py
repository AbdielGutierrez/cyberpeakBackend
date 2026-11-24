"""Crea una cuenta de acceso por cada empleado de Northwind.

Uso (desde ``backend/``):

    python -m db.seed_employee_accounts

Reglas:

- nombre: ``FirstName`` del empleado.
- correo: ``{FirstName}{LastName}@cyberpeak.com`` en minúsculas.
- contraseña: ``{FirstName}1234``.
- rol: ``employee``.

Es idempotente: si el correo ya existe no lo duplica ni cambia su contraseña.
"""

import sqlite3

from auth.security import hash_password
from db.database import connection

ROLE = "employee"


def build_account(first_name: str, last_name: str) -> tuple[str, str, str]:
    """Devuelve ``(name, email, password)`` para un empleado."""
    name = first_name.strip()
    email = f"{first_name.strip()}{last_name.strip()}@cyberpeak.com".lower()
    return name, email, f"{name}1234"


def seed() -> None:
    with connection() as conn:
        employees = conn.execute(
            "SELECT FirstName, LastName FROM Employees ORDER BY EmployeeID"
        ).fetchall()

        created, skipped = 0, 0
        for employee in employees:
            name, email, password = build_account(
                employee["FirstName"], employee["LastName"]
            )

            exists = conn.execute(
                "SELECT 1 FROM Users WHERE lower(email) = ?", (email,)
            ).fetchone()
            if exists is not None:
                skipped += 1
                continue

            conn.execute(
                "INSERT INTO Users (name, email, password_hash, role)"
                " VALUES (?, ?, ?, ?)",
                (name, email, hash_password(password), ROLE),
            )
            created += 1

    print(f"Cuentas creadas: {created}. Ya existían: {skipped}.")


if __name__ == "__main__":
    try:
        seed()
    except sqlite3.Error as error:
        raise SystemExit(f"No se pudo crear las cuentas: {error}") from error
