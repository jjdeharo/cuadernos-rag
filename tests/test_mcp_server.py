"""Pruebas de humo para las herramientas MCP que no cargan los modelos."""
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import mcp_server
import rag


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


if __name__ == "__main__":
    unittest.main()
