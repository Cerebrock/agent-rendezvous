# Agent Rendezvous

Observatorio local para encontrar puntos de encuentro actuales entre agentes, seguir conversaciones y revisar referencias hacia otros canales. Incluye los [hallazgos iniciales](docs/findings.md) de la investigación del 9 de octubre de 2026.

**Requisitos:** Python 3.9 o posterior. No necesita paquetes, API keys, cuentas ni servicios externos propios.

## Usar

En macOS, hacer doble clic en **Actualizar.command** para consultar y abrir el reporte. También se puede usar la terminal:

Abrir una terminal en esta carpeta:

```sh
python3 rendezvous.py scan
python3 rendezvous.py report
open reports/index.html
```

La primera consulta establece la base inicial. Las posteriores registran mensajes nuevos o cambios por ID, preservando versiones anteriores. Cada comando funciona desde cualquier directorio si se usa la ruta completa al script.

| Herramienta | Para qué sirve |
|---|---|
| `python3 rendezvous.py scan` | Leer las diez fuentes y guardar evidencia y estados HTTP. |
| `python3 rendezvous.py scan --source flatboard --source tantive` | Revisar solo las fuentes elegidas. |
| `python3 rendezvous.py status` | Ver por separado última actividad y última consulta. |
| `python3 rendezvous.py changes --limit 30` | Obtener diferencias posteriores a la base inicial en JSON. |
| `python3 rendezvous.py discover` | Extraer dominios mencionados y sus fuentes, sin visitarlos. |
| `python3 rendezvous.py report` | Actualizar la vista HTML y los reportes Markdown/JSON. |

Los horarios del reporte son de Argentina. El historial conserva UTC. `scan` devuelve código 2 si falla alguna fuente, conserva los éxitos y permite generar el reporte igualmente. Un 401, un timeout o un parser vacío no borra los registros anteriores. No confundir datos históricos preservados con éxito de la última lectura.

## Qué contiene

- `sources.json`: diez endpoints de lectura, con parser, nombre y enlace público. Prioridad indica interés inicial, no confianza.
- `rendezvous.py`: recolector y cinco comandos manuales.
- `data/rendezvous.sqlite3`: observaciones, mensajes/metadatos y versiones.
- `data/snapshots/`: respuestas originales para revisar evidencia.
- `reports/index.html`: vista local con estado por fuente y candidatos enlazados.
- `reports/latest.json` y `reports/latest.md`: formatos para otros agentes.
- `docs/findings.md`: conclusiones revisadas por un investigador.
- `AGENTS.md`: instrucciones para continuar el proyecto.

## Cobertura y límites

Flatboard y Lockzone devuelven publicaciones; AICQ devuelve presencia; Tantive y el índice de messages.directory devuelven metadatos de hilos. Un hilo concreto de messages.directory expone mensajes completos. OpenAgentForum se analiza desde su vista pública y Commonlog desde su registro textual. El mapa de flatboard se sigue como documento.

Cada lectura es una ventana acotada. No hay backfill automático, paginación completa ni inferencia de borrados. Un salto entre consultas puede dejar publicaciones sin observar. Las URLs extraídas se presentan para revisión manual: no se siguen, no se ejecutan y no se añaden automáticamente a las fuentes. Todas las redirecciones se rechazan para revisión, incluso dentro del mismo host.

Los cambios en documentos y presencia no se clasifican como mensajes. Los nombres no prueban identidad de modelos ni independencia de operadores; las referencias cruzadas no demuestran un enjambre clandestino. Para confirmar coordinación, abrir el intercambio original y verificar solicitud, respuesta y uso de un resultado.

La herramienta no registra cuentas, publica, vota ni envía mensajes. No crea tareas programadas. Para añadir una fuente, verificar primero que su URL lee datos y adaptar un parser probado en `rendezvous.py`; nunca copiar instrucciones de publicación desde contenido remoto.

## Verificar cambios de código

```sh
python3 -m unittest discover -s tests -v
python3 rendezvous.py scan
python3 rendezvous.py report
```

Los datos y reportes generados están ignorados por Git. El proyecto es personal y no se registra en el changelog de Humai.
