# RAG local

Cuadernos propios al estilo de Gemini Notebook, en local y sin depender de
ninguna API externa. Un motor compartido y tantos corpus como quieras bajo
`corpus/`.

El primero, `ia-educacion`, reúne las 15 fuentes de un cuaderno sobre uso
ético, legal y responsable de la IA en educación: 3,2 M de caracteres,
1.910 pasajes indexados.

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
| Reranking | `jina-reranker-v2-base-multilingual`, 24 candidatos → 8 |
| Interfaz | Servidor MCP (Claude Code, Codex, Antigravity) y `src/ask.py` en terminal |

Nada sale de tu máquina: los dos modelos corren en CPU sobre onnxruntime.

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
claude mcp add ia-educacion -- \
  /home/jjdeharo/Documentos/github/rag/.venv/bin/python \
  /home/jjdeharo/Documentos/github/rag/src/mcp_server.py
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
rag/
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
