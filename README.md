# HGLEBREDO ONLINE MARKET

# Team Challenge SQL — Parte 1: SQL murder mistery

He solucionado la primera parte del "SQL Murder Mistery", muy divertido y didáctico, por cierto. 

Situado en la carpeta `parte_1_sql_murder_mistery/` . 

# Team Challenge SQL — Parte 2: modelo en BigQuery.

En esta segunda parte puedo decir que tras trastear un poco con los notebooks, preferí enfrentarme directamente en la codificación del archivo `seed.py` presente en la carpeta `parte_2_modelo_bigquery/`. 

## Entorno virtual

Obviamente, para su ejecución se precisa un entorno virtual para cargar los requerimientos de librerías y dependencias que habilitan su ejecución:

```powershell
# 1. Entorno virtual (una vez)
python -m venv .venv
.venv\Scripts\activate          # en mac/linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Variables de entorno (en tu .env local; NUNCA subas el .env a GitHub)
GCP_PROJECT_ID=hglebredo-online
BQ_DATASET_ID=onlinemarket_dataset
GOOGLE_APPLICATION_CREDENTIALS=./credentials/su-clave.json

# 3. Ejecuciones típicas
python seed.py --dry-run                     # solo genera e imprime, no toca BigQuery
python seed.py --customers 500 --orders 2000  # volumen del enunciado
python seed.py --customers 500 --products 70 --orders 2000 \
    --order_lines 4500 --payments 1600 --reviews 800
```
> **ATENCIÓN:** Es preciso señalar que se requieren los secretos precisos en el archivo `.env` y carpeta `credenciales/` , ambos en el raíz del proyecto. Así como se precisa la existencia de un dataset en el proyecto de Big Query (se puede generar previamente con el notebook existente en `parte_2_modelo_bigquery/notebooks/01_setup_bigquery.ipynb`) 

## Generación de datos bajo demanda con `seed.py` 

Habiendo cumplido los requisitos anteriores:

Señalar al script llamado `seed.py` (en `parte_2_modelo_bigquery/seed.py`) como el fabricante de datos que alimentará a la base de datos. Como se esperaba y describe en el enunciado, se permite alimentar esa ejecución en un solo comando con argumentos.

La ejecución comprende las siguientes fases:
1. generación de **todas** las tablas del modelo en memoria (DataFrames de pandas),
2. presentación de vista previa de los datos antes de inserción (10 filas de cada tabla),
3. en consola y a la vista de los datos indicados en el punto anterior, si se sube a Big Query (y/N) - comportamiento 'default' con tecla `intro`: **"No"**).
4. únicamente pulsando la tecla "s" (sí) se responde afirmativamente y carga los datos mostrados en BigQuery.

Así de fácil, cualquiera podría **recrear los datos desde cero** con distintos volúmenes de los mismos, y para que el dataset que se sube nunca incumpla una regla de negocio (FK - foreign keys), se respetan, relaciones 1:1 y 1:N correctas. Además de precios que se pueden entender y fechas que tienen sentido cronológico. Es el "bonus" del enunciado del ejercicio y se le ha puesto el rigor debido: cada control tiene su regla y su validación dentro en tiempo de ejecución.

### Cómo se ejecuta

Estos son los argumentos y qué se espera de cada uno:

| Argumento | Por defecto | Qué debe cumplir |
|---|---|---|
| `--categories` | 10 | Entre 1 y 10 (el _universo_ categorías es fijo, son 10) |
| `--customers` | 100 | Al menos 1 |
| `--products` | 100 | Al menos 1 |
| `--orders` | 500 | Al menos 1 |
| `--order_lines` | `orders × 2` | Entre `orders` y `orders × products` (por qué, más abajo: R8 y R9) |
| `--payments` | `80 %` de orders | Entre 0 y `orders` (nunca más de 1 pago por pedido) |
| `--reviews` | `40 %` de orders | Al menos 0; si sobrepasan las líneas entregadas, se recortan (R10) |
| `--location` | `EU` | Ubicación del dataset |
| `--seed` | 42 | No es una regla: es tu semilla. Misma semilla → mismos datos (R12) |
| `--dry-run` | no | Genera e imprime, pero nunca toca BigQuery |

