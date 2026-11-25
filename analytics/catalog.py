"""Catálogo semántico: pregunta → SQL, columnas y tipo de respuesta.

Cada intención declara:

- ``keywords``: fragmentos que deben aparecer en la pregunta normalizada;
  cuantos más, más específica es la intención.
- ``forbid`` / ``regex``: guardas para no robar preguntas a otra intención.
- ``sql``: consulta de solo lectura con ``?`` para los valores del usuario.
- ``columns``: encabezados en el orden del ``SELECT``.
- ``response_type``: forma de la respuesta (ver ``RESPONSE_TYPES``).
- ``params``: valores que se extraen de la pregunta y viajan parametrizados.

El SQL se escribe completo aquí: nunca se concatena texto del usuario.
"""

from dataclasses import dataclass

# Tipos de respuesta soportados.
SCALAR_COUNT = "scalar_count"  # un entero (COUNT)
SCALAR_NUMBER = "scalar_number"  # un número (suma, promedio, porcentaje)
SCALAR_DATE = "scalar_date"  # una fecha ISO
SINGLE_ROW = "single_row"  # un objeto {columna: valor}
ROW_LIST = "row_list"  # lista de objetos acotada (top N)
GROUPED_LIST = "grouped_list"  # lista de objetos agrupada
TABLE = "table"  # lista de objetos de detalle
VALUE_LIST = "value_list"  # lista plana de valores

RESPONSE_TYPES = (
    SCALAR_COUNT,
    SCALAR_NUMBER,
    SCALAR_DATE,
    SINGLE_ROW,
    ROW_LIST,
    GROUPED_LIST,
    TABLE,
    VALUE_LIST,
)


@dataclass(frozen=True)
class Param:
    """Valor que se extrae de la pregunta.

    ``kind``: count | year | optional_year | order_id | price | amount |
    country | category | role.
    """

    name: str
    kind: str
    default: object = None


@dataclass(frozen=True)
class Intent:
    """Una pregunta reconocida con su consulta y su forma de respuesta."""

    id: str
    category: str
    response_type: str
    keywords: tuple[str, ...]
    sql: str
    columns: tuple[str, ...]
    description: str
    params: tuple[Param, ...] = ()
    forbid: tuple[str, ...] = ()
    regex: str | None = None


# Ventas = unidades × precio del catálogo (Northwind no guarda precio por línea).
SALES = "ROUND(SUM(od.Quantity * p.Price), 2)"

SALES_FROM = (
    "FROM Orders o\n"
    "JOIN OrderDetails od ON od.OrderID = o.OrderID\n"
    "JOIN Products p ON p.ProductID = od.ProductID"
)

SALES_FROM_CUSTOMER = (
    "FROM Customers cu\n"
    "JOIN Orders o ON o.CustomerID = cu.CustomerID\n"
    "JOIN OrderDetails od ON od.OrderID = o.OrderID\n"
    "JOIN Products p ON p.ProductID = od.ProductID"
)

SALES_FROM_EMPLOYEE = (
    "FROM Employees e\n"
    "JOIN Orders o ON o.EmployeeID = e.EmployeeID\n"
    "JOIN OrderDetails od ON od.OrderID = o.OrderID\n"
    "JOIN Products p ON p.ProductID = od.ProductID"
)

LAST_YEAR = "(SELECT strftime('%Y', MAX(OrderDate)) FROM Orders)"

# Producto más o menos vendido de cada categoría (``direction``: ASC o DESC).
CATEGORY_PRODUCT_RANK = (
    "WITH ranking AS (\n"
    "  SELECT c.CategoryName AS categoria,\n"
    "         p.ProductName AS producto,\n"
    "         COALESCE(SUM(od.Quantity), 0) AS unidades_vendidas,\n"
    "         ROW_NUMBER() OVER (\n"
    "             PARTITION BY c.CategoryID\n"
    "             ORDER BY COALESCE(SUM(od.Quantity), 0) {direction},\n"
    "                      p.ProductName) AS posicion\n"
    "  FROM Categories c\n"
    "  JOIN Products p ON p.CategoryID = c.CategoryID\n"
    "  LEFT JOIN OrderDetails od ON od.ProductID = p.ProductID\n"
    "  GROUP BY p.ProductID\n"
    ")\n"
    "SELECT categoria, producto, unidades_vendidas\n"
    "FROM ranking\n"
    "WHERE posicion = 1\n"
    "ORDER BY categoria"
)

