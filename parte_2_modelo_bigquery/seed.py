"""Script CLI para (re)generar desde cero todos los datos sintéticos en BigQuery.

Modelo de datos (dbdiagram, revisión de octubre de 2026):
- categories, customers, products (catálogo sin stock ni coste),
  purchase_orders + purchase_items (compras a proveedores: definen coste y stock),
  orders, order_items, payments (1:1 con orders), reviews (1:1 con order_items vía review_id).

Reglas relacionales que deben ser garantizadas:
- orders: siempre tienen al menos una order_items (1:N, N >= 1), ningún pedido
  es anterior a la fecha de alta (created_at) de su cliente, y si tienen pago su
  estado no puede ser cancelled/returned... (estado y pago consistentes).
- payments: no existe un payment sin order y como máximo 1 por order (1:1);
  el importe del payment es la suma de los valores de las líneas del order.
- purchase_items: (purchase_id, product_id) único; el cost es el precio de
  compra de ese producto en ese purchase_order (el stock queda reflejado en stock).
- reviews: como máximo 1 revisión por línea (order+product) de un pedido
  entregado (delivered o returned); created_at posterior a la entrega.

Uso:
    python seed.py --project mi-proyecto --dataset tu_empresa --customers 500 --orders 2000
    python seed.py --project mi-proyecto --dataset tu_empresa \
        --customers 500 --products 2000 --orders 1000 \
        --order_lines 2600 --purchase_orders 100 --purchase_items 2000 \
        --payments 800 --reviews 1900
    python seed.py --project mi-proyecto --dataset tu_empresa --dry-run

Flujo: todo el generador trabaja solo sobre DataFrames (df = pd.DataFrame(...)).
Los datos no se mandan a BigQuery hasta que todas las tablas están generadas:
primero se imprimen las 10 primeras filas de cada tabla, se pregunta si se sube
y, si se confirma, se carga todo en BigQuery.
"""

import argparse
import os
import random
import sys
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

try:
    from google.cloud import bigquery
    from google.oauth2 import service_account
except ImportError:
    print("[◬]ATENCIÓN[◬]: ✕ Faltan las librerías de BigQuery. Instala: pip install google-cloud-bigquery google-auth")
    sys.exit(1)

try:
    from faker import Faker
except ImportError:
    print("[◬]ATENCIÓN[◬]: ✕ Falta la librería faker. Instala: pip install faker")
    sys.exit(1)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print("[◬]ATENCIÓN[◬]: ✕ Falla la lectura de variables de entorno. Se necesitan para la conexión.")
    sys.exit(1)

carpeta = os.path.dirname(os.getcwd())
print(carpeta)

# Configuración
PROJECT_ID: str | None = os.getenv("GCP_PROJECT_ID")
DATASET_ID: str | None = os.getenv("BQ_DATASET_ID")
CREDS_JSON: str | None = os.getenv("CREDENTIALS_PATH")

if not CREDS_JSON:
    print("[X] Falta la variable de entorno CREDENTIALS_PATH")
    sys.exit(1)
trayectoria = os.path.normpath(os.path.join(os.getcwd(), CREDS_JSON))

CREDENTIALS_PATH = trayectoria

if not os.path.isfile(CREDENTIALS_PATH):
    raise FileNotFoundError(f"Credentials file not found: {CREDENTIALS_PATH}")

credentials = service_account.Credentials.from_service_account_file(trayectoria)
client = bigquery.Client(
    project=PROJECT_ID,
    credentials=credentials
)

# Semilla determinista de Faker (se siembra de verdad en main()).
FAKER_SEED = 42
faker = Faker()

# Rangos de fechas de los datos simulados.
# Invariante: FECHAS_REGISTRO[1] <= FECHAS_ORDEN[1] (siempre cabe al menos 1 día de pedidos).
FECHAS_REGISTRO = (date(2020, 1, 1), date(2026, 1, 1))
FECHAS_ORDEN = (date(2023, 1, 1), date(2026, 10, 12))

