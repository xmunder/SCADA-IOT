# Vista de despliegue

## Qué se despliega y cómo

```mermaid
flowchart LR
    subgraph LOCAL["Equipo local (zona OT simulada)"]
        S["Simulador OPC UA :4840"]
        G["Gateway"]
        CERT["certs/<br/>certificado X.509 + clave<br/>(fuera de Git)"]
        S --> G
        CERT -.-> G
    end
    subgraph REGION["Una región de AWS"]
        subgraph STACK["Pila CloudFormation: scada-iot-ingest (SAM)"]
            TR["Regla de IoT"]
            FN["Función Lambda"]
            ROLE["Rol de ejecución<br/>(mínimo privilegio, o uno existente)"]
            TB[("Tabla DynamoDB scada_telemetry")]
            TR --> FN --> TB
            ROLE -.-> FN
        end
        subgraph FUERA["Fuera de la pila (provision_device.py)"]
            TH["Thing PLANTA-01"]
            CE["Certificado + política scada-gateway-policy"]
        end
        BR["Broker MQTT de IoT Core"]
    end
    G -- "8883, TLS mutuo" --> BR --> TR
    CE --- TH
```

- **Infraestructura como código:** `infra/template.yaml` (SAM), desplegada con `infra/deploy.ps1`. Un único comando
  crea la tabla, la función, el rol y la regla.
- **Fuera de la pila:** el Thing, el certificado y la política los crea `infra/provision_device.py` por dispositivo.
  `sam delete` **no** los elimina.
- **Parámetros de la plantilla:** `TableName` (por defecto `scada_telemetry`) y `ExecutionRoleName`. Vacío, SAM crea
  un rol que solo permite escribir en la tabla; con un valor reutiliza un rol existente.
- **Cuotas que condicionan el diseño** (documentación de AWS): 100 publicaciones por segundo por conexión en IoT
  Core, y 4,000 escrituras por segundo sostenidas en una tabla DynamoDB bajo demanda nueva. Ver
  [riesgos](05-riesgos.md).

## Entorno utilizado

| | Cuenta de pruebas |
|---|---|
| Región | us-east-2 (la única permitida por una política de organización) |
| Rol de la Lambda | Creado por SAM, mínimo privilegio |
| Timestream / InfluxDB | LiveAnalytics bloqueado; InfluxDB, según la consola, exige plan de pago |
| Plan | Gratuito, con créditos que caducan el 16 de mayo de 2027 |

El contexto y la decisión están en el [ADR 0005](../adr/0005-region-de-despliegue.md).