INTENTS: tuple[Intent, ...] = (
    # ---------------------------------------------------------------- Clientes
    Intent(
        id="clientes_total",
        category="Clientes",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "cliente"),
        forbid=("nunca", "pais", "ciudad", "gast", "orden", "pedido", "compr"),
        sql="SELECT COUNT(*) AS total_clientes FROM Customers",
        columns=("total_clientes",),
        description="Número entero con el total de clientes registrados.",
    ),
    Intent(
        id="empleados_total",
        category="Empleados",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "empleado"),
        forbid=("vend", "nac", "orden", "rol", "mas", "menos", "por",
                "pais", "ciudad"),
        sql="SELECT COUNT(*) AS total_empleados FROM Employees",
        columns=("total_empleados",),
        description="Número entero con el total de empleados de la empresa.",
    ),
    Intent(
        id="clientes_sin_ordenes",
        category="Clientes",
        response_type=VALUE_LIST,
        keywords=("cliente", "nunca"),
        regex=r"orden|pedido|compra",
        sql=(
            "SELECT cu.CustomerName\n"
            "FROM Customers cu\n"
            "LEFT JOIN Orders o ON o.CustomerID = cu.CustomerID\n"
            "WHERE o.OrderID IS NULL\n"
            "ORDER BY cu.CustomerName"
        ),
        columns=("CustomerName",),
        description="Lista con los nombres de los clientes sin pedidos registrados.",
    ),
    Intent(
        id="clientes_por_pais",
        category="Clientes",
        response_type=VALUE_LIST,
        keywords=("cliente",),
        regex=r"pais|ubicad|viven|residen|estan en|son de|de alemania|de espana",
        params=(Param("country", "country"),),
        sql=(
            "SELECT CustomerName\n"
            "FROM Customers\n"
            "WHERE Country = ?\n"
            "ORDER BY CustomerName"
        ),
        columns=("CustomerName",),
        description="Lista con los nombres de los clientes del país indicado.",
    ),
    Intent(
        id="cliente_mas_gasto",
        category="Clientes",
        response_type=SINGLE_ROW,
        keywords=("cliente", "gast"),
        sql=(
            "SELECT cu.CustomerName AS cliente,\n"
            "       ROUND(SUM(od.Quantity * p.Price), 2) AS total_gastado\n"
            f"{SALES_FROM_CUSTOMER}\n"
            "GROUP BY cu.CustomerID\n"
            "ORDER BY total_gastado DESC\n"
            "LIMIT 1"
        ),
        columns=("cliente", "total_gastado"),
        description="Cliente con mayor facturación y el total gastado.",
    ),
    Intent(
        id="top_clientes_ordenes",
        category="Clientes",
        response_type=ROW_LIST,
        keywords=("cliente", "orden"),
        regex=r"mas|top|mayor|ranking",
        params=(Param("count", "count", 5),),
        sql=(
            "SELECT cu.CustomerName AS cliente,\n"
            "       COUNT(o.OrderID) AS total_ordenes\n"
            "FROM Customers cu\n"
            "JOIN Orders o ON o.CustomerID = cu.CustomerID\n"
            "GROUP BY cu.CustomerID\n"
            "ORDER BY total_ordenes DESC\n"
            "LIMIT ?"
        ),
        columns=("cliente", "total_ordenes"),
        description="Clientes con más órdenes y su cantidad de órdenes.",
    ),
    Intent(
        id="pedidos_por_pais",
        category="Clientes",
        response_type=GROUPED_LIST,
        keywords=("pais", "pedido"),
        sql=(
            "SELECT cu.Country AS pais,\n"
            "       COUNT(o.OrderID) AS cantidad_pedidos\n"
            "FROM Customers cu\n"
            "JOIN Orders o ON o.CustomerID = cu.CustomerID\n"
            "GROUP BY cu.Country\n"
            "ORDER BY cantidad_pedidos DESC"
        ),
        columns=("pais", "cantidad_pedidos"),
        description="Lista de países con su cantidad de pedidos.",
    ),
    Intent(
        id="unidades_por_cliente",
        category="Clientes",
        response_type=GROUPED_LIST,
        keywords=("unidad", "cliente"),
        params=(Param("product", "product_or_all"), Param("count", "count", 10)),
        sql=(
            "SELECT cu.CustomerName AS cliente,\n"
            "       SUM(od.Quantity) AS unidades_totales\n"
            "FROM Customers cu\n"
            "JOIN Orders o ON o.CustomerID = cu.CustomerID\n"
            "JOIN OrderDetails od ON od.OrderID = o.OrderID\n"
            "JOIN Products p ON p.ProductID = od.ProductID\n"
            "WHERE (? IS NULL OR p.ProductName = ?)\n"
            "GROUP BY cu.CustomerID\n"
            "ORDER BY unidades_totales DESC\n"
            "LIMIT ?"
        ),
        columns=("cliente", "unidades_totales"),
        description=(
            "Clientes con más unidades compradas (del producto indicado, si lo hay)."
        ),
    ),
    Intent(
        id="clientes_que_compraron_producto",
        category="Clientes",
        response_type=ROW_LIST,
        keywords=("cliente",),
        regex=r"compr|pidier|realizar|orden|pedido|consum|solicitar",
        forbid=("nunca",),
        params=(Param("product", "product"), Param("count", "count", 10)),
        sql=(
            "SELECT cu.CustomerName AS cliente,\n"
            "       SUM(od.Quantity) AS unidades_compradas,\n"
            f"       {SALES} AS total_gastado\n"
            "FROM Customers cu\n"
            "JOIN Orders o ON o.CustomerID = cu.CustomerID\n"
            "JOIN OrderDetails od ON od.OrderID = o.OrderID\n"
            "JOIN Products p ON p.ProductID = od.ProductID\n"
            "WHERE p.ProductName = ?\n"
            "GROUP BY cu.CustomerID\n"
            "ORDER BY unidades_compradas DESC\n"
            "LIMIT ?"
        ),
        columns=("cliente", "unidades_compradas", "total_gastado"),
        description="Clientes que compraron el producto indicado.",
    ),
    Intent(
        id="clientes_que_compraron_producto_total",
        category="Clientes",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "cliente"),
        regex=r"compr|pidier|orden|pedido|consum",
        forbid=("nunca", "pais", "ciudad", "gast", "cada", "unidade"),
        params=(Param("product", "product"),),
        sql=(
            "SELECT COUNT(DISTINCT cu.CustomerID) AS total_clientes\n"
            "FROM Customers cu\n"
            "JOIN Orders o ON o.CustomerID = cu.CustomerID\n"
            "JOIN OrderDetails od ON od.OrderID = o.OrderID\n"
            "JOIN Products p ON p.ProductID = od.ProductID\n"
            "WHERE p.ProductName = ?"
        ),
        columns=("total_clientes",),
        description="Cantidad de clientes que compraron el producto indicado.",
    ),
    Intent(
        id="ciudades_mas_clientes",
        category="Clientes",
        response_type=GROUPED_LIST,
        keywords=("ciudad", "cliente"),
        sql=(
            "SELECT City AS ciudad, COUNT(*) AS total_clientes\n"
            "FROM Customers\n"
            "GROUP BY City\n"
            "ORDER BY total_clientes DESC\n"
            "LIMIT 10"
        ),
        columns=("ciudad", "total_clientes"),
        description="Top 10 de ciudades con más clientes registrados.",
    ),
    # --------------------------------------------------------------- Productos
    Intent(
        id="productos_total",
        category="Productos",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "producto"),
        forbid=("categoria", "vend", "precio", "proveedor", "mas", "menos",
                "por"),
        sql="SELECT COUNT(*) AS total_productos FROM Products",
        columns=("total_productos",),
        description="Número entero con el total de productos del catálogo.",
    ),
    Intent(
        id="productos_nunca_vendidos",
        category="Productos",
        response_type=VALUE_LIST,
        keywords=("producto", "nunca"),
        regex=r"vend",
        sql=(
            "SELECT p.ProductName\n"
            "FROM Products p\n"
            "LEFT JOIN OrderDetails od ON od.ProductID = p.ProductID\n"
            "WHERE od.OrderDetailID IS NULL\n"
            "ORDER BY p.ProductName"
        ),
        columns=("ProductName",),
        description="Lista de productos que no tienen ninguna venta registrada.",
    ),
    Intent(
        id="producto_mas_caro_categoria",
        category="Productos",
        response_type=TABLE,
        keywords=("producto", "caro", "cada"),
        sql=(
            "SELECT c.CategoryName AS categoria,\n"
            "       p.ProductName AS producto,\n"
            "       p.Price AS precio\n"
            "FROM Categories c\n"
            "JOIN Products p ON p.CategoryID = c.CategoryID\n"
            "WHERE p.Price = (SELECT MAX(p2.Price)\n"
            "                 FROM Products p2\n"
            "                 WHERE p2.CategoryID = c.CategoryID)\n"
            "ORDER BY c.CategoryName"
        ),
        columns=("categoria", "producto", "precio"),
        description="Producto más caro de cada categoría con su precio.",
    ),
    Intent(
        id="producto_mas_caro",
        category="Productos",
        response_type=SINGLE_ROW,
        keywords=("producto", "caro"),
        forbid=("cada",),
        sql=(
            "SELECT ProductName, Price\n"
            "FROM Products\n"
            "ORDER BY Price DESC\n"
            "LIMIT 1"
        ),
        columns=("ProductName", "Price"),
        description="Producto más caro del catálogo y su precio.",
    ),
    Intent(
        id="productos_por_categoria",
        category="Productos",
        response_type=GROUPED_LIST,
        keywords=("cuantos", "producto", "categoria"),
        forbid=("pertenec", "especifica", "caro", "menos", "factur"),
        sql=(
            "SELECT c.CategoryName AS categoria,\n"
            "       COUNT(p.ProductID) AS total_productos\n"
            "FROM Categories c\n"
            "LEFT JOIN Products p ON p.CategoryID = c.CategoryID\n"
            "GROUP BY c.CategoryID\n"
            "ORDER BY total_productos DESC"
        ),
        columns=("categoria", "total_productos"),
        description="Lista de categorías con la cantidad de productos asociados.",
    ),
    Intent(
        id="productos_de_categoria",
        category="Productos",
        response_type=ROW_LIST,
        keywords=("producto",),
        regex=r"pertenec|categoria|hay en",
        forbid=("caro", "menos", "vend", "factur"),
        params=(Param("category", "category"),),
        sql=(
            "SELECT p.ProductName, p.Price, p.Unit\n"
            "FROM Products p\n"
            "JOIN Categories c ON p.CategoryID = c.CategoryID\n"
            "WHERE c.CategoryName = ?\n"
            "ORDER BY p.ProductName"
        ),
        columns=("ProductName", "Price", "Unit"),
        description="Productos de la categoría indicada con precio y unidad.",
    ),
    Intent(
        id="producto_menos_vendido_categoria",
        category="Productos",
        response_type=SINGLE_ROW,
        keywords=("producto", "menos", "vend", "categoria"),
        params=(Param("category", "category"),),
        sql=(
            "SELECT p.ProductName,\n"
            "       COALESCE(SUM(od.Quantity), 0) AS unidades_vendidas\n"
            "FROM Products p\n"
            "JOIN Categories c ON p.CategoryID = c.CategoryID\n"
            "LEFT JOIN OrderDetails od ON p.ProductID = od.ProductID\n"
            "WHERE c.CategoryName = ?\n"
            "GROUP BY p.ProductID\n"
            "ORDER BY unidades_vendidas ASC\n"
            "LIMIT 1"
        ),
        columns=("ProductName", "unidades_vendidas"),
        description="Producto menos vendido de la categoría indicada.",
    ),
    Intent(
        id="top_productos_unidades",
        category="Productos",
        response_type=ROW_LIST,
        keywords=("producto", "vend"),
        regex=r"mas|top|mayor|ranking|unidade|cantidad",
        forbid=("nunca", "menos", "caro"),
        params=(Param("year", "year_or_all"), Param("count", "count", 5)),
        sql=(
            "SELECT p.ProductName AS producto,\n"
            "       SUM(od.Quantity) AS unidades_vendidas\n"
            "FROM OrderDetails od\n"
            "JOIN Products p ON p.ProductID = od.ProductID\n"
            "JOIN Orders o ON o.OrderID = od.OrderID\n"
            "WHERE (? IS NULL OR strftime('%Y', o.OrderDate) = ?)\n"
            "GROUP BY p.ProductID\n"
            "ORDER BY unidades_vendidas DESC\n"
            "LIMIT ?"
        ),
        columns=("producto", "unidades_vendidas"),
        description="Productos más vendidos por unidades.",
    ),
    Intent(
        id="top_productos_ventas",
        category="Productos",
        response_type=ROW_LIST,
        keywords=("producto", "venta"),
        regex=r"mas|top|mayor|ranking|genera|factur",
        forbid=("nunca", "menos", "caro"),
        params=(Param("year", "year_or_all"), Param("count", "count", 5)),
        sql=(
            "SELECT p.ProductName AS producto,\n"
            f"       {SALES} AS total_vendido\n"
            "FROM OrderDetails od\n"
            "JOIN Products p ON p.ProductID = od.ProductID\n"
            "JOIN Orders o ON o.OrderID = od.OrderID\n"
            "WHERE (? IS NULL OR strftime('%Y', o.OrderDate) = ?)\n"
            "GROUP BY p.ProductID\n"
            "ORDER BY total_vendido DESC\n"
            "LIMIT ?"
        ),
        columns=("producto", "total_vendido"),
        description="Productos con más facturación generada.",
    ),
    Intent(
        id="producto_mas_vendido",
        category="Productos",
        response_type=SINGLE_ROW,
        keywords=("producto",),
        regex=r"producto (que )?(mas|mayor) (vend|factur)|producto con (mas|mayor)",
        forbid=(
            "menos",
            "nunca",
            "cada",
            "caro",
            "cuales",
            "son los",
            "dame",
            "lista",
            "top",
        ),
        params=(Param("year", "year_or_all"),),
        sql=(
            "SELECT p.ProductName AS producto,\n"
            "       SUM(od.Quantity) AS unidades_vendidas,\n"
            "       p.Price AS precio\n"
            "FROM OrderDetails od\n"
            "JOIN Products p ON p.ProductID = od.ProductID\n"
            "JOIN Orders o ON o.OrderID = od.OrderID\n"
            "WHERE (? IS NULL OR strftime('%Y', o.OrderDate) = ?)\n"
            "GROUP BY p.ProductID\n"
            "ORDER BY unidades_vendidas DESC\n"
            "LIMIT 1"
        ),
        columns=("producto", "unidades_vendidas", "precio"),
        description="Producto que más unidades vendió (del año indicado, si lo hay).",
    ),
    Intent(
        id="producto_menos_vendido",
        category="Productos",
        response_type=SINGLE_ROW,
        keywords=("producto",),
        regex=r"producto (que )?menos (vend|factur)|producto menos",
        forbid=("cada", "categoria", "nunca"),
        params=(Param("year", "year_or_all"),),
        sql=(
            "SELECT p.ProductName AS producto,\n"
            "       COALESCE(SUM(od.Quantity), 0) AS unidades_vendidas,\n"
            "       p.Price AS precio\n"
            "FROM Products p\n"
            "LEFT JOIN OrderDetails od ON od.ProductID = p.ProductID\n"
            "LEFT JOIN Orders o ON o.OrderID = od.OrderID\n"
            "WHERE (? IS NULL\n"
            "       OR strftime('%Y', o.OrderDate) = ?)\n"
            "GROUP BY p.ProductID\n"
            "ORDER BY unidades_vendidas ASC\n"
            "LIMIT 1"
        ),
        columns=("producto", "unidades_vendidas", "precio"),
        description="Producto que menos unidades vendió (del año indicado, si lo hay).",
    ),
    Intent(
        id="productos_mas_vendidos_por_categoria",
        category="Productos",
        response_type=TABLE,
        keywords=("producto", "mas", "vend", "cada", "categoria"),
        forbid=("nunca", "caro"),
        sql=CATEGORY_PRODUCT_RANK.format(direction="DESC"),
        columns=("categoria", "producto", "unidades_vendidas"),
        description="Producto más vendido de cada categoría.",
    ),
    Intent(
        id="productos_menos_vendidos_por_categoria",
        category="Productos",
        response_type=TABLE,
        keywords=("producto", "menos", "vend", "cada", "categoria"),
        sql=CATEGORY_PRODUCT_RANK.format(direction="ASC"),
        columns=("categoria", "producto", "unidades_vendidas"),
        description="Producto menos vendido de cada categoría.",
    ),
    Intent(
        id="categoria_de_producto",
        category="Productos",
        response_type=SINGLE_ROW,
        keywords=("categoria",),
        regex=r"categoria (es|son|tiene|del|de|pertenece|corresponde)",
        forbid=("cuantos", "cada", "vend", "factur", "precio promedio",
                "mas caro"),
        params=(Param("product", "product"),),
        sql=(
            "SELECT p.ProductName AS producto,\n"
            "       c.CategoryName AS categoria,\n"
            "       p.Price AS precio\n"
            "FROM Products p\n"
            "JOIN Categories c ON c.CategoryID = p.CategoryID\n"
            "WHERE p.ProductName = ?"
        ),
        columns=("producto", "categoria", "precio"),
        description="Categoría a la que pertenece el producto indicado.",
    ),
    Intent(
        id="precio_promedio",
        category="Productos",
        response_type=SCALAR_NUMBER,
        keywords=("precio", "promedio"),
        forbid=("categoria",),
        sql="SELECT ROUND(AVG(Price), 2) AS precio_promedio FROM Products",
        columns=("precio_promedio",),
        description="Precio promedio de todos los productos del catálogo.",
    ),
    Intent(
        id="categoria_precio_promedio_alto",
        category="Categorías",
        response_type=SINGLE_ROW,
        keywords=("categoria", "precio", "promedio"),
        regex=r"alto|mayor|mas",
        sql=(
            "SELECT c.CategoryName AS categoria,\n"
            "       ROUND(AVG(p.Price), 2) AS precio_promedio\n"
            "FROM Categories c\n"
            "JOIN Products p ON c.CategoryID = p.CategoryID\n"
            "GROUP BY c.CategoryID\n"
            "ORDER BY precio_promedio DESC\n"
            "LIMIT 1"
        ),
        columns=("categoria", "precio_promedio"),
        description="Categoría con el precio promedio más alto.",
    ),
    Intent(
        id="porcentaje_productos_caros",
        category="Productos",
        response_type=SCALAR_NUMBER,
        keywords=("porcentaje",),
        regex=r"cuest|precio|caro",
        params=(Param("price", "price"),),
        sql=(
            "SELECT ROUND(100.0 * SUM(CASE WHEN Price > ? THEN 1 ELSE 0 END)\n"
            "             / COUNT(*), 2) AS porcentaje\n"
            "FROM Products"
        ),
        columns=("porcentaje",),
        description="Porcentaje de productos con precio superior al valor dado.",
    ),
    # -------------------------------------------------------------- Categorías
    Intent(
        id="facturacion_por_categoria",
        category="Categorías",
        response_type=GROUPED_LIST,
        keywords=("categoria",),
        regex=r"vend|factur|ingres|cuanto",
        forbid=("cuantos productos", "nunca", "precio promedio", "mas caro"),
        sql=(
            "SELECT c.CategoryName AS categoria,\n"
            f"       {SALES} AS total_vendido\n"
            "FROM Categories c\n"
            "JOIN Products p ON p.CategoryID = c.CategoryID\n"
            "JOIN OrderDetails od ON od.ProductID = p.ProductID\n"
            "GROUP BY c.CategoryID\n"
            "ORDER BY total_vendido DESC"
        ),
        columns=("categoria", "total_vendido"),
        description="Categorías con su facturación total, de mayor a menor.",
    ),
    # --------------------------------------------------------------- Empleados
    Intent(
        id="empleados_lista",
        category="Empleados",
        response_type=ROW_LIST,
        keywords=("emplead",),
        regex=r"lista|listado|todos|todas|dame|muestra|quienes|catalogo",
        forbid=("vend", "factur", "ingres", "orden", "pedido", "cuant", "nac",
                "mas", "menos", "por", "rol", "monto", "gestion"),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       e.BirthDate AS nacimiento\n"
            "FROM Employees e\n"
            "ORDER BY empleado"
        ),
        columns=("empleado", "nacimiento"),
        description="Listado de empleados con su fecha de nacimiento.",
    ),
    Intent(
        id="empleado_por_nombre",
        category="Empleados",
        response_type=SINGLE_ROW,
        keywords=("emplead",),
        forbid=("vend", "venta", "factur", "ingres", "orden", "pedido", "cuant",
                "nac", "rol", "mas", "menos", "top", "total", "por", "gast"),
        params=(Param("employee", "employee"),),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       e.BirthDate AS nacimiento\n"
            "FROM Employees e\n"
            "WHERE e.EmployeeID = (\n"
            "    SELECT EmployeeID FROM Employees\n"
            "    WHERE FirstName || ' ' || LastName = ?"
            ")"
        ),
        columns=("empleado", "nacimiento"),
        description="Ficha del empleado indicado.",
    ),
    Intent(
        id="empleados_que_vendieron_producto",
        category="Empleados",
        response_type=ROW_LIST,
        keywords=("emplead", "vend"),
        forbid=("mas", "mayor", "menos", "top", "mejor", "peor", "unidade"),
        params=(Param("product", "product"), Param("count", "count", 10)),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       SUM(od.Quantity) AS unidades_vendidas,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "WHERE p.ProductName = ?\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY unidades_vendidas DESC\n"
            "LIMIT ?"
        ),
        columns=("empleado", "unidades_vendidas", "total_vendido"),
        description="Empleados que vendieron el producto indicado.",
    ),
    Intent(
        id="empleados_unidades_producto",
        category="Empleados",
        response_type=ROW_LIST,
        keywords=("emplead", "vend", "unidade"),
        regex=r"vendieron \d+|vendio \d+|mas de|al menos|por encima",
        forbid=("mayor", "menos", "top", "mejor", "peor"),
        params=(Param("product", "product"), Param("amount", "count", 1)),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       SUM(od.Quantity) AS unidades_vendidas,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "WHERE p.ProductName = ?\n"
            "GROUP BY e.EmployeeID\n"
            "HAVING SUM(od.Quantity) >= ?\n"
            "ORDER BY unidades_vendidas DESC"
        ),
        columns=("empleado", "unidades_vendidas", "total_vendido"),
        description=(
            "Empleados que vendieron al menos las unidades indicadas del producto."
        ),
    ),
    Intent(
        id="empleados_que_vendieron_producto_total",
        category="Empleados",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "emplead", "vend"),
        params=(Param("product", "product"),),
        sql=(
            "SELECT COUNT(DISTINCT e.EmployeeID) AS total_empleados\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "WHERE p.ProductName = ?"
        ),
        columns=("total_empleados",),
        description="Cantidad de empleados que vendieron el producto indicado.",
    ),
    Intent(
        id="empleado_mas_ordenes",
        category="Empleados",
        response_type=SINGLE_ROW,
        keywords=("empleado", "orden"),
        forbid=("transportista",),
        regex=r"mas|mayor|gestionad",
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       COUNT(o.OrderID) AS total_ordenes\n"
            "FROM Employees e\n"
            "JOIN Orders o ON e.EmployeeID = o.EmployeeID\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY total_ordenes DESC\n"
            "LIMIT 1"
        ),
        columns=("empleado", "total_ordenes"),
        description="Empleado que gestionó más órdenes y su cantidad.",
    ),
    Intent(
        id="empleado_mas_vendido",
        category="Empleados",
        response_type=SINGLE_ROW,
        keywords=("empleado", "vend"),
        regex=r"mas|mayor|top",
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY total_vendido DESC\n"
            "LIMIT 1"
        ),
        columns=("empleado", "total_vendido"),
        description="Empleado con mayor facturación generada.",
    ),
    Intent(
        id="empleado_menos_vendido",
        category="Empleados",
        response_type=SINGLE_ROW,
        keywords=("empleado", "vend"),
        regex=r"menos|peor",
        forbid=("producto", "orden"),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY total_vendido ASC\n"
            "LIMIT 1"
        ),
        columns=("empleado", "total_vendido"),
        description="Empleado con la menor facturación generada.",
    ),
    Intent(
        id="empleado_mas_vendido_producto",
        category="Empleados",
        response_type=SINGLE_ROW,
        keywords=("empleado", "vend"),
        regex=r"mas|mayor|top",
        params=(Param("product", "product"), Param("year", "year_or_all")),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       SUM(od.Quantity) AS unidades_vendidas,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "WHERE p.ProductName = ?\n"
            "  AND (? IS NULL OR strftime('%Y', o.OrderDate) = ?)\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY unidades_vendidas DESC\n"
            "LIMIT 1"
        ),
        columns=("empleado", "unidades_vendidas", "total_vendido"),
        description="Empleado que más vendió del producto indicado.",
    ),
    Intent(
        id="empleado_menos_vendido_producto",
        category="Empleados",
        response_type=SINGLE_ROW,
        keywords=("empleado", "vend"),
        regex=r"menos|peor",
        params=(Param("product", "product"), Param("year", "year_or_all")),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       SUM(od.Quantity) AS unidades_vendidas,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "WHERE p.ProductName = ?\n"
            "  AND (? IS NULL OR strftime('%Y', o.OrderDate) = ?)\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY unidades_vendidas ASC\n"
            "LIMIT 1"
        ),
        columns=("empleado", "unidades_vendidas", "total_vendido"),
        description="Empleado que menos vendió del producto indicado.",
    ),
    Intent(
        id="top_empleados_ventas",
        category="Empleados",
        response_type=ROW_LIST,
        keywords=("empleado", "vend"),
        regex=r"[0-9]+ empleado|empleados que (mas|mayor)|top [0-9]",
        forbid=("menos", "orden", "gestion", "producto"),
        params=(Param("count", "count", 5),),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY total_vendido DESC\n"
            "LIMIT ?"
        ),
        columns=("empleado", "total_vendido"),
        description="Empleados con más facturación generada.",
    ),
    Intent(
        id="ingreso_por_empleado",
        category="Empleados",
        response_type=GROUPED_LIST,
        keywords=("empleado", "ingreso"),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            f"       {SALES} AS ingreso_total\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "GROUP BY e.EmployeeID\n"
            "ORDER BY ingreso_total DESC"
        ),
        columns=("empleado", "ingreso_total"),
        description="Ingreso total generado por cada empleado.",
    ),
    Intent(
        id="empleados_sobre_monto",
        category="Empleados",
        response_type=ROW_LIST,
        keywords=("empleado", "monto"),
        params=(Param("year", "year"), Param("amount", "amount")),
        sql=(
            "SELECT e.FirstName || ' ' || e.LastName AS empleado,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM_EMPLOYEE}\n"
            "WHERE strftime('%Y', o.OrderDate) = ?\n"
            "GROUP BY e.EmployeeID\n"
            "HAVING total_vendido > ?\n"
            "ORDER BY total_vendido DESC"
        ),
        columns=("empleado", "total_vendido"),
        description="Empleados que superaron el monto indicado en el año dado.",
    ),
    Intent(
        id="empleados_nacidos_antes",
        category="Empleados",
        response_type=ROW_LIST,
        keywords=("empleado", "nac"),
        params=(Param("year", "year_start"),),
        sql=(
            "SELECT FirstName || ' ' || LastName AS empleado, BirthDate\n"
            "FROM Employees\n"
            "WHERE BirthDate < ?\n"
            "ORDER BY BirthDate"
        ),
        columns=("empleado", "BirthDate"),
        description="Empleados nacidos antes del año indicado.",
    ),
    # -------------------------------------------------------------------- Ventas
    Intent(
        id="ventas_totales",
        category="Ventas generales",
        response_type=SCALAR_NUMBER,
        keywords=("venta",),
        regex=r"total|empresa|negocio|general|toda",
        forbid=("por", "cada", "mes", "categoria", "producto", "empleado",
                "cliente", "pais", "transportista"),
        sql=f"SELECT {SALES} AS ventas_totales\n{SALES_FROM}",
        columns=("ventas_totales",),
        description="Facturación total del negocio.",
    ),
    Intent(
        id="ingreso_ultimo_anio",
        category="Ventas generales",
        response_type=SCALAR_NUMBER,
        keywords=("ingreso", "ultimo", "ano"),
        sql=(
            f"SELECT {SALES} AS ingreso_total\n"
            f"{SALES_FROM}\n"
            f"WHERE strftime('%Y', o.OrderDate) = {LAST_YEAR}"
        ),
        columns=("ingreso_total",),
        description="Facturación total del último año con datos.",
    ),
    Intent(
        id="ventas_por_mes_anio",
        category="Tendencias temporales",
        response_type=TABLE,
        keywords=("mes",),
        regex=r"vend|factur|ingres",
        params=(Param("year", "optional_year"),),
        sql=(
            "SELECT strftime('%Y-%m', o.OrderDate) AS mes,\n"
            f"       {SALES} AS total_vendido\n"
            f"{SALES_FROM}\n"
            "WHERE strftime('%Y', o.OrderDate) = ?\n"
            "GROUP BY mes\n"
            "ORDER BY mes"
        ),
        columns=("mes", "total_vendido"),
        description="Ventas mes a mes del año indicado.",
    ),
    Intent(
        id="ticket_promedio",
        category="Ventas generales",
        response_type=SCALAR_NUMBER,
        keywords=("promedio",),
        regex=r"pedido|orden|valor|ticket",
        forbid=("dias",),
        sql=(
            "SELECT ROUND(AVG(total_pedido), 2) AS ticket_promedio\n"
            "FROM (SELECT o.OrderID,\n"
            f"             {SALES} AS total_pedido\n"
            f"      {SALES_FROM}\n"
            "      GROUP BY o.OrderID)"
        ),
        columns=("ticket_promedio",),
        description="Valor promedio de un pedido.",
    ),
    Intent(
        id="promedio_dias_entre_ordenes",
        category="Tendencias temporales",
        response_type=SCALAR_NUMBER,
        keywords=("promedio", "dias"),
        sql=(
            "SELECT ROUND(AVG(dias), 2) AS promedio_dias\n"
            "FROM (SELECT julianday(OrderDate)\n"
            "             - julianday(LAG(OrderDate) OVER (\n"
            "                   PARTITION BY CustomerID ORDER BY OrderDate)) AS dias\n"
            "      FROM Orders)\n"
            "WHERE dias IS NOT NULL"
        ),
        columns=("promedio_dias",),
        description="Promedio de días entre órdenes consecutivas de un cliente.",
    ),
    Intent(
        id="ordenes_total",
        category="Ventas generales",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "orden"),
        forbid=("transportista", "empleado", "cliente", "producto", "pais"),
        sql="SELECT COUNT(*) AS total_ordenes FROM Orders",
        columns=("total_ordenes",),
        description="Cantidad total de órdenes registradas.",
    ),
    Intent(
        id="ultima_orden",
        category="Ventas generales",
        response_type=SCALAR_DATE,
        keywords=("ultima", "orden"),
        sql="SELECT MAX(OrderDate) AS ultima_orden FROM Orders",
        columns=("ultima_orden",),
        description="Fecha de la orden más reciente registrada.",
    ),
    Intent(
        id="ordenes_por_anio",
        category="Ventas generales",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "orden"),
        forbid=("transportista", "empleado", "cliente", "producto"),
        params=(Param("year", "year"),),
        sql=(
            "SELECT COUNT(*) AS total_ordenes\n"
            "FROM Orders\n"
            "WHERE strftime('%Y', OrderDate) = ?"
        ),
        columns=("total_ordenes",),
        description="Cantidad de órdenes realizadas en el año indicado.",
    ),
    # --------------------------------------------------------- Logística y otros
    Intent(
        id="proveedores_total",
        category="Proveedores",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "proveedor"),
        forbid=("producto", "vend", "ingreso", "mas", "menos", "por"),
        sql="SELECT COUNT(*) AS total_proveedores FROM Suppliers",
        columns=("total_proveedores",),
        description="Número entero con el total de proveedores.",
    ),
    Intent(
        id="proveedores_lista",
        category="Proveedores",
        response_type=ROW_LIST,
        keywords=("proveedor",),
        forbid=("cuant", "total", "mas", "menos", "producto", "ingreso",
                "factur"),
        sql=(
            "SELECT SupplierName AS proveedor,\n"
            "       Country AS pais,\n"
            "       City AS ciudad\n"
            "FROM Suppliers\n"
        ),
        columns=("proveedor", "pais", "ciudad"),
        description="Listado de proveedores con su país y ciudad.",
    ),
    Intent(
        id="transportistas_total",
        category="Logística",
        response_type=SCALAR_COUNT,
        keywords=("cuant", "transportista"),
        forbid=("orden", "pedido", "mas", "menos", "env"),
        sql="SELECT COUNT(*) AS total_transportistas FROM Shippers",
        columns=("total_transportistas",),
        description="Número entero con el total de transportistas.",
    ),
    Intent(
        id="ordenes_por_transportista_ultimo_anio",
        category="Logística",
        response_type=GROUPED_LIST,
        keywords=("transportista",),
        regex=r"orden|pedido|env",
        forbid=("mas", "mayor", "top"),
        sql=(
            "SELECT s.ShipperName AS transportista,\n"
            "       COUNT(o.OrderID) AS total_ordenes\n"
            "FROM Shippers s\n"
            "JOIN Orders o ON s.ShipperID = o.ShipperID\n"
            f"WHERE strftime('%Y', o.OrderDate) = {LAST_YEAR}\n"
            "GROUP BY s.ShipperID\n"
            "ORDER BY total_ordenes DESC"
        ),
        columns=("transportista", "total_ordenes"),
        description="Órdenes enviadas por cada transportista en el último año.",
    ),
    Intent(
        id="transportista_mas_ordenes",
        category="Logística",
        response_type=SINGLE_ROW,
        keywords=("transportista",),
        regex=r"mas|mayor|top|gestion|envi",
        sql=(
            "SELECT s.ShipperName AS transportista,\n"
            "       COUNT(o.OrderID) AS total_ordenes\n"
            "FROM Shippers s\n"
            "JOIN Orders o ON s.ShipperID = o.ShipperID\n"
            "GROUP BY s.ShipperID\n"
            "ORDER BY total_ordenes DESC\n"
            "LIMIT 1"
        ),
        columns=("transportista", "total_ordenes"),
        description="Transportista con más órdenes gestionadas.",
    ),
    Intent(
        id="proveedor_mas_productos",
        category="Proveedores",
        response_type=SINGLE_ROW,
        keywords=("proveedor", "producto"),
        forbid=("ingreso", "factur"),
        sql=(
            "SELECT s.SupplierName AS proveedor,\n"
            "       COUNT(p.ProductID) AS total_productos\n"
            "FROM Suppliers s\n"
            "JOIN Products p ON p.SupplierID = s.SupplierID\n"
            "GROUP BY s.SupplierID\n"
            "ORDER BY total_productos DESC\n"
            "LIMIT 1"
        ),
        columns=("proveedor", "total_productos"),
        description="Proveedor con más productos en catálogo.",
    ),
    Intent(
        id="proveedor_mas_ingresos",
        category="Proveedores",
        response_type=SINGLE_ROW,
        keywords=("proveedor", "ingreso"),
        sql=(
            "SELECT su.SupplierName AS proveedor,\n"
            "       su.Country AS pais,\n"
            "       ROUND(SUM(od.Quantity * p.Price), 2) AS ingresos\n"
            "FROM Suppliers su\n"
            "JOIN Products p ON p.SupplierID = su.SupplierID\n"
            "JOIN OrderDetails od ON od.ProductID = p.ProductID\n"
            "GROUP BY su.SupplierID\n"
            "ORDER BY ingresos DESC\n"
            "LIMIT 1"
        ),
        columns=("proveedor", "pais", "ingresos"),
        description="Proveedor que genera más ingresos con sus productos.",
    ),
    Intent(
        id="detalle_orden",
        category="Órdenes",
        response_type=TABLE,
        keywords=("orden",),
        regex=r"detalle|rengl|linea|muestra|completo",
        params=(Param("order_id", "order_id"),),
        sql=(
            "SELECT o.OrderID,\n"
            "       cu.CustomerName,\n"
            "       e.FirstName || ' ' || e.LastName AS empleado,\n"
            "       o.OrderDate,\n"
            "       s.ShipperName,\n"
            "       p.ProductName,\n"
            "       od.Quantity,\n"
            "       p.Price,\n"
            "       ROUND(od.Quantity * p.Price, 2) AS subtotal\n"
            "FROM Orders o\n"
            "JOIN Customers cu ON o.CustomerID = cu.CustomerID\n"
            "JOIN Employees e ON o.EmployeeID = e.EmployeeID\n"
            "JOIN Shippers s ON o.ShipperID = s.ShipperID\n"
            "JOIN OrderDetails od ON o.OrderID = od.OrderID\n"
            "JOIN Products p ON od.ProductID = p.ProductID\n"
            "WHERE o.OrderID = ?"
        ),
        columns=(
            "OrderID",
            "CustomerName",
            "empleado",
            "OrderDate",
            "ShipperName",
            "ProductName",
            "Quantity",
            "Price",
            "subtotal",
        ),
        description="Detalle completo de la orden indicada.",
    ),
    # ------------------------------------------------------------------ Usuarios
    Intent(
        id="usuarios_activos",
        category="Usuarios del sistema",
        response_type=SCALAR_COUNT,
        keywords=("usuario", "activo"),
        forbid=("rol", "cada"),
        sql=(
            "SELECT COUNT(*) AS usuarios_activos\n"
            "FROM Users\n"
            "WHERE is_active = 1"
        ),
        columns=("usuarios_activos",),
        description="Número de cuentas activas en el sistema.",
    ),
    Intent(
        id="usuarios_activos_por_rol",
        category="Usuarios del sistema",
        response_type=GROUPED_LIST,
        keywords=("usuario", "rol"),
        regex=r"por rol|cada rol|cuant",
        forbid=("lista", "dame", "muestra"),
        sql=(
            "SELECT role AS rol, COUNT(*) AS usuarios_activos\n"
            "FROM Users\n"
            "WHERE is_active = 1\n"
            "GROUP BY role\n"
            "ORDER BY usuarios_activos DESC"
        ),
        columns=("rol", "usuarios_activos"),
        description="Cuentas activas agrupadas por rol.",
    ),
    Intent(
        id="usuarios_por_rol",
        category="Usuarios del sistema",
        response_type=ROW_LIST,
        keywords=("usuario", "rol"),
        regex=r"lista|dame|muestra|activo",
        params=(Param("role", "role"),),
        sql=(
            "SELECT name, email, created_at\n"
            "FROM Users\n"
            "WHERE role = ? AND is_active = 1\n"
            "ORDER BY created_at DESC"
        ),
        columns=("name", "email", "created_at"),
        description="Cuentas activas del rol indicado.",
    ),
)
