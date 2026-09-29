"""
Tests unitarios para primitiva_historico.py
Ejecutar con:  python3 test_primitiva.py -v
"""

import csv
import json
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Importar el módulo bajo prueba ─────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
import primitiva_historico as ph

# ──────────────────────────────────────────────────────────
# Fixtures / helpers compartidos
# ──────────────────────────────────────────────────────────

def _sorteo(fecha="2024-01-01", nums=(1,2,3,4,5,6), comp=7, rein=3, bote=1_000_000.0):
    return {
        "fecha": fecha,
        "num1": nums[0], "num2": nums[1], "num3": nums[2],
        "num4": nums[3], "num5": nums[4], "num6": nums[5],
        "complementario": comp,
        "reintegro":      rein,
        "joker":          None,
        "bote":           bote,
    }

HISTORICO_EJEMPLO = {
    "2024-01-01": _sorteo("2024-01-01", (1,2,3,4,5,6)),
    "2024-01-04": _sorteo("2024-01-04", (7,8,9,10,11,12)),
    "2024-01-07": _sorteo("2024-01-07", (13,14,15,16,17,18)),
    "2024-01-11": _sorteo("2024-01-11", (19,20,21,22,23,24)),
    "2024-01-14": _sorteo("2024-01-14", (25,26,27,28,29,30)),
}

CFG_BASE = {
    "mis_numeros":             [1,3,5,7,9,11,13,15,17,19,21,23,25,27,29],
    "excluir_ultimos_sorteos": 2,
    "max_apuestas":            5,
    "estrategia":              "frecuencia_baja",
    "semilla_aleatoria":       42,
}

# ──────────────────────────────────────────────────────────
# 1. Parseo de la respuesta de la API
# ──────────────────────────────────────────────────────────

class TestParsearSorteo(unittest.TestCase):

    def _raw(self, **kwargs):
        base = {
            "fecha_sorteo": "20240101",
            "combinacion":  "5-12-23-34-41-48",
            "premier_segunda": "9",
            "premio_especial": "4",
            "joker": "1234567",
            "premio_bote": "3000000",
        }
        base.update(kwargs)
        return base

    def test_parseo_correcto(self):
        s = ph._parsear_sorteo(self._raw())
        self.assertEqual(s["fecha"], "2024-01-01")
        self.assertEqual([s["num1"],s["num2"],s["num3"],s["num4"],s["num5"],s["num6"]],
                         [5, 12, 23, 34, 41, 48])
        self.assertEqual(s["complementario"], 9)
        self.assertEqual(s["reintegro"], 4)
        self.assertEqual(s["bote"], 3_000_000.0)

    def test_fecha_invalida_devuelve_none(self):
        self.assertIsNone(ph._parsear_sorteo(self._raw(fecha_sorteo="2024010")))
        self.assertIsNone(ph._parsear_sorteo(self._raw(fecha_sorteo="")))

    def test_combinacion_incompleta_devuelve_none(self):
        self.assertIsNone(ph._parsear_sorteo(self._raw(combinacion="1-2-3-4-5")))
        self.assertIsNone(ph._parsear_sorteo(self._raw(combinacion="")))

    def test_combinacion_con_texto_ignorado(self):
        # Números válidos mezclados con basura → debe parsear solo los enteros
        s = ph._parsear_sorteo(self._raw(combinacion="1-2-3-4-5-X"))
        self.assertIsNone(s)  # solo 5 números válidos

    def test_bote_formato_europeo(self):
        # "3.000.000" (separador miles europeo) → 3_000_000.0
        s = ph._parsear_sorteo(self._raw(premio_bote="3.000.000"))
        self.assertEqual(s["bote"], 3_000_000.0)

    def test_bote_ausente(self):
        raw = self._raw()
        raw.pop("premio_bote", None)
        s = ph._parsear_sorteo(raw)
        self.assertEqual(s["bote"], 0.0)

    def test_complementario_campo_alternativo(self):
        raw = self._raw()
        raw.pop("premier_segunda", None)
        raw["num_complementario"] = "15"
        s = ph._parsear_sorteo(raw)
        self.assertEqual(s["complementario"], 15)

    def test_raw_vacio_devuelve_none(self):
        self.assertIsNone(ph._parsear_sorteo({}))

    def test_raw_none_fields(self):
        s = ph._parsear_sorteo(self._raw(premier_segunda=None, premio_especial=None))
        self.assertIsNone(s["complementario"])
        self.assertIsNone(s["reintegro"])

# ──────────────────────────────────────────────────────────
# 2. Formato de fecha para la API
# ──────────────────────────────────────────────────────────

class TestFechaApi(unittest.TestCase):

    def test_formato_correcto(self):
        self.assertEqual(ph._fecha_api(date(2024, 1, 5)),  "05012024")
        self.assertEqual(ph._fecha_api(date(2024, 12, 31)), "31122024")
        self.assertEqual(ph._fecha_api(date(2000, 6, 1)),  "01062000")

