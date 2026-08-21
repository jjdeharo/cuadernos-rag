# RAG local

Cuadernos propios al estilo de Gemini Notebook, en local y sin depender de
ninguna API externa. Un motor compartido y tantos corpus como quieras bajo
`corpus/`.

El primero, `ia-educacion`, reúne las 15 fuentes de un cuaderno sobre uso
ético, legal y responsable de la IA en educación: 3,2 M de caracteres,
1.910 pasajes indexados.
Su procedencia y licencia están en [FUENTES.md](FUENTES.md).

## Qué hace

| Pieza | Implementación |
|---|---|
| Ingesta | `src/cuadernos.py` — trae un cuaderno de Gemini Notebook como corpus |
| Importación | `src/importar.py` — PDF, TXT, MD y HTML propios |
| Limpieza | `src/clean.py` — repara las palabras que el PDF partió al maquetar |
| Troceado | por artículo en los textos legales; ventana de 330 palabras en el resto |
| Etiquetado | cada pasaje lleva su rúbrica (`Artículo 22 — Decisiones individuales…`) |
| Índice | SQLite: `sqlite-vec` (vectorial) + FTS5 (BM25) en un único fichero |
| Embeddings | `intfloat/multilingual-e5-large`, en local vía ONNX (sin API) |
| Recuperación | Híbrida vectorial + BM25, fusionadas con RRF |
| Reranking | `jina-reranker-v2-base-multilingual`, 24 candidatos → 8 (máximo 12) |
| Interfaz | Servidor MCP (Claude Code, Codex, Antigravity) y `src/ask.py` en terminal |

Nada sale de tu máquina: los dos modelos corren en CPU sobre onnxruntime.

## Instalación

Necesitas Python 3.11 o superior y unos 4 GB libres (3,2 GB son los dos
modelos, que se descargan solos la primera vez).

```bash
git clone https://github.com/jjdeharo/cuadernos-rag.git
cd cuadernos-rag
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Ya está: el corpus `ia-educacion` viene con su índice construido, así que
**no hace falta indexar nada** para empezar a preguntar. La primera consulta
tarda unos minutos mientras se descargan los modelos; las siguientes, segundos.

## Uso

```bash
# Consulta directa desde la terminal
.venv/bin/python src/ask.py "¿puede un centro publicar fotos del alumnado?"
.venv/bin/python src/ask.py "riesgo alto sistemas educativos" -k 5 -d oj-l-
.venv/bin/python src/ask.py "consentimiento menores" --context   # para pegar en un prompt
```

Desde Claude Code, abre esta carpeta y pregunta en lenguaje natural: el
servidor MCP declarado en `.mcp.json` se registra solo y `CLAUDE.md` le indica
cómo citar. Registro manual en otro sitio:

```bash
claude mcp add ia-educacion -- "$PWD/.venv/bin/python" "$PWD/src/mcp_server.py"
```

## Actualizar el corpus

```bash
.venv/bin/python src/sync.py     # re-descarga desde NotebookLM
.venv/bin/python src/index.py    # indexa sólo lo que ha cambiado
```

El indexado es **incremental**: guarda una huella de cada documento y sólo
recalcula los nuevos o modificados. Añadir un PDF cuesta el tiempo de ese PDF
(≈1 min por cada 100 KB de texto), no el del corpus entero.

```bash
.venv/bin/python src/index.py --status   # qué cambiaría, sin tocar nada
.venv/bin/python src/index.py --full     # reconstruir desde cero (~50 min)
```

## Varios RAG en la misma carpeta

Los programas, el entorno de Python y los modelos son compartidos. Cada RAG es
solo sus documentos y su índice, bajo `corpus/`:

```
cuadernos-rag/
├── src/  .venv/  models/        el motor, una sola vez
└── corpus/
    └── ia-educacion/
        ├── corpus.json          título y cuaderno de origen
        ├── docs/                los documentos en texto
        └── data/index.db        el índice
```

Un RAG nuevo ocupa lo que ocupen sus documentos y su índice: unos pocos MB.

**Desde un cuaderno de Gemini Notebook:**

```bash
.venv/bin/python src/cuadernos.py listar          # tus cuadernos
.venv/bin/python src/cuadernos.py crear <url>     # lo descarga como corpus
.venv/bin/python src/index.py --corpus <nombre>
```

**Con documentos propios:**

```bash
mkdir -p corpus/mi-tema/docs
.venv/bin/python src/importar.py --corpus corpus/mi-tema ~/papers/*.pdf
.venv/bin/python src/index.py --corpus mi-tema
```

**Consultar uno u otro:**

```bash
.venv/bin/python src/ask.py "pregunta" --corpus mi-tema
.venv/bin/python src/cuadernos.py corpus           # ver todos
```

Con un solo corpus no hace falta indicar cuál. Con varios, sí.

**Cómo se cita cada documento.** Las fuentes llegan de NotebookLM con el nombre
del fichero por título (`guia-centros-educativos.pdf`), que en una cita dice
poco. Para citarlas por su nombre real, añade un mapa `titulos` en el
`corpus.json` del corpus; se aplica al mostrar, sin reindexar nada:

```json
{
  "title": "…",
  "titulos": {
    "rgpd": "Reglamento (UE) 2016/679 — Reglamento General de Protección de Datos (RGPD)"
  }
}
```

## Formatos admitidos

`importar.py` acepta PDF, TXT, MD y HTML; convierte, repara las palabras que
el PDF partió al maquetar y descarta el texto no válido. Para audio o vídeo,
transcríbelo antes (Whisper) y pasa el `.txt`.

## Añadir tus propios documentos a mano

Deja cualquier `.md` en `docs/` con este encabezado y reindexa:

```markdown
---
title: Nombre legible del documento
source_id: opcional
type: pdf
---
```

Para PDFs que no vengan de NotebookLM: `pip install pymupdf4llm` y conviértelos
a Markdown antes de dejarlos en `docs/`.

## Licencia

| Qué | Licencia |
|---|---|
| El código (`src/`) | [AGPL-3.0](LICENSE) |
| El texto propio (README, FUENTES, prompt) | [CC BY-SA 4.0](LICENSE-DOCS.md) |
| Los documentos del corpus | Cada uno el suyo — ver [FUENTES.md](FUENTES.md) |

Los documentos del corpus son obra de terceros y no se relicencian aquí. Dos de
ellos llevan cláusula NoComercial, así que el corpus **en conjunto** no puede
reutilizarse con fines comerciales; el código sí.

## Autoría

Juan José de Haro — [educacion.bilateria.org](https://educacion.bilateria.org) ·
[github.com/jjdeharo](https://github.com/jjdeharo)

Si le das uso en tu centro o en formación del profesorado, me alegra saberlo.
