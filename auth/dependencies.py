import sqlite3

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.security import decode_access_token
from db.database import connection

bearer_scheme = HTTPBearer(auto_error=False)

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Credenciales inválidas o sesión expirada.",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> sqlite3.Row:
    """Usuario dueño del token enviado en ``Authorization: Bearer <token>``."""
    if credentials is None:
        raise UNAUTHORIZED

    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise UNAUTHORIZED

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise UNAUTHORIZED from None

    with connection() as conn:
        user = conn.execute(
            "SELECT UserID, name, email, role, is_active, created_at"
            " FROM Users WHERE UserID = ?",
            (user_id,),
        ).fetchone()

    if user is None:
        raise UNAUTHORIZED

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está desactivada.",
        )

    return user


def require_roles(*roles: str):
    """Autoriza solo a los roles indicados.

    Uso: ``Depends(require_roles("admin"))``.
    """

    def dependency(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
        if user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tienes permisos para esta acción.",
            )
        return user

    return dependency
