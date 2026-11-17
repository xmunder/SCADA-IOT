# SCADA-IOT: telemetría industrial hacia AWS

Prototipo de prueba de concepto para transportar telemetría de tags OPC UA hacia
AWS IoT Core y Amazon DynamoDB, con almacenamiento y reenvío en el borde y
pruebas de carga reproducibles.

## Documentación

- [Arquitectura](docs/architecture.md): resumen y vistas de contexto, despliegue, ISA-95/IEC 62443, escenarios de
  calidad, riesgos e interfaces en [docs/architecture/](docs/architecture/).
- [Decisiones de arquitectura (ADR)](docs/adr/): almacenamiento y reenvío, lotes, Lambda y DynamoDB, método de
  medición y región de despliegue.
- [Resultados de las pruebas](loadtest/results/README.md): qué contiene cada corrida y qué validez tiene.

## Componentes

| Carpeta | Qué contiene |
|---|---|
| `simulator/` | Servidor OPC UA simulado con N tags (solo laboratorio). |
| `gateway/` | Lector OPC UA por suscripción, lotes, buffer SQLite y publicador MQTT con TLS. |
| `infra/` | Aprovisionamiento de dispositivo (boto3), Lambda de ingesta y plantilla SAM. |
| `loadtest/` | Generador de carga MQTT, medición de PUBACK y verificación de pérdida en DynamoDB. |
| `tests/` | Pruebas unitarias (buffer, payload, configuración, ingesta, estadísticas). |

## Requisitos