# Categorías (las mismas que las claves de PRECIOS). Universo fijo de 10.
CATEGORIAS = [
    "Smartphones",
    "Laptops",
    "Tablets",
    "Monitores",
    "Audio",
    "Teclados",
    "Ratones",
    "Componentes",
    "Almacenamiento",
    "Redes",
]

# Canales de adquisición de clientes (por defecto 'organic').
CANALES_ADQUISICION = ("organic", "paid_ads", "social_media", "email", "referral", "affiliate")

# Proveedores de la cadena de suministro (tabla purchase_orders).
PROVEEDORES = ("Proveedor_A", "Proveedor_B", "Proveedor_C", "Proveedor_D")

# Métodos de pago admitidos (tabla payments).
METODOS_PAGO = ("credit_card", "debit_card", "paypal", "bank_transfer", "apple_pay", "google_pay")

# Estados de pedido (tabla orders) y probabilidad por estado según haya pago o no.
ESTADOS_CON_PAGO = ("confirmed", "shipped", "delivered", "delivered")
ESTADOS_SIN_PAGO = ("pending", "pending", "pending", "cancelled", "returned")

# Columnas de fecha/hora (ISO 8601) que se declaran TIMESTAMP en BigQuery.
ISO_DATETIME = {
    "customers": ("created_at",),
    "orders": ("order_date", "shipped_date", "delivered_date"),
    "payments": ("paid_at",),
    "reviews": ("created_at",),
    "purchase_items": ("stock_date",),
}

# Columnas NUMERIC(10,2) en BigQuery (el resto se sube como STRING salvo INT64/BOOLEAN).
NUMERICAS = {
    "products": ("unit_price",),
    "order_items": ("unit_price",),
    "payments": ("amount",),
    "purchase_items": ("cost",),
}

# Clientes solo de Europa (faker.country() da cualquier país del mundo).
PAISES_EUROPA = [
    "Austria",
    "Belgium",
    "Bulgaria",
    "Croatia",
    "Cyprus",
    "Czech Republic",
    "Denmark",
    "Estonia",
    "Finland",
    "France",
    "Germany",
    "Greece",
    "Hungary",
    "Ireland",
    "Italy",
    "Latvia",
    "Lithuania",
    "Luxembourg",
    "Malta",
    "Netherlands",
    "Poland",
    "Portugal",
    "Romania",
    "Slovakia",
    "Slovenia",
    "Spain",
    "Sweden",
    "Switzerland",
    "United Kingdom",
    "Norway",
    "Iceland",
]

