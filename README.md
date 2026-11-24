# Cyberpeak — Backend

Estado actual: base FastAPI con CORS, conexión a Northwind, autenticación con
JWT (registro, login y perfil), gestión de usuarios para administradores y
analítica (`/metrics`, `/ask`, `/history`).

## Requisitos

- Python 3.12+
- SQLite3 (incluido con Python)

## Base de datos

La API usa `backend/db/northwind.db` (tablas `Categories`, `Customers`,
`Employees`, `OrderDetails`, `Orders`, `Products`, `Shippers`, `Suppliers` y
`Users`). El código no crea tablas ni vistas.

La tabla `Users` guarda las cuentas (`UserID`, `name`, `email` único,
`password_hash`, `role`, `is_active`, `created_at`). La contraseña se guarda
hasheada con bcrypt; nunca en texto plano.

Se puede apuntar a otro archivo con `CYBERPEAK_DB_PATH`.

Comprobar la base:

```powershell
cd backend
.\\.venv\\Scripts\\python.exe -m db.database
```

## Ejecutar la API

```powershell
cd backend
.\\.venv\\Scripts\\python.exe -m uvicorn app:app --reload --port 8000
```

- Documentación interactiva: <http://127.0.0.1:8000/docs>

## Endpoints de autenticación

| Método | Ruta | Cuerpo | Respuesta |
|--------|------|--------|-----------|
| `POST` | `/auth/register` | `{name, email, password}` | `201` usuario creado (sin hash) |
| `POST` | `/auth/login` | `{email, password}` | `200` `{access_token, token_type}` |
| `GET` | `/auth/me` | — (requiere `Authorization: Bearer <token>`) | `200` usuario actual |

Errores: `409` si el correo ya existe, `401` con credenciales incorrectas o
token inválido/expirado, `403` si la cuenta está desactivada.

### Administración de cuentas (solo `admin`)

| Método | Ruta | Cuerpo | Respuesta |
|--------|------|--------|-----------|
| `GET` | `/auth/users` | — | `200` lista de cuentas con `is_active` |
| `PATCH` | `/auth/users/{id}/role` | `{role}` | `200` cuenta actualizada |
| `DELETE` | `/auth/users/{id}` | — | `204` sin cuerpo |

Roles válidos: `user`, `employee`, `admin`. Un `admin` no puede cambiar su
propio rol ni eliminar su propia cuenta (`409`), y siempre debe quedar al menos
un administrador (`409`).

## Analítica

Todas requieren `Authorization: Bearer <token>`.

| Método | Ruta | Cuerpo | Respuesta |
|--------|------|--------|-----------|
| `GET` | `/metrics` | — | `{orders, customers, products, employees}` |
| `POST` | `/ask` | `{question}` | `{question, sql, columns, rows, chart, explanation}` |
| `GET` | `/history` | — | lista de consultas del usuario |

`/ask` usa un catálogo semántico determinista (`analytics/catalog.py`) con 60
intenciones: cada una declara sus palabras clave, su SQL completo, las columnas
que devuelve, su tipo de respuesta y los parámetros que necesita (año, cantidad,
país, categoría, producto, rol, monto, ID de orden). Las entidades de la pregunta
se resuelven contra los valores reales de la base (`analytics/lookups.py`), con
alias en inglés y español y búsqueda por parecido para los nombres de producto
(`Queso Kabrales` → `Queso Cabrales`). El SQL nunca se arma con el texto del
usuario: los valores viajan como parámetros de SQLite.

El año es un filtro opcional en las preguntas de ranking: si la pregunta lo
menciona (`en 1996`) se limita a ese año; si no, se usa toda la historia. Las
preguntas de «el que más/menos vendió» devuelven un único objeto, y las de
«los 5 más vendidos» usan un `LIMIT` con la cantidad pedida (5 por defecto).

Las preguntas de catálogo («lista de productos con precio», «producto e id»)
devuelven solo las columnas pedidas, elegidas de una lista blanca (ID, producto,
precio, categoría, proveedor, unidad), y respetan el orden explícito
(`ordenados por precio de mayor a menor`, `alfabéticamente descendente`).

Si ninguna intención encaja se usa un plan genérico métrica × grupo (ventas,
pedidos o unidades por mes, producto, categoría, empleado, cliente o país). Si
tampoco, responde `200` con `responseType: "unsupported"`, `rows: []` y una
explicación con ejemplos.

### Tipos de respuesta de `/ask`

| `responseType` | Contenido | Ejemplo |
|----------------|-----------|---------|
| `scalar_count` | `value` entero | `91` |
| `scalar_number` | `value` numérico | `28.87` |
| `scalar_date` | `value` fecha ISO | `1997-02-12` |
| `single_row` | `value` objeto | `{ProductName, Price}` |
| `value_list` | `values` lista plana | `["Alfreds Futterkiste", …]` |
| `row_list` | `rows` top N | productos más vendidos |
| `grouped_list` | `rows` agrupadas | facturación por categoría |
| `table` | `rows` de detalle | líneas de una orden |

`rows` siempre lleva las filas crudas (lista de objetos) para que la tabla del
frontend funcione con cualquier tipo; `columns` indica su orden. El gráfico solo
se llena en listas de dos columnas con la segunda numérica.

`/history` devuelve `[]` mientras no exista la tabla de auditoría. El SQL para
crearla está en `db/schema_aurora.sql` (tabla `AuditoriaConsultas`). Una vez
ejecutado, el historial empieza a devolver datos:

Para proteger una ruta usa `Depends(get_current_user)` y, para exigir un rol,
`Depends(require_roles("admin"))` (ambos en `auth/dependencies.py`).

Ejemplo:

```bash
curl -X POST http://127.0.0.1:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"name":"Ana","email":"ana@correo.com","password":"secreto123"}'

curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"ana@correo.com","password":"secreto123"}'
```

## Variables de entorno

| Variable | Por defecto | Uso |
|----------|-------------|-----|
| `CYBERPEAK_DB_PATH` | `backend/db/northwind.db` | Ruta de la base SQLite |
| `CYBERPEAK_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Orígenes CORS separados por coma |
| `CYBERPEAK_SECRET_KEY` | clave de desarrollo | Clave para firmar los JWT (cámbiala en producción) |
| `CYBERPEAK_ACCESS_TOKEN_MINUTES` | `60` | Duración del token en minutos |

## Estructura

```text
backend/
├── app.py              # FastAPI + CORS + routers
├── auth/
│   ├── router.py       # /auth/* (registro, login, perfil y usuarios)
│   ├── schemas.py      # Modelos de entrada y salida
│   ├── security.py     # bcrypt + JWT
│   └── dependencies.py # get_current_user, require_roles
├── analytics/
│   ├── catalog.py      # intenciones: SQL, columnas y tipo de respuesta
│   ├── lookups.py      # países, categorías y roles contra la base
│   ├── router.py       # /metrics, /ask, /history
│   ├── resolver.py     # pregunta → intención + parámetros (solo lectura)
│   └── text.py         # normalización de preguntas
├── db/
│   ├── database.py     # Conexión y verificación de Northwind
│   ├── schema_aurora.sql # Tablas propias (auditoría de consultas)
│   └── northwind.db    # Base de datos
├── semantic/
│   └── model.yaml      # Modelo semántico (pendiente de adaptar a Northwind)
└── requirements.txt
```
