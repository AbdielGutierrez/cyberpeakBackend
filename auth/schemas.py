"""Modelos de entrada y salida de los endpoints de autenticación."""

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Datos del formulario de registro."""

    name: str = Field(min_length=1, max_length=80)
    email: EmailStr
    # bcrypt solo considera los primeros 72 bytes de la contraseña.
    password: str = Field(min_length=8, max_length=72)


class UserLogin(BaseModel):
    """Credenciales del formulario de login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserOut(BaseModel):
    """Usuario tal como se devuelve al cliente (sin el hash)."""

    id: int
    name: str
    email: str
    role: str
    created_at: str


class AdminUserOut(UserOut):
    """Usuario administrable, incluyendo su estado activo."""

    is_active: bool


class RoleUpdate(BaseModel):
    """Nuevo rol asignado a una cuenta."""

    role: str = Field(min_length=2, max_length=20)


class TokenOut(BaseModel):
    """Respuesta del login."""

    access_token: str
    token_type: str = "bearer"