Si algún volumen no tiene sentido (pedir 1 000 pagos y solo 500 pedidos, como ejemplo), la ejecución **da un aviso por consola** y sale sin generar nada. Antes de producir cualquier fila, el llamado `planificador` valida todo el plan de generación y garantiza mínimos, sin llegar al disparate... **;)**

### "The execution journey", paso a paso explicada

Así se mueve `main()` por dentro:

1. **Leer argumentos** (`parse_args`). Lo que no especifiques se llena con un _default digno_.
2. **Planificar** (`planificador`). Si omites `--order_lines`, `--payments` o `--reviews`, calcula los valores derivados (_2 líneas por pedido, 80 % de pedidos pagados, aprox. 40 % de reviews_) y valida el plan completo. Aquí es donde se evita un volu­men absurdo.
3. **Semilla**. `random.seed(...)` y `faker.seed_instance(...)`: a partir de aquí todo es reproducible con la semilla correcta (por defecto la 42).
4. **Generar en memoria, en este orden exacto**, porque cada tabla depende de la anterior y ninguna inventa IDs, sino que se relacionan con el fin de dar consistencia a los datos generados:
   1. `gen_categorias` → `categories` (lista fija de 10 categorías)
   2. `gen_clientes` → `customers` (la fecha de alta de cada cliente se recuerda en `reg_fechas`)
   3. `gen_productos` → `products` (cada producto cae en una categoría ya generada y su precio viene de esa categoría)
   4. El lote `paid_ids`: qué pedidos van a tener pago. Se decide **antes** de crear los pedidos porque el pedido necesita saber si está pagado para poder fijar su `state` (sin pago no puede haber estado `delivered`; ver R7)
   5. `gen_pedidos` → `orders` (fecha de pedido nunca antes que el alta del cliente)
   6. `gen_lineaspedido` → `order_items` (solo sobre pedidos y productos que ya existen)
   7. `gen_pago` → `payments` (el importe sale de sumar las líneas reales de ese pedido)
   8. `gen_valoracion` → `reviews` (solo sobre líneas de pedidos entregados)
5. **Vista previa**. Imprime las 10 primeras filas de cada tabla para que veas lo que vas a subir.
6. **Pausa** (`--dry-run` o `confirmar_subida`). Si no confirmas, aquí se acaba el mundo: nada se ha tocado. En un pipeline, donde no hay teclado (standard input cerrada), la respuesta es **"No"** por defecto: el script es seguro con posibles equivocaciones al pulsar `intro` por omisión.
7. **Carga** (`load_table`). Cada tabla sube en **un único job atómico** `WRITE_TRUNCATE`: borra, crea y llena a la vez por lo que re-ejecutar no duplica nada.
8. **Comprobación final** (`cuenta_registros`). Un `SELECT COUNT(*)` por tabla para que veas cuántas filas han quedado en BQ.

### Los controles, uno a uno

Un dataset ficticio tiene que comportarse como uno real, porque de no ser así, las querys analíticas de la parte siguiente dan respuestas a medias. Estas son las reglas que `seed.py` cuida en su código, con la casuística de cada una en concreto.

### Los volúmenes: el plan no genera sinsentidos

**Qué controla.** Que las cantidades pedidas por la línea de comandos tengan sentido juntas, no solas.

**Cómo.** `planificador` rellena los derivados (`order_lines`, `payments`, `reviews`) y valida el plan completo antes de generar nada. Si algo falla, imprime el argumento concreto y sale con código 1.

**Casuística cubre­da.**
- `--categories` fuera de 1-10: el universo de categorías es fijo; más no existe.
- `--customers`, `--products` o `--orders` en 0: no se puede poblar con tablas vacías.
- `--order_lines < orders` (p. ej. 3 pedidos y 2 líneas): algún pedido se quedaría **sin** líneas y la relación 1:N se rompería.
- `--order_lines > orders × products`: no existen tantas (pedido, producto) distintas — cada par solo puede repetirse una vez.
- `--payments > orders`: imposible porque relación 1:1; un pedido no admite 2 pagos.
- `--reviews` negativo.

### Datos con cara real

