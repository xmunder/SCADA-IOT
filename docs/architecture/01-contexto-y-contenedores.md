# Vista de contexto y de contenedores

Notación C4 simplificada con diagramas Mermaid (se renderizan en GitHub).

## Contexto

El sistema transporta telemetría de tags OPC UA desde una planta hacia AWS y la deja almacenada para análisis. **No hay
aplicación consumidora de los datos** (tableros, analítica): los únicos lectores de DynamoDB son los scripts de
medición de `loadtest/`.

```mermaid
flowchart LR
    OP["Ingeniero de datos / evaluador<br/>(persona)"]
    PLC["Servidor OPC UA de planta<br/>(simulado en este prototipo)"]
    SYS["Pipeline de telemetría SCADA -> AWS<br/>(este sistema)"]
    AWSSVC["Servicios gestionados de AWS<br/>IoT Core, Lambda, DynamoDB, CloudWatch"]
    PLC -- "OPC UA (IEC 62541)<br/>suscripción a tags" --> SYS
    SYS -- "MQTT/TLS, API de AWS" --> AWSSVC
    OP -- "ejecuta pruebas y consulta resultados" --> SYS
```

## Contenedores

```mermaid
flowchart TB
    subgraph OT["Zona de planta (OT), equipo local"]
        SIM["Simulador OPC UA<br/>Python, asyncua<br/>simulator/"]
        subgraph GW["Gateway de borde (un proceso asyncio)<br/>gateway/"]
            RD["Lector OPC UA<br/>suscripcion, 500 ms"]
            BT["Agrupador de lotes<br/>100 lecturas o 1 s"]
            BF[("Buffer SQLite<br/>WAL, synchronous=FULL")]
            FW["Reenviador<br/>ventana de 16 mensajes en vuelo"]
            PB["Publicador MQTT<br/>paho, QoS 1, TLS mutuo"]
            RD --> BT --> BF --> FW --> PB
        end
        SIM -- "OPC UA" --> RD
    end
    subgraph CLOUD["Nube (TI), una región de AWS"]
        IOT["AWS IoT Core<br/>broker MQTT + política por Thing"]
        RULE["Regla de IoT<br/>SELECT * FROM 'plant/+/telemetry'"]
        LMB["Lambda de ingesta<br/>Python 3.12, 256 MB, 30 s<br/>infra/ingest/app.py"]
        SRULE["Regla de IoT<br/>SELECT * FROM 'plant/+/status'"]
        STL["Lambda de estado<br/>infra/status/status_app.py"]
        DDB[("DynamoDB bajo demanda<br/>series + ts_ms")]
        CW["CloudWatch<br/>logs y métricas EMF ScadaIot"]
        IOT --> RULE --> LMB
        IOT --> SRULE --> STL
        STL -.-> CW
        LMB -- "BatchWriteItem" --> DDB
        LMB -.-> CW
    end
    PB -- "MQTT/TLS, puerto 8883<br/>plant/SITE/telemetry y plant/SITE/status" --> IOT
    LT["Herramientas de prueba<br/>loadtest/ (generador, matriz,<br/>corte, informes)"]
    LT -. "publica carga sintética" .-> IOT
    LT -. "Query por serie" .-> DDB
```

| Contenedor | Responsabilidad | Tecnología | Código |
|---|---|---|---|
| Simulador OPC UA | Exponer N tags con rampas, senoidales y estados | Python, asyncua | `simulator/` |
| Gateway de borde | Leer, agrupar, persistir y publicar con confirmación | Python, asyncio, SQLite, paho-mqtt | `gateway/` |
| AWS IoT Core | Autenticar al dispositivo y enrutar mensajes | Servicio gestionado | `infra/template.yaml` |
| Lambda de ingesta | Validar lecturas, medir latencia y escribir idempotente | Python 3.12 | `infra/ingest/app.py` |
| Lambda de estado | Convertir el latido del gateway en métricas (sin acceso a DynamoDB) | Python 3.12 | `infra/status/status_app.py` |
| DynamoDB | Almacenar un item por lectura | Servicio gestionado | `infra/template.yaml` |
| Alarmas y panel | Avisar de errores, latencia alta, cola grande y gateway caído | CloudWatch, SNS | `infra/template.yaml` |
| Herramientas de prueba | Carga, medición, cortes y verificación | Python, boto3 | `loadtest/` |

Las decisiones que dan forma a estos contenedores están en los [ADR](../adr/).
