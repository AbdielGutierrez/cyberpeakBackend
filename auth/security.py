"""Hashing de contraseñas y creación/validación de tokens JWT."""

import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

JWT_ALGORITHM = "HS256"

# Solo para desarrollo local: en producción define CYBERPEAK_SECRET_KEY.
DEFAULT_SECRET_KEY = "cyberpeak-dev-secret-change-me"
DEFAULT_ACCESS_TOKEN_MINUTES = 60

# bcrypt: hash lento a propósito, con salt incluido en cada contraseña.
password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def secret_key() -> str:
    """Clave con la que se firman los JWT."""
    return os.getenv("CYBERPEAK_SECRET_KEY", DEFAULT_SECRET_KEY)


def access_token_minutes() -> int:
    """Duración del token en minutos (``CYBERPEAK_ACCESS_TOKEN_MINUTES``)."""
    raw = os.getenv("CYBERPEAK_ACCESS_TOKEN_MINUTES")
    if not raw:
        return DEFAULT_ACCESS_TOKEN_MINUTES

    try:
        minutes = int(raw)
    except ValueError:
        return DEFAULT_ACCESS_TOKEN_MINUTES

    return minutes if minutes > 0 else DEFAULT_ACCESS_TOKEN_MINUTES


def hash_password(password: str) -> str:
    """Devuelve el hash bcrypt de una contraseña en texto plano."""
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Comprueba una contraseña contra su hash."""
    try:
        return password_context.verify(password, password_hash)
    except ValueError:
        # Hash corrupto o con formato desconocido.
        return False


def create_access_token(user_id: int, role: str) -> str:
    """Firma un JWT con el id y el rol del usuario."""
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=access_token_minutes())
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": expires_at,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, secret_key(), algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    """Devuelve el contenido del token o ``None`` si no es válido o expiró."""
    try:
        return jwt.decode(token, secret_key(), algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