**Precios que se pueden entender.** Cada producto toma su precio del intervalo `(min, max)` de su categoría, de la tabla `PRECIOS`. Un smartphone vale entre 100 € y 1 300 €, un ratón entre 20 € y 250 €: no aparece un ratón a 3 000 € ni un smartphone a 20 €. Y el coste sale calculado como un 20-75 % del precio, así `cost < price` siempre y el margen es siempre positivo (el enunciado pide calcular márgenes y rentabilidad).
- **Casuística:** productos en su rango de precio + margen positivo. Si el precio fuera inventado al azar, la query de "ingresos por categoría" no diría nada.

**Clientes europeos de verdad.** `country` sale de la lista `PAISES_EUROPA` (si dependiera de "faker", insertarìamos "Tanzanía" o "Chile", perdiendo un poco de crédito nuestra generación); el email es único (faker `unique` + numero de cliente); el canal de adquisición es "organic" el 50 % de las veces y el resto se reparte entre canales de pago, socios, etc...
- **Casuística:** datos del segmento correcto para el análisis de país y canal que el enunciado pide.

**Fechas de alta de verdad.** Una fecha en BigQuery `TIMESTAMP` debe llevar **fecha + hora**; solo fecha (`2021-07-04`) lo rechaza la carga. Por eso la fecha de alta se genera desde el inicio con `rand_fechahora` (la misma función que las demás fechas de tiempo), no con "solo una fecha".
- **Casuística:** es el bug que se ha padecido y resultaba desconcertante: una carga que fallaba en `customers` porque el TIMESTAMP solo traía a fecha. Esta regla evita su ocurrencia.

### El orden temporal: que el tiempo fluya en una sola dirección 

- **Registro - compra.** Un pedido solo puede pasar **después** de que el cliente se dió de alta. Por eso el rango de fecha de cada pedido empieza en el mayor entre el inicio global y la fecha de alta de su cliente concreto. Un cliente de alta en el año 2025, no pudo pedir nada en 2023.
- **Estado coherente con el pago.** Un pedido pagado no puede estar `delivered` o `shipped` si el pago está `pending`. La conexión viene de arriba hacia abajo: `paid_ids` se elige **antes** de crear los pedidos, así el estado sale correcto en vez de reescribirse a posteriori.
- **Compra → valoración.** Las reviews se generan 1-30 días **después** de la fecha del pedido: no se puede valorar un producto que aún no has recibido.

### Relaciones que nunca se rompen

Voy a tratar de describir las seguridades implementadas y las casuísticas cubiertas:

**Cada pedido tiene al menos una línea.** Antes de repartir el resto, cada pedido recibe **una línea garantizada**. Sin esto un pedido vacío rompería la FK 1:N y su importe de pago saldría 0, curiosamente.
- **Casuística:** `orders` con 0 líneas, habríamos roto la lógica a proteger de las "foreign key" del modelo relacional.

**Control de la generación de líneas mediante los datos existentes.** Las líneas solo se crean combinando IDs de tablas ya generadas (nunca un producto que no existe) y se muestrea **sin repetir**: un mismo producto no aparece dos veces en el mismo pedido (la FK compuesta `(order_id, product_id)` es 1:1). Además, `unit_price` se captura del precio del producto **en el momento de generar la línea**; este es el "precio en el momento de la compra" del enunciado, y permite que el precio histórico difiera del precio actual.
- **Casuística:** producto fantasma / línea duplicada / precio imposible de diferir del actual.

**Máximo 1 revisión por línea entregada.** Las valoraciones solo caen sobre líneas de pedidos `delivered` (un pedido no entregado no se puede valorar) y se muestrea sobre las líneas existentes sin repetir. Y si se piden más reviews líneas hay entregadas, se recortan a ese tope antes de la generación y se avisa por consola al usuario (no miente: no fabrica reviews de la nada).
- **Casuística:** review de un pedido `pending` / dos reviews sobre la misma línea / más reviews que líneas posibles.

**Máximo 1 pago por pedido y su importe debe cuadrar.** Cada pedido de `paid_ids` genera exactamente un pago (1:1, nunca 1:N) y el importe debe ser la suma de `unit_price × quantity` de las líneas de ese pedido: nunca un número inventado. `paid_at` trae la fecha del pedido, precisamente para que pueda ser expedido.
- **Casuística:** dos pagos del mismo pedido / un pago cuyo importe no cuadra con sus líneas.

