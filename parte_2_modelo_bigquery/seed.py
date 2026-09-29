"""Script CLI para (re)generar desde cero todos los datos sintéticos en BigQuery.

Reglas relacionales que deben ser garantizadas:
- reviews: como máximo 1 revisión por línea (par order+product) de un pedido
  entregado (delivered); created_at de la valoración NO anterior a la fecha del
  pedido (no puedes valorar un producto que aún no has recibido).
- orders: siempre tienen al menos una order_line (1:N, N >= 1) y ningún pedido
  puede ser anterior a la fecha de alta (created_at) de su cliente.
- payments: no existe un payment sin order y como máximo 1 por order (1:1);
  el importe del payment es la suma de los valores de las líneas del order.
- order_items: siempre referencian un product existente (1:N).
- prices: el precio de los productos se saca del diccionario PRECIOS
  (mínimo/máximo por categoría).

Uso:
    python seed.py --project mi-proyecto --dataset tu_empresa --customers 500 --orders 2000
    python seed.py --project mi-proyecto --dataset tu_empresa \
        --customers 500 --products 2000 --orders 1000 \
        --order_lines 2600 --payments 800 --reviews 1900
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
    print("[◬]Atención[◬]: ✕ Faltan las librerías de BigQuery. Instala: pip install google-cloud-bigquery google-auth")
    sys.exit(1)

try:
    from faker import Faker
except ImportError:
    print("[◬]Atención[◬]: ✕ Falta la librería faker. Instala: pip install faker")
    sys.exit(1)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print("[◬]Atención[◬]: ✕ Falla la lectura de variables de entorno. Se necesitan para la conexión.")
    sys.exit(1)


# Configuración
PROJECT_ID: str | None = os.getenv("GCP_PROJECT_ID")
DATASET_ID: str | None = os.getenv("BQ_DATASET_ID")
CREDENTIALS_PATH: Path = Path(__file__).resolve().parent.parent / "credentials" / "hglebredo-online-92ccd6898cd9.json"

# Semilla determinista de Faker (se siembra de verdad en main()).
FAKER_SEED = 42
faker = Faker()

# Rangos de fechas de los datos simulados.
# Invariante: FECHAS_REGISTRO[1] <= FECHAS_ORDEN[1] (siempre cabe al menos 1 día de pedidos).
FECHAS_REGISTRO = (date(2020, 1, 1), date(2026, 1, 1))
FECHAS_ORDEN = (date(2023, 1, 1), date(2026, 6, 30))

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

# Columnas de fecha/hora (ISO 8601) que se declaran TIMESTAMP en BigQuery;
# el resto se sube como STRING (salvo enteros, INT64).
ISO_DATETIME = {
    "customers": ("created_at",),
    "orders": ("order_date",),
    "payments": ("paid_at",),
    "reviews": ("created_at",),
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

# Diccionario de precios según categoría: (price_min, price_max)
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
        "--payments",
        type=int,
        default=None,
        help="Número de pagos (por defecto 80%% de los orders). Debe ser <= orders",
    )
    parser.add_argument(
        "--reviews",
        type=int,
        default=None,
        help="Número de revisiones (por defecto 40%% de los orders). Tope: líneas de pedido entregadas (1 revisión por línea).",
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
        "payments": args.payments,
        "reviews": args.reviews,
    }

    # validación: los números deben respetar las reglas relacionales (1:1, 1:N, topes)
    errors: list[str] = []
    for label in ("categories", "customers", "products", "orders"):
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
                "customer_id": i,
                "first_name": first,
                "last_name": last,
                "email": f"{first}.{last}.{i}@{faker.unique.domain_name()}",
                "phone": faker.phone_number(),
                "country": random.choice(PAISES_EUROPA),
                "city": faker.city(),
                "acquisition_channel": "organic" if random.random() < 0.5 else random.choice(CANALES_ADQUISICION[1:]),
                "created_at": reg.isoformat(),
            }
        )
    return pd.DataFrame(salida)


def gen_productos(df_categories: pd.DataFrame, n_products: int) -> pd.DataFrame:
    """Genera los productos.

    El precio de los productos se saca del diccionario PRECIOS, que contiene
    los límites de precio por cada una de las categorías.
    """
    categoria_map = {c["name"]: c["category_id"] for c in df_categories.to_dict("records")}
    salida = []
    for i in range(1, n_products + 1):
        cat = faker.random_element(list(categoria_map))
        pmin, pmax = PRECIOS[cat]
        price = Decimal(str(random.uniform(pmin, pmax))).quantize(Decimal("0.01"))
        cost = (price * Decimal(random.randint(20, 75)) / 100).quantize(Decimal("0.01"))
        salida.append(
            {
                "product_id": i,
                "name": faker.catch_phrase(),
                "description": faker.sentence(),
                "category_id": categoria_map[cat],
                "price": str(price),
                "cost": str(cost),
                "stock": random.randint(0, 1000),
            }
        )
    return pd.DataFrame(salida)


def gen_pedidos(reg_dates: dict[int, str], paid_ids: set[int], n_orders: int) -> pd.DataFrame:
    """Genera los pedidos.

    Ningún pedido es anterior a la fecha de alta (created_at) de su cliente:
    reg_dates es {customer_id: created_at} y se recupera del DataFrame de
    clientes que ya se generó. paid_ids son los orders con pago (1 por order).
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
        order_date = rand_fechatime(random.Random(i * 7 + customer_id), inicio, fin)
        state = random.choice(["delivered", "shipped"]) if i in paid_ids else "pending"
        salida.append(
            {
                "order_id": i,
                "customer_id": customer_id,
                "state": state,
                "order_date": order_date.isoformat(),
            }
        )
    return pd.DataFrame(salida)


