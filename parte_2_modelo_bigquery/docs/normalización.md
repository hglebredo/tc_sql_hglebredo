# Modelo de datos

## Elección de entidades - modelo relacional

### Diseño

Hay varios aspectos que se cuídan especialmente como es la tabla intermedia (atomicidad) entre productos y pedidos (`order_items`) que registra respecto de un pedido y un maestro de artículos, la relación entre `products.product_id` y `order.order_id` que registra de un lado un `unit_price` o precio unitario en el momento de compra. Es el que en ese momento está reflejado en la entidad `products`, dado que es un variable según tendencias de mercado. La tabla `order_items` actúa por tanto como histórico de precios.

Hay otro aspecto que difiere del diseño propuesto inicialmente y son las entidades `purchase_items` y `purchase_orders` que pretenden llevar un registro de las entradas de stock (se podrían registrar aquí lotes en tabla vinculada independiente), así como el propio stock fuera de lo que es el producto y sus características. Vi interesante hacerlo precisamente por este motivo, de esta manera una salida de un pedido hacia cliente debería dejarnos trazar hacia atrás el origen del artículo hasta proveedor, luego hasta `purchase_orders.provider` y la orden de pedido de compras que se generó, además de la fecha de entrada del stock. No se han ampliado demasiado estas entidades para no complicar el diseño, aunque sugiero que debería haber también un maestro de proveedores en esta parte.

`Country` está directamente en la entidad `customers` dado te no tiene actualmente en este esquema atributos propios, representando aquí un valor individual aislado. Aunque ciertamente podría tenerlos, lo que requeriría una tabla independiente. Pero añadir a `orders` el nombre actual del cliente junto con `customer_id`, como sugiere el enunciado, vulneraría la tercera forma normal, porque introduciría la *dependencia transitiva*.

También señalar que actualmente se descuentan las líneas de pedidos para cliente directamente de los stocks de `purchase_items`, considerando en este punto que debería llevarse aquí el stock en una tabla independiente de movimientos, donde habría un movimiento de entrada de stock en función de una orden de compra que se cumpliese al recibir el pedido de proveedor, y de ahí se registrarían los subsiguientes movimientos de salida, mermas, posibles deterioros, etc... Dando a la empresa la capacidad de reconstruir un stock a una fecha concreta y poder estudiar con detalle todo movimientos de stock que se genere en pro tener un mayor control de su core de negocio.

Indicar además que hay relaciones unívocas entre tablas:  

* `payments - orders`: relación 1 a 1 donde un pedido es cubierto por una única orden de pago.
* `order_items - reviews`: relación 1 a 0..1, representando la valoración opcional del cliente, pero una línea de pedido (luego artículo/producto) es a una única review en todo caso, evitando que ocurra la valoración de una línea de pedido doblemente, garantizando por tanto esta cardinalidad. Se debe controlar además que esto se permita cuando el pedido tenga el estado `delivered`, que será cuando el cliente podrá opinar fielmente del artículo recibido.


## Normalización hasta Tercera Forma Normal

### 1NF - Primera Forma Normal 

Se garantiza que cada atributo contiene un valor de su dominio, no existen grupos repetidos de columnas y las filas en las entidades pueden identificarse mediante una clave. Se utilizan columnas individuales para nombres, fechas, cantidades, identificadores, estados, etc... evitando por ello mezclar datos de distinta naturaleza. 

El modelo cumple por tanto la primera forma normal porque cada columna almacena un valor individual de su dominio y no existen atributos destinados a contener listas de entidades ni grupos repetidos de columnas. Las relaciones multivaluadas se representan mediante tablas de detalle: `order_items` contiene una fila por producto incluido en un pedido y `purchase_items` contiene las líneas de las compras a proveedores. Con esta justificación presupongo que campos como phone y address no almacenan colecciones de valores independientes.

### 2NF - Segunda Forma Normal

El modelo cumple la segunda forma normal porque cumple 1NF y los atributos no primos dependen de las claves candidatas completas. En `order_items`, cuya clave primaria es (`order_id`, `product_id`), `quantity`, `unit_price` y `review_id` son propiedades de la línea concreta del pedido. Los atributos determinados únicamente por `product_id` se almacenan en `products`, y los determinados únicamente por `order_id` se almacenan en orders. En las demás tablas se ha verificado la no existencia de dependencias parciales respecto a otras posibles claves candidatas compuestas.

### 3NF - Tercera Forma Normal

Quisiera documentar las siguientes reglas antes de concluir acerca del cumplimiento de 3NF:

* order_items tiene clave (order_id, product_id) y admite una sola línea por producto en cada pedido.

* order_items.unit_price es el precio aplicado a esa venta.

* products.unit_price es el precio actual del catálogo.

* Los datos de customers representan el estado actual del cliente.

* country y city no introducen dependencias geográficas adicionales dentro de customers.

* purchase_items.cost tiene un significado preciso: coste unitario, importe de línea u otro.

* purchase_items.stock se define como stock de lote, stock histórico u otra medida; no se da por válido si es el stock global del producto.

* Las cardinalidades y las claves alternativas se garantizan con restricciones reales en el esquema.

Por todo lo anterior, el modelo se ha diseñado para cumplir la tercera forma normal, bajo las reglas de negocio documentadas. Por ello **cumple 3NF** porque las entidades relacionadas se referencian mediante identificadores **sin copiar sus atributos actuales**: los datos del cliente se almacenan en `customers`, los del producto en `products`, los de la categoría en `categories` y los de la reseña en `reviews`. El precio de la línea de pedido representa un hecho histórico de la venta, distinto del precio vigente.