### Reproducible y seguro de cargar

**Misma semilla, mismos datos.** `--seed` arranca `random` y `Faker` en un punto fijo: la ejecución se puede repetir con la misma salida, requisito del bonus.
- **Casuística:** el usuario correr el script en idénticas condiciones y obtiene exactamente los mismos datos.

**Carga atómica en BigQuery.** Cada tabla sube con **un solo job** mediante `load_table_from_json` en modo `WRITE_TRUNCATE`: borra, crea y llena en una operación atómica. Esto sirve a dos fines: re-ejecutar el script no duplica filas, y evita la carrera create-then-insert que en BigQuery da 404 en una tabla recién hecha (consistencia eventual). El esquema BQ se deriva del DataFrame: `TIMESTAMP` solo en las columnas de fecha/hora, `INT64` para enteros (`rating`), el resto `STRING`.
- **Casuística:** una recarga duplicando filas / carga que falla porque la tabla no "existía" todavía cuando intentaba insertar.

**Seguridad por omisión.** Sin `--dry-run` y sin confirmación explícita, nada alude al dataset de Big Query; en un pipeline (donde no hay teclado) la respuesta por defecto es No. La pregunta de confirmación muestra antes las 10 primeras filas de cada tabla como prueba del algodón de lo que se va a subir.
- **Casuística:** nadie (ni un script automático) sobreescribe BigQuery sin querer.

### Resumen: qué hace cada bloque y qué prueba

| Función | Entrada → Salida | Reglas que cubre |
|---|---|---|
| `parse_args` | argumentos → `args` | (opc.) Proyecto, dataset destino, por defecto `.env`, además de volúmenes que van al planificador |
| `planificador` | plan → planificación y errores | Datos y volúmenes coherentes (también sus relaciones) |
| `rand_fecha` / `rand_fechahora` | rango → `fecha` / `fechahora` | Valores de tiempo coherentes y válidos para BQ) |
| `gen_categorias` | n → DataFrame | Universo finito de 10 categorías (FK de productos) |
| `gen_clientes` | n → DataFrame | Europa online, fecha de alta completa |
| `gen_productos` | Categorías, n → DataFrame | Rangos de precios por cat y margen (costos) |
| `gen_pedidos` | Alas de alta, `paid_ids`, n → DataFrame | fecha pedido ≥ alta usuario, además del estado coherente con el pago |
| `gen_lineaspedido` | Pedidos, productos, n → DataFrame | Cobertura relacional, pareja única, precio del momento (variable por eso)|
| `gen_pago` | Pedidos, líneas, `paid_ids` → DataFrame | 1 pago/pedido, totalizando importes de cada línea |
| `gen_valoracion` | Pedidos, líneas, n → DataFrame | Solo entregados y 1 a 1 línea de pedido garantizando fecha posterior |
| `constructor_cliente` | proyecto → `bigquery.Client` | Conexión por cuenta de servicio (`.env` y `credentials/`) |
| `tipo_bq` | Columna, DataFrame → tipo BigQuery | Se mapean los tipos de dato |
| `load_table` | DataFrame → tabla BQ | job atómico `WRITE_TRUNCATE` sin colisiones. Ya garantizamos consistencia en todo lo anteior |
| `cuenta_registros` | Tabla → nº de filas | R14 (confirmación de carga) |
| `confirmar_subida` | stdin → sí/no | Confirmación explícita de borrado/carga (s/N), sin extrañas equivocaciones. Solo "`s` + `intro`" es sí. **Default: NO**) |

## Información IMPORTANTE
> **[ATENCIÓN:] De igual manera** la ejecución del **Notebook directamente borra antiguos datos** e inserta los nuevos, también **CON CONFIRMACIÓN**, aunque el set de volúmen de datos es el acotado por el enunciado:
> ```text
[✓ ÉXITO] Datos cargados en BigQuery:
  categories    10 filas
  customers     500 filas
  products      70 filas
  orders        2000 filas
  order_items   4500 filas
  payments      1600 filas
  reviews       700 filas
  ```     

> Para que sea tenido en cuenta ante una eventual comprobación del propio flujo del notebook.    