- [uv](https://docs.astral.sh/uv/) como gestor de paquetes y entorno. uv instala Python 3.12 si no lo tiene (ver `.python-version`).
- AWS CLI configurado con una cuenta de prueba y permisos de IoT, DynamoDB y CloudFormation.
- AWS SAM CLI para desplegar la infraestructura.

## Instalación con uv (Windows PowerShell)

Si uv no está instalado:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Después, desde la carpeta del proyecto:

```powershell
cd C:\Users\MSI\Documents\dev\AYGO\SCADA-IOT
uv sync              # crea .venv con las versiones exactas de uv.lock
uv run pytest        # ejecuta las pruebas
```

Todos los comandos de Python se ejecutan con `uv run`, por ejemplo `uv run python -m gateway.main`.
No hace falta activar el entorno manualmente.

## Flujo de ejecución

1. **Simulador** (una terminal):

   ```powershell
   uv run python -m simulator.plant_simulator --tags 200 --period 1.0
   ```

2. **Infraestructura** (una vez por cuenta). El script verifica la identidad de AWS, compila con SAM, despliega y muestra las salidas:

   ```powershell
   powershell -ExecutionPolicy Bypass -File infra\deploy.ps1 -Region us-east-2 -Profile personal
   ```

   Anote el nombre de la tabla que muestra la salida (por defecto `scada_telemetry`).

   La región por defecto es `us-east-2`: la cuenta personal usada en las pruebas bloquea las demás
   regiones con una política de organización. Si usa otra región,
   exporte también `AWS_REGION` antes de `provision_device.py` y los scripts de `loadtest`.

   Opciones: `-Profile <perfil>` usa un perfil del CLI distinto del predeterminado, y
   `-ExecutionRoleName <rol>` reutiliza un rol IAM existente (útil en cuentas que no permiten crear
   roles). Sin esa opción, SAM crea un rol de mínimo privilegio con escritura solo en la tabla.
   Al terminar las pruebas, `sam delete --stack-name scada-iot-ingest` elimina la pila y deja el
   costo en cero (la tabla y los datos se borran).

3. **Aprovisionar el gateway** (una vez por sitio). El nombre del sitio es el nombre del Thing y el identificador de cliente MQTT:

   ```powershell
   uv run python infra/provision_device.py --site PLANTA-01 --out certs
   ```

   El script imprime el `IOT_ENDPOINT` que debe configurar.

4. **Configurar variables de entorno** (copie `.env.example` como referencia):

   ```powershell
   $env:SITE_ID = "PLANTA-01"
   $env:OPC_ENDPOINT = "opc.tcp://localhost:4840/scada-iot/simulador"
   $env:OPC_NAMESPACE_URI = "urn:scada-iot:planta"
   $env:IOT_ENDPOINT = "<endpoint impreso por provision_device.py>"
   $env:CERT_FILE = "certs\PLANTA-01-certificate.pem.crt"
   $env:KEY_FILE = "certs\PLANTA-01-private.pem.key"
   $env:CA_FILE = "certs\AmazonRootCA1.pem"
   ```

5. **Gateway** (otra terminal):

   ```powershell
   uv run python -m gateway.main
   ```

6. **Prueba de carga** (ejemplo: 1,000 lecturas por segundo durante 60 s):

   ```powershell
   uv run python -m loadtest.mqtt_load --rate 1000 --readings-per-msg 100 --duration 60 --run-name carga-1000
   uv run python -m loadtest.verify_counts --summary loadtest\results\carga-1000\summary.json --table scada_telemetry
   ```

## Mediciones

Todos los comandos usan las variables de `.env` (`uv run --env-file .env ...`).

| Qué se mide | Comando | Resultado |
|---|---|---|
| Reloj local frente a AWS | `python -m loadtest.clock_check` | Desfase estimado por SNTP; debe ser menor a 50 ms. |
| Pérdida y latencia de ingesta (p50/p95/p99) de una corrida | `python -m loadtest.latency_report --summary <summary.json> --table scada_telemetry` | JSON con pérdida por tag y percentiles. |
| Matriz de escalabilidad (niveles x repeticiones) | `python -m loadtest.run_matrix --levels 200,1000,5000 --duration 60 --repetitions 3 --table scada_telemetry --name matriz-1` | `runs.csv` por corrida y `summary.csv` con media y desviación estándar por nivel. |
| Latencia y continuidad con el gateway real (incluye lotes en el borde) | `python -m loadtest.gateway_report --start-ms <ms> --end-ms <ms> --tags 200 --table scada_telemetry` | Percentiles de latencia y pérdida estimada por huecos en los tags analógicos. |
| Límite de escritura de una tabla DynamoDB nueva (hipótesis de la tabla fría) | `python -m loadtest.ddb_cold_test --rate 5000 --phase-seconds 150 --phases 2` | Items servidos y rechazados por ventana en dos fases seguidas; crea una tabla temporal y la elimina. |
| Política de mínimo privilegio de IoT Core | `python -m infra.check_policy --site PLANTA-01` | Comprueba que se publica en el tema propio y se rechazan el tema y el cliente ajenos. |
| Compromiso latencia/mensajes del gateway real (intervalo de vaciado) | `python -m loadtest.flush_sweep --intervals 0.1,0.25,0.5,1.0,2.0 --tags 200 --table scada_telemetry --name flush-sweep` | Para cada intervalo: latencia p50/p95/p99, mensajes por segundo y continuidad. Levanta el simulador y el gateway solo. |
| Recuperación tras un corte de enlace | `python -m loadtest.outage_test --duration 300` (PowerShell como administrador) | Acumulación máxima, tiempo de detección y de recuperación, y la serie temporal del buffer. |

Notas de uso:

- **Relojes.** La latencia de ingesta es `hora de la Lambda - ts_ms de origen`, así que depende de que el reloj
  del equipo coincida con el de AWS. Se protege en tres capas:
  1. **Prevenir:** `loadtest\sync_clock.ps1` (consola de administrador) configura Windows para consultar
     `time.aws.com` cada 10 minutos y corrige la hora ahora.
  2. **Compensar:** la Lambda guarda la latencia cruda (puede ser negativa) y `run_matrix`, `latency_report` y
     `gateway_report` miden el desfase por SNTP y lo restan. `run_matrix` lo mide antes y después de cada corrida
     y lo registra en `runs.csv` (`clock_offset_ms`, `clock_drift_ms`, `clock_uncertainty_ms`).
  3. **Vigilar:** `run_matrix` aborta si el desfase supera `--max-clock-skew` (250 ms) o si no se puede medir
     por SNTP (UDP 123), porque entonces la corrección sería poco fiable.
  Ya no se recorta a 0: con un reloj adelantado se observó un p95 de 0 a 8 ms, con la información real
  perdida. Datos antiguos (anteriores a este cambio) no se pueden corregir. Un reloj adelantado es el caso peor: la Lambda recorta a 0 las
  latencias negativas y las latencias bajas quedan aplastadas (se observó un p95 de 0 a 8 ms con 180 ms
  de adelanto). `clock_check` usa SNTP (UDP 123); el encabezado HTTP `Date`, de resolución de 1 s, solo
  es el último recurso y llegó a dar el signo contrario. En Windows, sincronice con `w32tm /resync` desde una
  consola de administrador.
- `flush_sweep` ejecuta el simulador OPC UA y el gateway por su cuenta (no los lance antes) y mide cada intervalo de
  `GATEWAY_FLUSH_INTERVAL_S` con la cadena completa. Mide el desfase de reloj por SNTP antes y después de cada
  intervalo y lo resta de las latencias.
- `ddb_cold_test` escribe directamente en DynamoDB, sin IoT Core ni Lambda, para aislar el comportamiento de una
  tabla nueva bajo demanda: sostiene ~4,000 escrituras por segundo al principio y, en nuestra prueba, tardó unos 7 minutos de
  presión sostenida en ajustarse a 5,000 (no es un plazo que AWS garantice). Desactiva los reintentos del SDK para ver la limitación. Una corrida de
  8 × 150 s a 5,000 items/s cuesta unos 3.5 USD en escrituras.
- `check_policy` exige detener el gateway antes: IoT Core desconecta a un cliente cuando otro usa su `client_id`.
- `gateway_report` mide la cadena completa (simulador, gateway, IoT Core, Lambda y DynamoDB). OPC UA
  solo notifica cuando un valor cambia, así que la pérdida se estima por huecos en la serie de los
  tags analógicos (los de estado no emiten cada ciclo). La ventana debe excluir el arranque y los
  últimos segundos antes de detener el gateway.
- `outage_test` requiere el simulador y el gateway en ejecución y una consola de PowerShell
  **como administrador**. Simula el corte bloqueando el puerto de salida 8883 (MQTT sobre TLS)
  con el firewall de Windows y siempre elimina la regla al terminar. Quitar la política o
  desactivar el certificado en IoT Core no sirve: no cierran las conexiones ya establecidas.
- `backlog_start_s` es cuándo empieza a crecer el buffer (los lotes dejan de confirmarse), no
  cuándo el cliente MQTT declara la desconexión. `recovery_s` incluye la espera de reconexión
  del cliente (retroceso exponencial de hasta 30 s) y el vaciado, y tolera `--tolerance` lotes
  (2 por defecto) sobre la línea base, que es el ruido normal de mensajes en vuelo.
- La Lambda publica métricas en CloudWatch (espacio de nombres `ScadaIot`, dimensión `Site`):
  `ReadingsWritten`, `ReadingsRejected` e `IngestLatencyMs`, con percentiles nativos.
- `run_matrix` espera (hasta `--max-wait`) a que termine la ingesta antes de contar la pérdida:
  con carga alta DynamoDB aplica límites de escritura y la Lambda reintenta, por lo que los
  items pueden llegar decenas de segundos tarde. Medir antes cuenta como pérdida lo que solo
  está retrasado.
- El costo de escritura de DynamoDB que reporta `runs.csv` es una estimación; verifíquelo en la
  calculadora de precios de AWS. Una corrida de 5,000 tags durante 30 minutos escribe unos
  9 millones de items, por lo que la duración de la matriz debe ajustarse al presupuesto.

## Desarrollo

```powershell
uv run pytest                    # unitarias, de contrato e integración local (sin acceso a AWS)
uv run pytest -m "not integration"  # solo las rápidas, sin levantar el servidor OPC UA
uv run ruff check .              # lint
uv run ruff format --check .     # formato
uvx cfn-lint infra/template.yaml # validación de la plantilla
```

El flujo de integración continua (`.github/workflows/ci.yml`) ejecuta estos cuatro pasos en cada cambio.

## Seguridad

- Cada gateway usa un certificado X.509 propio y una política que solo le permite
  conectarse con su nombre de Thing y publicar en `plant/<site>/telemetry`.
- Las credenciales y certificados se guardan en `certs/`, que está excluida de Git.
- El simulador OPC UA no usa seguridad de transporte porque es solo para laboratorio.
  En planta real se debe usar OPC UA con cifrado y certificados de aplicación, y la
  red OT debe estar segmentada según IEC 62443.

## Limitaciones conocidas

- La entrega es al menos una vez: si el proceso cae después de la confirmación de la
  nube y antes de borrar el registro local, ese lote se reenvía y puede duplicarse.
  Como la clave de DynamoDB es (serie, tiempo), el reenvío sobrescribe el mismo item y la
  tabla no conserva duplicados; la pérdida se mide como lecturas enviadas menos almacenadas.
- Las latencias de extremo a extremo usan la hora del equipo de origen y la de la
  Lambda. Para mediciones válidas, ambos deben estar sincronizados con NTP.
- `verify_counts` hace un `Query` por tag (en paralelo) usando el conteo por tag de
  `summary.json`, por lo que solo lee las claves de la prueba. Requiere un `summary.json`
  generado por la versión actual de `mqtt_load`. Para monitoreo continuo en producción se
  usan las métricas `ScadaIot` de CloudWatch que emite la Lambda.
- Por defecto SAM crea para la Lambda un rol limitado a escribir en la tabla. Si se indica
  `ExecutionRoleName` se reutiliza un rol existente, que puede ser más permisivo que el mínimo necesario.
- El tamaño de la cola local del gateway se registra en su log y `outage_test` lo muestrea
  del buffer SQLite; no se publica en CloudWatch porque el gateway solo tiene credenciales
  de IoT Core.
- `mqtt_load` exige `--readings-per-msg` menor o igual a `--tags`: un tag repetido en el
  mismo lote tendría la misma clave y se contaría como pérdida.
- Si el servidor OPC UA deja de estar disponible, el gateway reintenta la conexión con retroceso exponencial
  (de 1 s hasta `OPC_RECONNECT_MAX_S`, 30 s por defecto) y retoma la suscripción solo; mientras tanto no se recogen
  lecturas de la planta, pero el buffer sigue enviando lo ya guardado. La caída se detecta leyendo el estado del
  servidor cada 2 s, de modo que tarda unos segundos en notarse.
- Una tabla DynamoDB bajo demanda recién creada sostiene unas 4,000 escrituras por segundo: una carga mayor se
  retrasa hasta que la capacidad se ajusta (ver `loadtest/ddb_cold_test.py` y el [registro de riesgos](docs/architecture/05-riesgos.md)).

## Licencia

[MIT](LICENSE).