# ──────────────────────────────────────────────────────────
# 3. Lectura / escritura de ficheros
# ──────────────────────────────────────────────────────────

class TestFicheroJSON(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "primitiva.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_leer_fichero_inexistente_devuelve_vacio(self):
        self.assertEqual(ph.leer_historico(self.ruta), {})

    def test_guardar_y_leer_roundtrip(self):
        ph.guardar_historico(HISTORICO_EJEMPLO, self.ruta)
        recuperado = ph.leer_historico(self.ruta)
        self.assertEqual(set(recuperado.keys()), set(HISTORICO_EJEMPLO.keys()))
        self.assertEqual(recuperado["2024-01-01"]["num1"], 1)
        self.assertEqual(recuperado["2024-01-07"]["num4"], 16)

    def test_json_ordenado_por_fecha(self):
        ph.guardar_historico(HISTORICO_EJEMPLO, self.ruta)
        with open(self.ruta) as f:
            lista = json.load(f)
        fechas = [s["fecha"] for s in lista]
        self.assertEqual(fechas, sorted(fechas))

    def test_tipos_preservados(self):
        ph.guardar_historico(HISTORICO_EJEMPLO, self.ruta)
        r = ph.leer_historico(self.ruta)
        s = r["2024-01-01"]
        self.assertIsInstance(s["num1"], int)
        self.assertIsInstance(s["bote"], float)


class TestFicheroCSV(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "primitiva.csv"

    def tearDown(self):
        self.tmp.cleanup()

    def test_leer_fichero_inexistente_devuelve_vacio(self):
        self.assertEqual(ph.leer_historico(self.ruta), {})

    def test_guardar_y_leer_roundtrip(self):
        ph.guardar_historico(HISTORICO_EJEMPLO, self.ruta)
        recuperado = ph.leer_historico(self.ruta)
        self.assertEqual(set(recuperado.keys()), set(HISTORICO_EJEMPLO.keys()))

    def test_csv_tiene_cabecera_correcta(self):
        ph.guardar_historico(HISTORICO_EJEMPLO, self.ruta)
        with open(self.ruta, newline="") as f:
            cabecera = next(csv.reader(f))
        self.assertEqual(cabecera, ph.CAMPOS_CSV)

    def test_valores_numericos_recuperados_correctamente(self):
        ph.guardar_historico(HISTORICO_EJEMPLO, self.ruta)
        r = ph.leer_historico(self.ruta)
        s = r["2024-01-04"]
        self.assertEqual(s["num1"], 7)
        self.assertEqual(s["num6"], 12)
        self.assertIsInstance(s["bote"], float)

    def test_campos_nulos_recuperados_como_none(self):
        sorteo_sin_comp = _sorteo("2024-02-01", comp=None, rein=None)
        ph.guardar_historico({"2024-02-01": sorteo_sin_comp}, self.ruta)
        r = ph.leer_historico(self.ruta)
        self.assertIsNone(r["2024-02-01"]["complementario"])
        self.assertIsNone(r["2024-02-01"]["reintegro"])

# ──────────────────────────────────────────────────────────
# 4. Fusión del histórico
# ──────────────────────────────────────────────────────────

class TestFusionar(unittest.TestCase):

    def test_inserta_nuevos(self):
        hist = {}
        nuevos = [_sorteo("2024-03-01"), _sorteo("2024-03-04")]
        insertados = ph.fusionar(hist, nuevos)
        self.assertEqual(insertados, 2)
        self.assertIn("2024-03-01", hist)

    def test_no_duplica_existentes(self):
        hist = {"2024-03-01": _sorteo("2024-03-01")}
        nuevos = [_sorteo("2024-03-01"), _sorteo("2024-03-04")]
        insertados = ph.fusionar(hist, nuevos)
        self.assertEqual(insertados, 1)
        self.assertEqual(len(hist), 2)

    def test_no_sobreescribe_datos_existentes(self):
        original = _sorteo("2024-03-01", nums=(1,2,3,4,5,6))
        hist = {"2024-03-01": original}
        diferente = _sorteo("2024-03-01", nums=(40,41,42,43,44,45))
        ph.fusionar(hist, [diferente])
        self.assertEqual(hist["2024-03-01"]["num1"], 1)  # dato original intacto

    def test_lista_vacia(self):
        hist = {"2024-03-01": _sorteo("2024-03-01")}
        insertados = ph.fusionar(hist, [])
        self.assertEqual(insertados, 0)

# ──────────────────────────────────────────────────────────
# 5. Helpers del generador
# ──────────────────────────────────────────────────────────

class TestNumerosYFrecuencias(unittest.TestCase):

    def test_numeros_sorteo(self):
        s = _sorteo(nums=(5,10,15,20,25,30))
        self.assertEqual(ph._numeros_sorteo(s), {5,10,15,20,25,30})

    def test_calcular_frecuencias(self):
        hist = {
            "a": _sorteo(nums=(1,2,3,4,5,6)),
            "b": _sorteo(nums=(1,2,3,7,8,9)),  # 1,2,3 aparecen 2 veces
        }
        freq = ph._calcular_frecuencias(hist)
        self.assertEqual(freq[1], 2)
        self.assertEqual(freq[6], 1)
        self.assertEqual(freq[9], 1)
        self.assertEqual(freq[99], 0)  # ausente → 0

    def test_frecuencias_historico_vacio(self):
        freq = ph._calcular_frecuencias({})
        self.assertEqual(len(freq), 0)

# ──────────────────────────────────────────────────────────
# 6. Scoring de combinaciones
# ──────────────────────────────────────────────────────────

class TestScoreCombinacion(unittest.TestCase):

    def setUp(self):
        # num 1 aparece 10x, num 2 aparece 5x, num 3 aparece 1x, resto 0
        self.freq = Counter({1: 10, 2: 5, 3: 1})

    def test_frecuencia_baja_prefiere_frios(self):
        combo_fria    = (3, 4, 5, 6, 7, 8)   # frecuencias bajas
        combo_caliente = (1, 2, 3, 4, 5, 6)  # incluye num 1 y 2
        s_fria    = ph._score_combinacion(combo_fria,    self.freq, "frecuencia_baja")
        s_caliente = ph._score_combinacion(combo_caliente, self.freq, "frecuencia_baja")
        self.assertGreater(s_fria, s_caliente)

    def test_frecuencia_alta_prefiere_calientes(self):
        combo_fria     = (3, 4, 5, 6, 7, 8)
        combo_caliente = (1, 2, 3, 4, 5, 6)
        s_fria    = ph._score_combinacion(combo_fria,    self.freq, "frecuencia_alta")
        s_caliente = ph._score_combinacion(combo_caliente, self.freq, "frecuencia_alta")
        self.assertGreater(s_caliente, s_fria)

    def test_equilibrado_prefiere_baja_varianza(self):
        # Combo homogénea (todos aparecen igual) vs. heterogénea
        freq_uniforme = Counter({n: 5 for n in range(1, 50)})
        combo_uniforme  = (10, 20, 30, 40, 41, 42)   # todos freq=5
        combo_extrema   = (1, 2, 3, 4, 5, 6)         # freq 10,5,1,0,0,0
        s_u = ph._score_combinacion(combo_uniforme, freq_uniforme, "equilibrado")
        s_e = ph._score_combinacion(combo_extrema,  self.freq,      "equilibrado")
        self.assertGreater(s_u, s_e)

    def test_aleatorio_devuelve_cero(self):
        combo = (1, 2, 3, 4, 5, 6)
        self.assertEqual(ph._score_combinacion(combo, self.freq, "aleatorio"), 0.0)

# ──────────────────────────────────────────────────────────
# 7. Generador de apuestas — lógica de filtrado
# ──────────────────────────────────────────────────────────

class TestGenerarApuestas(unittest.TestCase):
    """
    Pruebas de la lógica de filtrado y generación.
    Se parchean sys.exit y print para no contaminar la salida.
    """

    def _run(self, historico, cfg, para):
        with patch("builtins.print"):
            return ph.generar_apuestas(historico, cfg, para)

    def test_genera_combinaciones_validas(self):
        apuestas, total, candidatos, excluidos, recientes = self._run(
            HISTORICO_EJEMPLO, CFG_BASE, date(2024, 1, 20)
        )
        # Cada apuesta debe tener exactamente 6 números
        for combo in apuestas:
            self.assertEqual(len(combo), 6)
            # Todos los números deben ser del pool candidato
            for n in combo:
                self.assertIn(n, candidatos)

    def test_excluye_numeros_recientes(self):
        # mis_numeros incluye el 25 y 27, que salieron en el sorteo 2024-01-14
        cfg = {**CFG_BASE, "excluir_ultimos_sorteos": 1}
        _, _, candidatos, excluidos, _ = self._run(
            HISTORICO_EJEMPLO, cfg, date(2024, 1, 20)
        )
        # 25 y 27 estaban en el último sorteo (25-26-27-28-29-30) y en mis_numeros
        self.assertNotIn(25, candidatos)
        self.assertNotIn(27, candidatos)
        self.assertNotIn(29, candidatos)

    def test_excluir_cero_no_filtra_nada(self):
        cfg = {**CFG_BASE, "excluir_ultimos_sorteos": 0}
        _, _, candidatos, excluidos, _ = self._run(
            HISTORICO_EJEMPLO, cfg, date(2024, 1, 20)
        )
        self.assertEqual(set(candidatos), set(CFG_BASE["mis_numeros"]))
        self.assertEqual(len(excluidos), 0)

    def test_max_apuestas_respetado(self):
        cfg = {**CFG_BASE, "max_apuestas": 3}
        apuestas, *_ = self._run(HISTORICO_EJEMPLO, cfg, date(2024, 1, 20))
        self.assertLessEqual(len(apuestas), 3)

    def test_total_combinaciones_correcto(self):
        cfg = {**CFG_BASE, "excluir_ultimos_sorteos": 0}
        _, total, candidatos, *_ = self._run(
            HISTORICO_EJEMPLO, cfg, date(2024, 1, 20)
        )
        import math
        esperado = math.comb(len(candidatos), 6)
        self.assertEqual(total, esperado)

    def test_estrategia_aleatoria_semilla_reproducible(self):
        cfg = {**CFG_BASE, "estrategia": "aleatorio", "semilla_aleatoria": 99}
        apuestas1, *_ = self._run(HISTORICO_EJEMPLO, cfg, date(2024, 1, 20))
        apuestas2, *_ = self._run(HISTORICO_EJEMPLO, cfg, date(2024, 1, 20))
        self.assertEqual(apuestas1, apuestas2)

    def test_frecuencia_baja_primero_tiene_menor_suma_frecuencias(self):
        cfg = {**CFG_BASE, "estrategia": "frecuencia_baja", "max_apuestas": 100}
        apuestas, _, candidatos, *_ = self._run(
            HISTORICO_EJEMPLO, cfg, date(2024, 1, 20)
        )
        freq = ph._calcular_frecuencias(HISTORICO_EJEMPLO)
        sumas = [sum(freq.get(n, 0) for n in c) for c in apuestas]
        # La lista debe estar ordenada de menor a mayor suma
        self.assertEqual(sumas, sorted(sumas))

    def test_sin_sorteos_anteriores_llama_exit(self):
        cfg = {**CFG_BASE}
        with patch("builtins.print"), self.assertRaises(SystemExit):
            ph.generar_apuestas(HISTORICO_EJEMPLO, cfg, date(2000, 1, 1))

    def test_pocos_candidatos_llama_exit(self):
        # Solo 6 números en mi lista, pero todos aparecen en los últimos 2 sorteos
        cfg = {
            **CFG_BASE,
            "mis_numeros": [19, 20, 21, 22, 23, 24, 25, 26],  # todos en últ. 2 sorteos
            "excluir_ultimos_sorteos": 2,
        }
        with patch("builtins.print"), self.assertRaises(SystemExit):
            ph.generar_apuestas(HISTORICO_EJEMPLO, cfg, date(2024, 1, 20))

    def test_numeros_fuera_de_rango_llama_exit(self):
        cfg = {**CFG_BASE, "mis_numeros": [0, 3, 5, 7, 50, 11, 13]}
        with patch("builtins.print"), self.assertRaises(SystemExit):
            ph.generar_apuestas(HISTORICO_EJEMPLO, cfg, date(2024, 1, 20))

    def test_menos_de_6_numeros_llama_exit(self):
        cfg = {**CFG_BASE, "mis_numeros": [1, 2, 3, 4, 5]}
        with patch("builtins.print"), self.assertRaises(SystemExit):
            ph.generar_apuestas(HISTORICO_EJEMPLO, cfg, date(2024, 1, 20))

    def test_numeros_duplicados_en_config_se_deduplicam(self):
        cfg = {**CFG_BASE, "mis_numeros": [1, 1, 3, 5, 7, 9, 11, 13, 15]}
        apuestas, *_ = self._run(HISTORICO_EJEMPLO, cfg, date(2024, 1, 20))
        for combo in apuestas:
            self.assertEqual(len(combo), len(set(combo)))  # sin repetidos

# ──────────────────────────────────────────────────────────
# 8. Init-config
# ──────────────────────────────────────────────────────────

class TestInitConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ruta = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_crea_fichero(self):
        with patch("builtins.print"):
            ph.cmd_init_config(self.ruta)
        self.assertTrue(self.ruta.exists())

    def test_json_valido_con_campos_obligatorios(self):
        with patch("builtins.print"):
            ph.cmd_init_config(self.ruta)
        with open(self.ruta) as f:
            cfg = json.load(f)
        for campo in ("mis_numeros", "excluir_ultimos_sorteos", "max_apuestas", "estrategia"):
            self.assertIn(campo, cfg)

    def test_no_sobreescribe_existente(self):
        self.ruta.write_text('{"mis_numeros":[99]}')
        with patch("builtins.print"):
            ph.cmd_init_config(self.ruta)
        with open(self.ruta) as f:
            cfg = json.load(f)
        self.assertEqual(cfg["mis_numeros"], [99])  # contenido original intacto

# ──────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