def gen_lineaspedido(df_orders: pd.DataFrame, df_products: pd.DataFrame, n_lines: int) -> pd.DataFrame:
    """Genera las líneas de pedido (1 pedido mínimo, (pedido, producto) único)."""
    salida: list[dict[str, object]] = []
    ids_pedidos = tuple(df_orders["order_id"])
    productos = df_products.to_dict("records")
    ids_productos: list[int] = [p["product_id"] for p in productos]
    precios: dict[int, str] = {p["product_id"]: p["price"] for p in productos}
    usados: set[tuple[int, int]] = set()

    def _gen(oid: int, pid: int) -> None:
        qty = random.randint(1, 3)
        salida.append(
            {
                "order_id": oid,
                "product_id": pid,
                "quantity": qty,
                "unit_price": precios[pid],
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
            total_por_order.get(oid, Decimal("0.00")) + Decimal(linea["unit_price"]) * linea["quantity"]
        )
    fechas = dict(zip(df_orders["order_id"], df_orders["order_date"]))
    salida = []
    payment_id = 0
    for oid in df_orders["order_id"]:
        if oid not in paid_ids:
            continue
        payment_id += 1
        salida.append(
            {
                "payment_id": payment_id,
                "order_id": oid,
                "method": random.choice(["card", "paypal", "bizum"]),
                "amount": str(total_por_order[oid]),
                "paid_at": fechas[oid],
            }
        )
    return pd.DataFrame(salida)


def gen_valoracion(df_orders: pd.DataFrame, df_order_items: pd.DataFrame, n_reviews: int) -> pd.DataFrame:
    """Genera las valoraciones de los productos.

    Solo puede valorarse una línea (order, product) de un pedido entregado
    (delivered) y con created_at posterior o igual a la fecha de ese pedido.
    Como máximo 1 revisión por línea (FK compuesta (order_id, product_id)).
    """
    if n_reviews == 0:
        return pd.DataFrame()

    entregados = set(df_orders[df_orders["state"] == "delivered"]["order_id"])
    lineas_entregadas = [
        (l["order_id"], l["product_id"]) for l in df_order_items.to_dict("records") if l["order_id"] in entregados
    ]
    if not lineas_entregadas:
        return pd.DataFrame()

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
        order_date = datetime.fromisoformat(fechas[oid])
        salida.append(
            {
                "review_id": len(salida) + 1,
                "order_id": oid,
                "product_id": pid,
                "comment": faker.sentence() if random.random() < 0.7 else "",
                "rating": random.randint(1, 5),
                "created_at": (order_date + timedelta(days=random.randint(1, 30))).isoformat(),
            }
        )
    return pd.DataFrame(salida)


def constructor_cliente(project: str) -> "bigquery.Client":
    """Cliente de BigQuery con cuenta de servicio de proyecto (env o clave fija del repo)."""
    credentials_path = Path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", CREDENTIALS_PATH))
    print(f"[i] Usando credenciales: {credentials_path}")
    credentials = service_account.Credentials.from_service_account_file(credentials_path)  # type: ignore[no-untyped-call]
    return bigquery.Client(project=project, credentials=credentials)


def tipo_bq(column: str, df: pd.DataFrame, tabla: str) -> str:
    """Tipo de BigQuery de cada columna del df (INT64, BOOLEAN o TIMESTAMP; resto STRING)."""
    if column in ISO_DATETIME.get(tabla, ()):
        return "TIMESTAMP"
    if column == "rating":
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
    job = client.load_table_from_json(df.to_dict(orient="records"), table_ref, job_config=config, location=location)
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
    reg_dates = dict(zip(df_customers["customer_id"], df_customers["created_at"]))
    df_products = gen_productos(df_categories, plan["products"])
    # qué orders tienen pago: se decide ANTES de generarlos para fijar su state (pending vs delivered/shipped)
    paid_ids = set(random.sample(range(1, plan["orders"] + 1), plan["payments"]))
    df_orders = gen_pedidos(reg_dates, paid_ids, plan["orders"])
    df_order_items = gen_lineaspedido(df_orders, df_products, plan["order_items"])
    df_payments = gen_pago(df_orders, df_order_items, paid_ids)
    df_reviews = gen_valoracion(df_orders, df_order_items, plan["reviews"])

    data = {
        "categories": df_categories,
        "customers": df_customers,
        "products": df_products,
        "orders": df_orders,
        "order_items": df_order_items,
        "payments": df_payments,
        "reviews": df_reviews,
    }
    # 2) Resumen en pantalla: 10 primeras posiciones de cada tabla.
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