# Diccionario de precios según categoría: (price_min, price_max).
# El precio unitario de los productos sale de aquí; el margen se calcula frente
# al coste registrado en purchase_items.
PRECIOS = {
    "Smartphones": (100, 1300),
    "Laptops": (1000, 3200),
    "Tablets": (100, 1500),
    "Monitores": (60, 800),
    "Audio": (100, 1000),
    "Teclados": (20, 300),
    "Ratones": (20, 250),
    "Componentes": (20, 900),
    "Almacenamiento": (100, 800),
    "Redes": (30, 3000),
    "Accesorios": (20, 120),
    "Wearables": (30, 600),
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Volumen de datos por tabla + destino en BigQuery; los None se rellenan en planificador()."""
    parser = argparse.ArgumentParser(description="Generar y cargar datos ficticios en BigQuery")
    parser.add_argument(
        "--project", type=str, default=PROJECT_ID, help="Proyecto GCP (por defecto la env GCP_PROJECT_ID)"
    )
    parser.add_argument(
        "--dataset", type=str, default=DATASET_ID, help="Dataset de BigQuery (por defecto la env BQ_DATASET_ID)"
    )
    parser.add_argument("--categories", type=int, default=10, help="Número de categorías (máximo 10, por defecto 10)")
    parser.add_argument("--customers", type=int, default=100, help="Número de clientes (por defecto 100)")
    parser.add_argument("--products", type=int, default=100, help="Número de productos (por defecto 100)")
    parser.add_argument("--orders", type=int, default=500, help="Número de pedidos (por defecto 500)")
    parser.add_argument(
        "--order_lines",
        type=int,
        default=None,
        help="Número de líneas de pedido (por defecto orders * 2). Debe ser >= orders",
    )
    parser.add_argument(
        "--purchase_orders",
        type=int,
        default=None,
        help="Número de pedidos de compra (por defecto products // 2)",
    )
    parser.add_argument(
        "--purchase_items",
        type=int,
        default=None,
        help="Número de líneas de compra (por defecto purchase_orders * 2). Debe ser >= purchase_orders",
    )
    parser.add_argument(
        "--payments",
        type=int,
        default=None,
        help="Número de pagos (por defecto 80%% de los orders). Debe ser <= orders",
    )
    parser.add_argument(
        "--reviews",
        type=int,
        default=None,
        help="Número de revisiones (por defecto 40%% de los orders). Tope: líneas de pedidos entregados (1 revisión por línea).",
    )
    parser.add_argument("--location", default="EU", help="Ubicación del dataset en BigQuery (por defecto EU)")
    parser.add_argument("--seed", type=int, default=FAKER_SEED, help="Semilla de generación de datos")
    parser.add_argument("--dry-run", action="store_true", help="Solo genera e imprime los datos sin tocar BigQuery")
    return parser.parse_args(argv)


def planificador(args: argparse.Namespace) -> tuple[dict[str, int], list[str]]:
    """Valores por defecto (lineas/pagos/reviews si no se piden) + errores de coherencia."""
    # si no se piden lineas de pedido, 2 por pedido (garantía 1:1 por (order, product))
    if args.order_lines is None:
        args.order_lines = args.orders * 2
    # si no se pide nº de pedidos de compra, la mitad de productos (mínimo 1)
    if args.purchase_orders is None:
        args.purchase_orders = max(1, args.products // 2)
    # si no se piden lineas de compra, 2 por pedido de compra
    if args.purchase_items is None:
        args.purchase_items = args.purchase_orders * 2
    # si no se piden pagos, 80% de los pedidos (1 payment por order pago)
    if args.payments is None:
        args.payments = int(args.orders * 0.8)
    # si no se piden reviews, 40% de los pedidos (gen_valoracion las recorta a las lineas entregadas)
    if args.reviews is None:
        args.reviews = int(args.orders * 0.4)

    plan = {
        "categories": args.categories,
        "customers": args.customers,
        "products": args.products,
        "orders": args.orders,
        "order_items": args.order_lines,
        "purchase_orders": args.purchase_orders,
        "purchase_items": args.purchase_items,
        "payments": args.payments,
        "reviews": args.reviews,
    }

    # validación: los números deben respetar las reglas relacionales (1:1, 1:N, topes)
    errors: list[str] = []
    for label in ("categories", "customers", "products", "orders", "purchase_orders"):
        if plan[label] < 1:
            errors.append(f"--{label} debe ser al menos 1")
    if plan["categories"] > len(CATEGORIAS):
        errors.append(f"--categories ({plan['categories']}) excede el universo disponible ({len(CATEGORIAS)})")
    if plan["order_items"] < plan["orders"]:
        errors.append(
            f"--order_lines ({plan['order_items']}) debe ser >= --orders ({plan['orders']}): cada pedido necesita al menos 1 línea"
        )
    if plan["order_items"] > plan["orders"] * plan["products"]:
        errors.append(
            f"--order_lines ({plan['order_items']}) excede orders*products ({plan['orders']}x{plan['products']}): un (pedido, producto) solo puede repetirse 1 vez por revisión (rel. 1:1)"
        )
    if plan["purchase_items"] < plan["purchase_orders"]:
        errors.append(
            f"--purchase_items ({plan['purchase_items']}) debe ser >= --purchase_orders ({plan['purchase_orders']}): cada pedido de compra necesita al menos 1 línea"
        )
    if plan["purchase_items"] > plan["purchase_orders"] * plan["products"]:
        errors.append(
            f"--purchase_items ({plan['purchase_items']}) excede purchase_orders*products ({plan['purchase_orders']}x{plan['products']}): un (purchase, producto) solo puede aparecer 1 vez"
        )
    if not 0 <= plan["payments"] <= plan["orders"]:
        errors.append(
            f"--payments ({plan['payments']}) debe estar entre 0 y --orders ({plan['orders']}): como máximo 1 pago por order"
        )
    if not 0 <= plan["reviews"]:
        errors.append(
            f"--reviews ({plan['reviews']}) debe ser >= 0: como máximo 1 revisión por línea entregada (order, product)"
        )
    return plan, errors


def rand_fecha(rnd: random.Random, start: date, end: date) -> date:
    """Fecha aleatoria dentro del rango [start, end] (inclusive)."""
    return start + timedelta(days=rnd.randint(0, (end - start).days))


def rand_fechatime(rnd: random.Random, start: date, end: date) -> datetime:
    """Fecha + hora aleatorias dentro del rango. Se usa en campos TIMESTAMP de BQ."""
    day = start + timedelta(days=rnd.randint(0, (end - start).days))
    return datetime(day.year, day.month, day.day, rnd.randint(0, 23), rnd.randint(0, 59), rnd.randint(0, 59))


def gen_categorias(n_categorias: int) -> pd.DataFrame:
    """Selecciona las primeras categorías del universo fijo CATEGORIAS."""
    salida = [
        {"category_id": i, "name": name, "description": f"Categoría {name}"}
        for i, name in enumerate(CATEGORIAS[:n_categorias], start=1)
    ]
    return pd.DataFrame(salida)


def gen_clientes(n_clientes: int) -> pd.DataFrame:
    """Genera los clientes.

    created_at es la fecha de alta: ningún pedido del cliente puede ser
    anterior a esta fecha (la recupera main a partir de este DataFrame).
    """
    salida = []
    for i in range(1, n_clientes + 1):
        first, last = faker.first_name(), faker.last_name()
        reg = rand_fechatime(random.Random(i), *FECHAS_REGISTRO)
        salida.append(
            {
                "id": i,
                "first_name": first,
                "last_name": last,
                "email": f"{first}.{last}.{i}@{faker.unique.domain_name()}",
                "phone": faker.phone_number(),
                "country": random.choice(PAISES_EUROPA),
                "city": faker.city(),
                "address": faker.address(),
                "acquisition_channel": "organic" if random.random() < 0.5 else random.choice(CANALES_ADQUISICION[1:]),
                "created_at": reg.isoformat(),
            }
        )
    return pd.DataFrame(salida)


def gen_productos(df_categories: pd.DataFrame, n_products: int) -> pd.DataFrame:
    """Genera los productos (catálogo: sin stock ni coste; el precio histórico
    se guarda en order_items.unit_price).

    El unit_price de cada producto se saca del diccionario PRECIOS, que
    contiene los límites de precio por categoría.
    """
    categoria_map = {c["name"]: c["category_id"] for c in df_categories.to_dict("records")}
    salida = []
    for i in range(1, n_products + 1):
        cat = faker.random_element(list(categoria_map))
        pmin, pmax = PRECIOS[cat]
        price = Decimal(str(random.uniform(pmin, pmax))).quantize(Decimal("0.01"))
        salida.append(
            {
                "product_id": i,
                "name": faker.catch_phrase(),
                "description": faker.sentence(),
                "category_id": categoria_map[cat],
                "unit_price": float(price),
                "is_active": random.random() < 0.95,
            }
        )
    return pd.DataFrame(salida)


def gen_compras(df_products: pd.DataFrame, n_po: int, n_items: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Genera los pedidos de compra (purchase_orders) y sus líneas (purchase_items).

    (purchase_id, product_id) es único. El coste de cada línea es el precio de
    compra de ese producto en ese pedido (20%-75% del unit_price del catálogo)
    y stock es la cantidad que queda en almacén en stock_date.
    """
    ids_productos: list[int] = [p["product_id"] for p in df_products.to_dict("records")]
    precios: dict[int, Decimal] = {p["product_id"]: Decimal(str(p["unit_price"])) for p in df_products.to_dict("records")}

    df_purchase_orders = pd.DataFrame(
        [{"order_id": i, "provider": random.choice(PROVEEDORES)} for i in range(1, n_po + 1)]
    )

    usados: set[tuple[int, int]] = set()
    salida: list[dict[str, object]] = []

    def _gen(po: int, pid: int) -> None:
        unidades = random.randint(10, 200)
        coste = (precios[pid] * Decimal(random.randint(20, 75)) / 100).quantize(Decimal("0.01"))
        salida.append(
            {
                "purchase_id": po,
                "order_id": po,
                "product_id": pid,
                "purchased_units": unidades,
                "cost": float(coste),
                "stock": unidades + random.randint(0, 20),
                "stock_date": rand_fechatime(random.Random(po * 31 + pid), *FECHAS_REGISTRO).isoformat(),
            }
        )
        usados.add((po, pid))

    if n_items >= len(ids_productos):
        # cobertura: cada producto aparece en al menos 1 pedido de compra
        for pid in ids_productos:
            _gen(random.randrange(1, n_po + 1), pid)
    else:
        print(
            f"[i] purchase_items: {n_items} < products ({len(ids_productos)}): "
            "algún producto quedará sin historial de compra (coste desconocido)."
        )

    faltan = n_items - len(salida)
    if faltan:
        restantes = [(po, pid) for po in range(1, n_po + 1) for pid in ids_productos if (po, pid) not in usados]
        for po, pid in random.sample(restantes, faltan):
            _gen(po, pid)
    return df_purchase_orders, pd.DataFrame(salida)


def gen_pedidos(reg_dates: dict[int, str], paid_ids: set[int], n_orders: int) -> pd.DataFrame:
    """Genera los pedidos.

    Ningún pedido es anterior a la fecha de alta (created_at) de su cliente:
    reg_dates es {id: created_at} y se recupera del DataFrame de clientes que
    ya se generó. paid_ids son los orders con pago (1 por order); su estado es
    coherente con el pago (confirmed/shipped/delivered) y los sin pago salen de
    pending/cancelled/returned.
    """
    salida: list[dict[str, object]] = []
    # los pedidos salen del rango global FECHAS_ORDEN y siempre después del alta del cliente
    min_d, max_d = FECHAS_ORDEN
    inicio_global = datetime(min_d.year, min_d.month, min_d.day)
    fin = datetime(max_d.year, max_d.month, max_d.day, 23, 59, 59)
    ids_clientes = tuple(reg_dates)
    for i in range(1, n_orders + 1):
        customer_id = random.choice(ids_clientes)
        alta_cliente = datetime.fromisoformat(reg_dates[customer_id])
        inicio = max(inicio_global, alta_cliente)
        order_dt = rand_fechatime(random.Random(i * 7 + customer_id), inicio, fin)
        status = random.choice(ESTADOS_CON_PAGO if i in paid_ids else ESTADOS_SIN_PAGO)
        shipped_dt: datetime | None = None
        delivered_dt: datetime | None = None
        if status in ("shipped", "delivered", "returned"):
            shipped_dt = order_dt + timedelta(days=random.randint(1, 14), hours=random.randint(0, 23))
        if status in ("delivered", "returned"):
            delivered_dt = shipped_dt + timedelta(days=random.randint(1, 21), hours=random.randint(0, 23))  # type: ignore[arg-type]
        salida.append(
            {
                "order_id": i,
                "customer_id": customer_id,
                "status": status,
                "order_date": order_dt.isoformat(),
                "shipped_date": shipped_dt.isoformat() if shipped_dt else None,
                "delivered_date": delivered_dt.isoformat() if delivered_dt else None,
            }
        )
    return pd.DataFrame(salida)


def gen_lineaspedido(df_orders: pd.DataFrame, df_products: pd.DataFrame, n_lines: int) -> pd.DataFrame:
    """Genera las líneas de pedido (1 pedido mínimo, (pedido, producto) único).

    El unit_price es el precio del producto en el momento de la compra: se
    deriva de products.unit_price con un margen de variación de ±15%.
    review_id nace vacío (None) y gen_valoracion lo rellena con el id de la
    revisión si la línea se valora (relación 1:1 por línea).
    """
    salida: list[dict[str, object]] = []
    ids_pedidos = tuple(df_orders["order_id"])
    productos = df_products.to_dict("records")
    ids_productos: list[int] = [p["product_id"] for p in productos]
    precios: dict[int, Decimal] = {p["product_id"]: Decimal(str(p["unit_price"])) for p in productos}
    usados: set[tuple[int, int]] = set()

    def _gen(oid: int, pid: int) -> None:
        qty = random.randint(1, 3)
        precio = (precios[pid] * (Decimal(1) + Decimal(random.uniform(-0.15, 0.15)))).quantize(Decimal("0.01"))
        salida.append(
            {
                "order_id": oid,
                "product_id": pid,
                "quantity": qty,
                "unit_price": float(precio),
                "review_id": None,
            }
        )
        usados.add((oid, pid))

    # 1a línea garantizada por pedido (regla 1:N, N >= 1)
    for oid in ids_pedidos:
        _gen(oid, random.choice(ids_productos))
    # el resto sin repetir (pedido, producto): muestreo sin reintentos
    faltan = n_lines - len(salida)
    if faltan:
        restantes = [(oid, pid) for oid in ids_pedidos for pid in ids_productos if (oid, pid) not in usados]
        for oid, pid in random.sample(restantes, faltan):
            _gen(oid, pid)
    return pd.DataFrame(salida)


def gen_pago(df_orders: pd.DataFrame, df_order_items: pd.DataFrame, paid_ids: set[int]) -> pd.DataFrame:
    """Genera los pagos.

    El importe del pago es la suma de los valores de las líneas del order.
    Como máximo 1 payment por order (los que están en paid_ids).
    """
    total_por_order: dict[int, Decimal] = {}
    for linea in df_order_items.to_dict("records"):
        oid = linea["order_id"]
        total_por_order[oid] = (
            total_por_order.get(oid, Decimal("0.00")) + Decimal(str(linea["unit_price"])) * linea["quantity"]
        )
    fechas = dict(zip(df_orders["order_id"], df_orders["order_date"]))
    salida = []
    payment_id = 0
    for oid in df_orders["order_id"]:
        if oid not in paid_ids:
            continue
        payment_id += 1
        order_dt = datetime.fromisoformat(fechas[oid])
        salida.append(
            {
                "payment_id": payment_id,
                "order_id": oid,
                "method": random.choice(METODOS_PAGO),
                "amount": float(total_por_order[oid]),
                "paid_at": (order_dt + timedelta(days=random.randint(0, 3), hours=random.randint(0, 23))).isoformat(),
            }
        )
    return pd.DataFrame(salida)


def gen_valoracion(df_orders: pd.DataFrame, df_order_items: pd.DataFrame, n_reviews: int) -> pd.DataFrame:
    """Genera las valoraciones sobre líneas de pedido.

    Solo puede valorarse una línea (order, product) de un pedido entregado
    (delivered o returned) y con created_at posterior a la entrega.
    Como máximo 1 revisión por línea (FK 1:1 en order_items.review_id).
    """
    if n_reviews == 0:
        return pd.DataFrame()

    entregados = set(df_orders[df_orders["status"].isin(("delivered", "returned"))]["order_id"])
    lineas_entregadas = [
        (l["order_id"], l["product_id"]) for l in df_order_items.to_dict("records") if l["order_id"] in entregados
    ]
    if not lineas_entregadas:
        return pd.DataFrame()

    entregate = dict(zip(df_orders["order_id"], df_orders["delivered_date"]))
    fechas = dict(zip(df_orders["order_id"], df_orders["order_date"]))
    salida: list[dict[str, object]] = []
    # si se piden mas reviews que lineas entregadas, se recorta a esa cantidad (tope natural)
    objetivo = min(n_reviews, len(lineas_entregadas))
    if objetivo < n_reviews:
        print(
            f"[i] reviews: {n_reviews} solicitadas pero solo hay {len(lineas_entregadas)} lineas entregadas: se generan {objetivo}."
        )
    # 1 revisión por linea: se muestrea sin replacement sobre las lineas entregadas
    for oid, pid in random.sample(lineas_entregadas, objetivo):
        # la valoración siempre es posterior a la entrega (o a la fecha del pedido si no hay entrega)
        base_iso = entregate[oid] or fechas[oid]
        base = datetime.fromisoformat(base_iso)
        review_id = len(salida) + 1
        salida.append(
            {
                "review_id": review_id,
                "comment": faker.sentence() if random.random() < 0.7 else "",
                "rating": random.randint(1, 5),
                "created_at": (base + timedelta(days=random.randint(1, 30))).isoformat(),
            }
        )
        # back-reference 1:1: la revision queda colgada de la linea entregada
        df_order_items.loc[
            (df_order_items["order_id"] == oid) & (df_order_items["product_id"] == pid),
            "review_id",
        ] = review_id
    return pd.DataFrame(salida)


def constructor_cliente(project: str) -> "bigquery.Client":
    """Cliente de BigQuery con cuenta de servicio de proyecto (env o clave fija del repo)."""
    credentials_path = Path(os.getenv("CREDENTIALS_PATH", CREDENTIALS_PATH))
    print(f"[i] Usando credenciales: {credentials_path}")
    credentials = service_account.Credentials.from_service_account_file(credentials_path)  # type: ignore[no-untyped-call]
    return bigquery.Client(project=project, credentials=credentials)


def tipo_bq(column: str, df: pd.DataFrame, tabla: str) -> str:
    """Tipo de BigQuery de cada columna del df (INT64, BOOLEAN, NUMERIC, TIMESTAMP o STRING)."""
    if column in ISO_DATETIME.get(tabla, ()):
        return "TIMESTAMP"
    if column in NUMERICAS.get(tabla, ()):
        return "NUMERIC"
    if column in ("rating", "review_id"):
        return "INT64"
    kind = df[column].dtype.kind
    return {"i": "INT64", "b": "BOOLEAN"}.get(kind, "STRING")


def load_table(client: "bigquery.Client", dataset: str, table_id: str, df: pd.DataFrame, location: str) -> None:
    """(Re)carga la tabla en un único job WRITE_TRUNCATE: atómico, sin la carrera create/insert."""
    if df.empty:
        print(f"[i] {table_id} vacía: no hay nada que cargar.")
        return
    table_ref = client.dataset(dataset).table(table_id)
    config = bigquery.LoadJobConfig()
    config.source_format = bigquery.SourceFormat.NEWLINE_DELIMITED_JSON
    config.write_disposition = bigquery.WriteDisposition.WRITE_TRUNCATE
    config.schema = [bigquery.SchemaField(column, tipo_bq(column, df, table_id)) for column in df.columns]
    # Los NaN de pandas (p. ej. shipped_date/delivered_date en pedidos sin estado
    # de envío) se serializan como el literal "NaN", que no es JSON válido y BigQuery
    # rechaza. Se convierten a None (null) antes de serializar.
    df_clean = df.astype(object).where(pd.notna(df), None)
    job = client.load_table_from_json(df_clean.to_dict(orient="records"), table_ref, job_config=config, location=location)
    job.result()



def cuenta_registros(client: "bigquery.Client", project: str, dataset: str, table_id: str) -> int:
    filas = client.query(f"SELECT COUNT(*) AS total FROM `{project}.{dataset}.{table_id}`").result()
    for fila in filas:
        return int(fila["total"])
    return 0


def confirmar_subida() -> bool:
    """Pregunta por stdin si subir a BigQuery; si stdin está cerrado (pipelines) responde No."""
    try:
        resp = input("\n¿Subir todos los datos a BigQuery? [s/N]: ").strip().lower()
    except EOFError:
        return False
    return resp in {"s", "si", "sí", "y", "yes"}


def _fmt_row(cell_values, widths):
    return "  ".join(str(v).ljust(w) for v, w in zip(cell_values, widths)).rstrip()


def render_table(title, rows, header=None) -> str:
    """Render rows in left-justified fixed-width columns, bookended by '=' lines.

    Title sits between two '=' separators; an optional header row sits above the
    data columns, each column sized to its widest string. width stays 60, matching
    the separators seed.py prints elsewhere.
    """
    lines = ["=" * 60]
    if title:
        lines.append(title)
        lines.append("=" * 60)
    if header:
        widths = [len(str(h)) for h in header]
        for row in rows:
            for i in range(len(header)):
                widths[i] = max(widths[i], len(str(row[i]) if i < len(row) else ""))
        lines.append(_fmt_row(header, widths))
        for row in rows:
            lines.append(_fmt_row(row, widths))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    plan, errors = planificador(args)
    if errors:
        for error in errors:
            print(f"[X] {error}")
        sys.exit(1)

    random.seed(args.seed)
    faker.seed_instance(args.seed)

    # 1) Se generan TODOS los DataFrames en memoria. Las columnas son lógicas:
    # el mapeo al esquema de BigQuery no se hace todavía.
    df_categories = gen_categorias(plan["categories"])
    df_customers = gen_clientes(plan["customers"])
    # La fecha de alta de cada cliente sale de su propio DataFrame: los pedidos
    # no pueden ser anteriores a created_at.
    reg_dates = dict(zip(df_customers["id"], df_customers["created_at"]))
    df_products = gen_productos(df_categories, plan["products"])
    # compras a proveedores (independientes de los pedidos de venta)
    df_purchase_orders, df_purchase_items = gen_compras(
        df_products, plan["purchase_orders"], plan["purchase_items"]
    )
    # qué orders tienen pago: se decide ANTES de generarlos para fijar su estado
    paid_ids = set(random.sample(range(1, plan["orders"] + 1), plan["payments"]))
    df_orders = gen_pedidos(reg_dates, paid_ids, plan["orders"])
    df_order_items = gen_lineaspedido(df_orders, df_products, plan["order_items"])
    df_payments = gen_pago(df_orders, df_order_items, paid_ids)
    df_reviews = gen_valoracion(df_orders, df_order_items, plan["reviews"])

    data = {
        "categories": df_categories,
        "customers": df_customers,
        "products": df_products,
        "purchase_orders": df_purchase_orders,
        "purchase_items": df_purchase_items,
        "orders": df_orders,
        "order_items": df_order_items,
        "payments": df_payments,
        "reviews": df_reviews,
    }
    # 2) Resumen en pantalla: 10 primeras filas de cada tabla (orden padre → hijo).
    for nombre, df in data.items():
        print("\n" + "=" * 60)
        print(f"{nombre}: {len(df)} filas")
        print("=" * 60)
        print(df.head(10).to_string(index=False))

    if args.dry_run:
        print("\n[dry-run] No se ha tocado BigQuery.")
        return
    if not confirmar_subida():
        print("No se ha tocado BigQuery.")
        return

    # 3) Subida a BigQuery (solo tras confirmación, con todos los df ya generados).
    client = constructor_cliente(args.project)
    for nombre, df in data.items():
        load_table(client, args.dataset, nombre, df, args.location)

    print("\n[✓ ÉXITO] Datos cargados en BigQuery:")
    for nombre in data:
        print(f"  {nombre:<13} {cuenta_registros(client, args.project, args.dataset, nombre)} filas")


if __name__ == "__main__":
    main()
