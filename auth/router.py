"""Endpoints de registro, login y perfil del usuario autenticado."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from auth.dependencies import get_current_user, require_roles
from auth.schemas import (
    AdminUserOut,
    RoleUpdate,
    TokenOut,
    UserCreate,
    UserLogin,
    UserOut,
)
from auth.security import create_access_token, hash_password, verify_password
from db.database import connection

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = "Correo o contraseña incorrectos."
EMAIL_TAKEN = "Ya existe una cuenta con ese correo."
USER_NOT_FOUND = "No existe esa cuenta."
SELF_ACTION = "No puedes modificar tu propia cuenta desde aquí."
LAST_ADMIN = "Debe quedar al menos un administrador."
ALLOWED_ROLES = {"admin", "employee", "user"}


def public_user(row: sqlite3.Row) -> dict:
    """Convierte una fila de Users en la respuesta pública."""
    return {
        "id": row["UserID"],
        "name": row["name"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def admin_user(row: sqlite3.Row) -> dict:
    return {
        **public_user(row),
        "is_active": bool(row["is_active"]),
    }


@router.get("/users", response_model=list[AdminUserOut])
def list_users(_: sqlite3.Row = Depends(require_roles("admin"))) -> list[dict]:
    """Lista todas las cuentas. Solo disponible para administradores."""
    with connection() as conn:
        rows = conn.execute(
            "SELECT UserID, name, email, role, is_active, created_at"
            " FROM Users ORDER BY created_at DESC, UserID DESC"
        ).fetchall()
    return [admin_user(row) for row in rows]


@router.patch("/users/{user_id}/role", response_model=AdminUserOut)
def update_user_role(
    user_id: int,
    payload: RoleUpdate,
    current_user: sqlite3.Row = Depends(require_roles("admin")),
) -> dict:
    """Cambia el rol de una cuenta."""
    role = payload.role.strip().lower()
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El rol indicado no es válido.",
        )

    if user_id == current_user["UserID"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=SELF_ACTION
        )

    with connection() as conn:
        existing = conn.execute(
            "SELECT role FROM Users WHERE UserID = ?", (user_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")

        if existing["role"] == "admin" and role != "admin":
            admins = conn.execute(
                "SELECT COUNT(*) FROM Users WHERE role = 'admin'"
            ).fetchone()[0]
            if admins <= 1:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail=LAST_ADMIN
                )

        row = conn.execute(
            "UPDATE Users SET role = ? WHERE UserID = ?"
            " RETURNING UserID, name, email, role, is_active, created_at",
            (role, user_id),
        ).fetchone()

    return admin_user(row)


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    current_user: sqlite3.Row = Depends(require_roles("admin")),
) -> None:
    """Elimina una cuenta. Un administrador no puede eliminar su propia cuenta."""
    if user_id == current_user["UserID"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No puedes eliminar tu propia cuenta.",
        )

    try:
        with connection() as conn:
            exists = conn.execute(
                "SELECT 1 FROM Users WHERE UserID = ?", (user_id,)
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuario no encontrado.")
            conn.execute("DELETE FROM Users WHERE UserID = ?", (user_id,))
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede eliminar el usuario porque tiene datos asociados.",
        ) from None


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate) -> dict:
    """Crea un usuario con la contraseña hasheada."""
    email = payload.email.strip().lower()
    name = payload.name.strip()

    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El nombre no puede estar vacío.",
        )

    try:
        with connection() as conn:
            # El correo se guarda en minúsculas, así que comparamos igual.
            exists = conn.execute(
                "SELECT 1 FROM Users WHERE lower(email) = ?",
                (email,),
            ).fetchone()
            if exists is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=EMAIL_TAKEN,
                )

            user = conn.execute(
                "INSERT INTO Users (name, email, password_hash)"
                " VALUES (?, ?, ?)"
                " RETURNING UserID, name, email, role, created_at",
                (name, email, hash_password(payload.password)),
            ).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=EMAIL_TAKEN,
        ) from None

    return public_user(user)


@router.post("/login", response_model=TokenOut)
def login(payload: UserLogin) -> dict:
    """Verifica las credenciales y devuelve un JWT."""
    email = payload.email.strip().lower()

    with connection() as conn:
        user = conn.execute(
            "SELECT UserID, name, email, password_hash, role, is_active"
            " FROM Users WHERE email = ?",
            (email,),
        ).fetchone()

    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está desactivada.",
        )

    return {
        "access_token": create_access_token(user["UserID"], user["role"]),
        "token_type": "bearer",
    }


@router.get("/me", response_model=UserOut)
def read_me(user: sqlite3.Row = Depends(get_current_user)) -> dict:
    """Perfil del usuario autenticado."""
    return public_user(user)
