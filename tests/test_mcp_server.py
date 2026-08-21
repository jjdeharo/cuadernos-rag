"""Pruebas de humo para las herramientas MCP que no cargan los modelos."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mcp_server
import rag
import index as indexador


class MCPServerToolsTest(unittest.TestCase):
    corpus = "ia-educacion"

    def test_listar_corpus(self):
        self.assertIn(self.corpus, mcp_server.listar_corpus())

    def test_listar_fuentes_usa_el_corpus(self):
        stats = rag.resolve_corpus(self.corpus).stats()
        resultado = mcp_server.listar_fuentes(self.corpus)
        self.assertIn(
            f"{stats['chunks']} pasajes en {len(stats['docs'])} documentos",
            resultado,
        )
        self.assertIn("rgpd", resultado)

    def test_leer_pasaje_usa_el_corpus(self):
        corpus = rag.resolve_corpus(self.corpus)
        with corpus.connect() as db:
            chunk_id = db.execute("SELECT MIN(id) FROM chunks").fetchone()[0]
        resultado = mcp_server.leer_pasaje(chunk_id, contexto=0,
                                           corpus=self.corpus)
        self.assertIn(f"#{chunk_id}]", resultado)

    def test_leer_documento_usa_el_corpus(self):
        resultado = mcp_server.leer_documento(
            "rgpd", desde=0, longitud=300, corpus=self.corpus
        )
        self.assertTrue(resultado.startswith("# rgpd (caracteres 0-300)"))

    def test_leer_documento_no_admite_rutas(self):
        resultado = mcp_server.leer_documento(
            "../../README", corpus=self.corpus
        )
        self.assertIn("No encuentro", resultado)

    def test_corpus_desconocido_devuelve_texto(self):
        """Un nombre que no existe es una respuesta, no un error de herramienta."""
        resultado = mcp_server.listar_fuentes("no-existe-este-corpus")
        self.assertIn("No encuentro el corpus", resultado)

    def test_corpus_sin_indexar_devuelve_texto(self):
        with tempfile.TemporaryDirectory() as tmp:
            sin_indice = Path(tmp) / "vacio"
            (sin_indice / "docs").mkdir(parents=True)
            (sin_indice / "docs" / "x.md").write_text("hola", encoding="utf-8")
            resultado = mcp_server.listar_fuentes(str(sin_indice))
        self.assertIn("no está indexado", resultado)


class FiltroPorDocumentoTest(unittest.TestCase):
    """El filtro por documento no debe vaciar la rama vectorial.

    Con el filtro aplicado por fuera del MATCH, sqlite-vec calculaba los
    vecinos de todo el índice y el JOIN los descartaba después: quedaban cero
    o un puñado, y la búsqueda filtrada se convertía en BM25 a secas.
    """

    corpus = "ia-educacion"

    def test_el_filtro_no_vacia_los_vecinos(self):
        corpus = rag.resolve_corpus(self.corpus)
        with corpus.connect() as db:
            por_tamano = [r[0] for r in db.execute(
                "SELECT doc_slug FROM chunks GROUP BY doc_slug"
                " HAVING COUNT(*) >= 40 ORDER BY COUNT(*) DESC")]
            # La consulta sale del documento más grande y el filtro apunta a
            # uno pequeño: así los 40 vecinos globales son todos del grande y
            # el filtro se nota. Un vector ya indexado evita cargar el modelo.
            grande, pequeno = por_tamano[0], por_tamano[-1]
            crudo = db.execute(
                "SELECT embedding FROM chunks_vec WHERE chunk_id ="
                " (SELECT MIN(id) FROM chunks WHERE doc_slug = ?)", (grande,)
            ).fetchone()[0]
            consulta = struct.unpack(f"{rag.EMBED_DIM}f", crudo)

            filtrados = rag._vec_hits(db, consulta, 40, pequeno)
            self.assertEqual(len(filtrados), 40, f"filtrando por {pequeno}")
            ajenos = db.execute(
                f"SELECT COUNT(*) FROM chunks WHERE doc_slug != ? AND id IN"
                f" ({','.join('?' * len(filtrados))})",
                [pequeno, *[r["id"] for r in filtrados]],
            ).fetchone()[0]
            self.assertEqual(ajenos, 0)


class SinSolapesTest(unittest.TestCase):
    """Dos ventanas consecutivas comparten texto: sólo debe salir una."""

    @staticmethod
    def pasaje(id, slug, ini, fin):
        return {"id": id, "doc_slug": slug, "start_char": ini, "end_char": fin}

    def test_descarta_el_solapado_y_repone(self):
        elegidos = rag._sin_solapes([
            self.pasaje(1, "a", 0, 2000),
            self.pasaje(2, "a", 1600, 3600),    # solapa 400 con el 1: sobra
            self.pasaje(3, "a", 3600, 5600),    # pegado al 2, sin solape: vale
        ], k=2)
        self.assertEqual([r["id"] for r in elegidos], [1, 3])

    def test_el_solape_solo_cuenta_dentro_del_mismo_documento(self):
        elegidos = rag._sin_solapes([
            self.pasaje(1, "a", 0, 2000),
            self.pasaje(2, "b", 0, 2000),
        ], k=2)
        self.assertEqual([r["id"] for r in elegidos], [1, 2])

    def test_un_roce_minimo_no_es_redundancia(self):
        elegidos = rag._sin_solapes([
            self.pasaje(1, "a", 0, 2000),
            self.pasaje(2, "a", 1950, 3950),    # 50 caracteres: por debajo del 10 %
        ], k=2)
        self.assertEqual(len(elegidos), 2)


class TitulosTest(unittest.TestCase):

    def test_el_titulo_curado_manda_en_la_cita(self):
        corpus = rag.resolve_corpus("ia-educacion")
        pasaje = {"id": 1, "doc_slug": "rgpd", "doc_title": "rgpd.pdf",
                  "section": "Artículo 5", "text": "…"}
        cita = rag.format_context([pasaje], corpus.titulos)
        self.assertIn("[rgpd#1] Reglamento (UE) 2016/679", cita)

    def test_sin_titulo_curado_se_limpia_el_nombre_de_fichero(self):
        self.assertEqual(
            rag.titulo_legible("Guía_INTEF_2024.pdf", "guia"),
            "Guía INTEF 2024",
        )
        self.assertEqual(rag.titulo_legible("", "guia"), "guia")


class SeccionesLegalesTest(unittest.TestCase):

    def test_articulo_y_rubrica_en_la_misma_linea(self):
        secciones = rag.find_sections("\n".join(
            f"Artículo {n}.  Título del artículo {n}. 1. Contenido suficiente."
            for n in range(1, 13)
        ))
        self.assertEqual(len(secciones), 12)
        self.assertEqual(secciones[6][1], "Artículo 7 — Título del artículo 7")

    def test_ignora_entradas_del_indice(self):
        cuerpo = "\n".join(
            [f"Artículo {n}. Título. . . . . {n + 10}" for n in range(1, 13)]
            + [f"Artículo {n}. Título real {n}. 1. Texto." for n in range(1, 13)]
        )
        secciones = rag.find_sections(cuerpo)
        self.assertEqual(len(secciones), 12)
        self.assertTrue(all("real" in nombre for _, nombre in secciones))

    def test_no_usa_una_url_como_rubrica(self):
        cuerpo = "\n".join(
            sum(([f"Artículo {n}", f"https://example.test/a{n}", "Texto"]
                 for n in range(1, 13)), [])
            + [f"Artículo {n}. Título real {n}. 1. Texto."
               for n in range(1, 13)]
        )
        secciones = rag.find_sections(cuerpo)
        self.assertEqual(len(secciones), 12)
        self.assertTrue(all("http" not in nombre for _, nombre in secciones))


class CorpusDanadoTest(unittest.TestCase):

    def test_listar_corpus_no_falla_por_un_indice_incompleto(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "roto"
            (base / "docs").mkdir(parents=True)
            (base / "data").mkdir()
            (base / "data" / "index.db").touch()
            with mock.patch.object(rag, "CORPUS_DIR", Path(tmp)):
                resultado = mcp_server.listar_corpus()
        self.assertIn("roto: índice no utilizable", resultado)


class IndexadoIncrementalTest(unittest.TestCase):

    def test_un_documento_que_queda_vacio_retira_los_pasajes_antiguos(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            fichero = base / "docs" / "fuente.md"
            fichero.parent.mkdir()
            fichero.write_text("", encoding="utf-8")
            db = rag.connect(base / "index.db")
            rag.create_schema(db)
            db.execute(
                "INSERT INTO chunks(doc_slug, doc_title, text) VALUES (?,?,?)",
                ("fuente", "Fuente", "contenido antiguo"),
            )
            db.execute(
                "INSERT INTO documents(slug, title, hash, n_chunks)"
                " VALUES (?,?,?,1)",
                ("fuente", "Fuente", "hash-antiguo"),
            )
            db.commit()
            indexador.indexar(db, [fichero])
            pasajes = db.execute(
                "SELECT COUNT(*) FROM chunks WHERE doc_slug = 'fuente'"
            ).fetchone()[0]
            registrado = db.execute(
                "SELECT n_chunks FROM documents WHERE slug = 'fuente'"
            ).fetchone()[0]
            db.close()
        self.assertEqual(pasajes, 0)
        self.assertEqual(registrado, 0)


if __name__ == "__main__":
    unittest.main()
