"""
Histórico de La Primitiva — Loterías y Apuestas del Estado
============================================================
Descarga los resultados de La Primitiva desde la API oficial de LAE,
los almacena en JSON o CSV y genera apuestas personalizadas.

Uso general:
    python primitiva_historico.py                          # Descarga desde 2000
    python primitiva_historico.py --formato csv            # Guarda en primitiva.csv
    python primitiva_historico.py --desde 2020-01-01       # Desde una fecha concreta
    python primitiva_historico.py --ultimos 10             # Últimos N sorteos
    python primitiva_historico.py --stats                  # Estadísticas
    python primitiva_historico.py --buscar 7,14,21         # Sorteos con esos números

Generador de apuestas:
    python primitiva_historico.py --apuestas               # Genera apuestas con config.json
    python primitiva_historico.py --apuestas --config mi_config.json
    python primitiva_historico.py --apuestas --para 2026-04-03
    python primitiva_historico.py --init-config            # Crea config.json de ejemplo
"""

import requests
import json
import csv
import argparse
import sys
import random
import itertools
from datetime import timedelta, date
from pathlib import Path
from collections import Counter

# ──────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────
API_BASE   = "https://www.loteriasyapuestas.es/servicios"
GAME_ID    = "LNAC"
HEADERS    = {"User-Agent": "Mozilla/5.0 (compatible; PrimitivaBot/1.0)",
              "Accept": "application/json"}
CAMPOS_CSV = ["fecha","num1","num2","num3","num4","num5","num6",
              "complementario","reintegro","joker","bote"]

CONFIG_EJEMPLO = {
    "_ayuda": {
        "mis_numeros":              "Tu lista de números favoritos (1-49)",
        "excluir_ultimos_sorteos":  "Excluir números que salieron en los últimos N sorteos (0 = sin exclusión)",
        "max_apuestas":             "Máximo de apuestas a generar",
        "estrategia": (
            "Cómo ordenar/seleccionar combinaciones:\n"
            "  'frecuencia_baja'  → prioriza números que han salido menos veces históricamente\n"
            "  'frecuencia_alta'  → prioriza los números más frecuentes\n"
            "  'equilibrado'      → mezcla de fríos y calientes en cada combinación\n"
            "  'aleatorio'        → selección aleatoria entre las candidatas"
        ),
        "semilla_aleatoria":        "Número entero para reproducibilidad en modo aleatorio (null = cada vez diferente)"
    },
    "mis_numeros":             [1, 3, 7, 12, 14, 17, 19, 21, 26, 28, 33, 35, 40, 42, 48],
    "excluir_ultimos_sorteos": 2,
    "max_apuestas":            8,
    "estrategia":              "frecuencia_baja",
    "semilla_aleatoria":       None
}

# ──────────────────────────────────────────────────────────
# Lectura / escritura del histórico
# ──────────────────────────────────────────────────────────

def leer_historico(fichero: Path) -> dict:
    if not fichero.exists():
        return {}
    ext = fichero.suffix.lower()
    if ext == ".json":
        with open(fichero, encoding="utf-8") as f:
            return {s["fecha"]: s for s in json.load(f)}
    if ext == ".csv":
        historico = {}
        with open(fichero, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                s = {
                    "fecha":          row["fecha"],
                    "num1":           int(row["num1"]),
                    "num2":           int(row["num2"]),
                    "num3":           int(row["num3"]),
                    "num4":           int(row["num4"]),
                    "num5":           int(row["num5"]),
                    "num6":           int(row["num6"]),
                    "complementario": int(row["complementario"]) if row["complementario"] else None,
                    "reintegro":      int(row["reintegro"])      if row["reintegro"]      else None,
                    "joker":          row["joker"] or None,
                    "bote":           float(row["bote"]) if row["bote"] else 0.0,
                }
                historico[s["fecha"]] = s
        return historico
    print(f"❌ Formato no reconocido: '{ext}'. Usa .json o .csv"); sys.exit(1)


def guardar_historico(historico: dict, fichero: Path):
    lista = sorted(historico.values(), key=lambda s: s["fecha"])
    ext   = fichero.suffix.lower()
    if ext == ".json":
        with open(fichero, "w", encoding="utf-8") as f:
            json.dump(lista, f, ensure_ascii=False, indent=2)
    elif ext == ".csv":
        with open(fichero, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS_CSV)
            w.writeheader(); w.writerows(lista)
    else:
        print(f"❌ Formato no reconocido: '{ext}'."); sys.exit(1)


def fusionar(historico: dict, nuevos: list) -> int:
    antes = len(historico)
    for s in nuevos:
        historico.setdefault(s["fecha"], s)
    return len(historico) - antes

# ──────────────────────────────────────────────────────────
# API de LAE
# ──────────────────────────────────────────────────────────

def _fecha_api(d: date) -> str:
    return d.strftime("%d%m%Y")


def obtener_sorteos_rango(desde: date, hasta: date) -> list:
    resultados, cursor = [], desde
    while cursor <= hasta:
        try:
            fin = min(cursor.replace(year=cursor.year + 1) - timedelta(days=1), hasta)
        except ValueError:
            fin = min(cursor.replace(month=3, day=1) - timedelta(days=1), hasta)
        url = (f"{API_BASE}/buscadorSorteos?game_id={GAME_ID}&celebrados=true"
               f"&fechaInicioIncl={_fecha_api(cursor)}&fechaFinIncl={_fecha_api(fin)}")
        print(f"  📡 {cursor} → {fin} …", end=" ", flush=True)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            bloque = [p for raw in resp.json() if (p := _parsear_sorteo(raw))]
            print(f"{len(bloque)} sorteos")
            resultados.extend(bloque)
        except requests.RequestException as e:
            print(f"ERROR — {e}")
        cursor = fin + timedelta(days=1)
    return resultados


def _parsear_sorteo(raw: dict):
    try:
        fecha_raw = raw.get("fecha_sorteo", "")
        if len(fecha_raw) != 8: return None
        fecha = f"{fecha_raw[:4]}-{fecha_raw[4:6]}-{fecha_raw[6:]}"
        nums  = [int(n) for n in raw.get("combinacion","").split("-") if n.strip().isdigit()]
        if len(nums) < 6: return None
        comp = raw.get("premier_segunda") or raw.get("complementario") or raw.get("num_complementario")
        rein = raw.get("premio_especial")  or raw.get("reintegro")
        try:
            bote = float(str(raw.get("premio_bote") or raw.get("acumulado_bote") or 0
                             ).replace(".", "").replace(",", ".") or 0)
        except ValueError:
            bote = 0.0
        return {"fecha": fecha,
                "num1": nums[0], "num2": nums[1], "num3": nums[2],
                "num4": nums[3], "num5": nums[4], "num6": nums[5],
                "complementario": int(comp) if comp else None,
                "reintegro":      int(rein)  if rein  else None,
                "joker": str(raw.get("joker","") or "").strip() or None,
                "bote":  bote}
    except (ValueError, TypeError, AttributeError):
        return None

# ──────────────────────────────────────────────────────────
# Generador de apuestas
# ──────────────────────────────────────────────────────────

def _numeros_sorteo(s: dict) -> set:
    return {s["num1"], s["num2"], s["num3"], s["num4"], s["num5"], s["num6"]}


def _calcular_frecuencias(historico: dict) -> Counter:
    c = Counter()
    for s in historico.values():
        c.update(_numeros_sorteo(s))
    return c


def _score_combinacion(combo: tuple, freq: Counter, estrategia: str) -> float:
    """
    Devuelve un score numérico para ordenar combinaciones.
    Mayor score → mejor posición en la lista.
    """
    frecs = [freq.get(n, 0) for n in combo]
    if estrategia == "frecuencia_baja":
        # Preferir números fríos: mayor score cuanto menos han salido
        return -sum(frecs)
    if estrategia == "frecuencia_alta":
        return sum(frecs)
    if estrategia == "equilibrado":
        # Mezcla: queremos combis con baja varianza entre fríos y calientes
        media = sum(frecs) / len(frecs)
        varianza = sum((f - media) ** 2 for f in frecs) / len(frecs)
        return -varianza   # menor varianza → más equilibrado → mejor
    # aleatorio: score asignado externamente
    return 0.0


def generar_apuestas(historico: dict, cfg: dict, para: date) -> list:
    """
    Lógica principal del generador:
      1. Toma los últimos N sorteos anteriores a `para`.
      2. Recopila qué números del usuario salieron en esos sorteos.
      3. Filtra la lista del usuario quitando esos números.
      4. Genera todas las combinaciones de 6 sobre los restantes.
      5. Ordena por estrategia y devuelve las mejores.
    """
    mis_numeros  = sorted(set(cfg.get("mis_numeros", [])))
    excluir_n    = int(cfg.get("excluir_ultimos_sorteos", 2))
    max_apuestas = int(cfg.get("max_apuestas", 8))
    estrategia   = cfg.get("estrategia", "frecuencia_baja")
    semilla      = cfg.get("semilla_aleatoria")

    if len(mis_numeros) < 6:
        print("❌ Necesitas al menos 6 números en 'mis_numeros'.")
        sys.exit(1)

    # Validar rango 1-49
    fuera_rango = [n for n in mis_numeros if not (1 <= n <= 49)]
    if fuera_rango:
        print(f"❌ Números fuera del rango 1-49: {fuera_rango}")
        sys.exit(1)

    # Sorteos anteriores a la fecha objetivo, ordenados desc
    anteriores = sorted(
        [s for s in historico.values() if s["fecha"] < para.isoformat()],
        key=lambda s: s["fecha"],
        reverse=True,
    )

    if not anteriores:
        print("⚠️  No hay sorteos anteriores a esa fecha en el histórico.")
        sys.exit(1)

    # Números del usuario que salieron en los últimos N sorteos
    recientes       = anteriores[:excluir_n]
    numeros_recientes = set()
    for s in recientes:
        numeros_recientes |= (_numeros_sorteo(s) & set(mis_numeros))

    candidatos = [n for n in mis_numeros if n not in numeros_recientes]

    print(f"\n  Tus números      : {mis_numeros}")
    print(f"  Últimos {excluir_n} sorteo(s) (excluidos):")
    for s in recientes:
        excl = sorted(_numeros_sorteo(s) & set(mis_numeros))
        print(f"    {s['fecha']}  →  {_numeros_sorteo(s)}  "
              f"[tuyos excluidos: {excl if excl else 'ninguno'}]")
    print(f"  Candidatos tras filtro: {candidatos}  ({len(candidatos)} números)\n")

    if len(candidatos) < 6:
        print(f"⚠️  Solo quedan {len(candidatos)} candidatos tras la exclusión; "
              f"no es posible formar 6 números.")
        print("   Prueba a reducir 'excluir_ultimos_sorteos' o ampliar 'mis_numeros'.")
        sys.exit(1)

    # Calcular frecuencias históricas
    freq = _calcular_frecuencias(historico)

    # Generar combinaciones
    todas = list(itertools.combinations(candidatos, 6))
    total_posibles = len(todas)

    if estrategia == "aleatorio":
        rng = random.Random(semilla)
        rng.shuffle(todas)
        seleccion = todas[:max_apuestas]
    else:
        todas.sort(key=lambda c: _score_combinacion(c, freq, estrategia), reverse=True)
        seleccion = todas[:max_apuestas]

    return seleccion, total_posibles, candidatos, numeros_recientes, recientes


def cmd_apuestas(historico: dict, cfg: dict, para: date):
    if not historico:
        print("⚠️  Histórico vacío. Descarga primero los datos.")
        sys.exit(1)

    estrategia   = cfg.get("estrategia", "frecuencia_baja")
    max_apuestas = int(cfg.get("max_apuestas", 8))

    apuestas, total, candidatos, excluidos, recientes = generar_apuestas(historico, cfg, para)

    freq = _calcular_frecuencias(historico)
    etiqueta_est = {
        "frecuencia_baja": "❄️  Números fríos (menos frecuentes)",
        "frecuencia_alta": "🔥 Números calientes (más frecuentes)",
        "equilibrado":     "⚖️  Combinaciones equilibradas",
        "aleatorio":       "🎲 Selección aleatoria",
    }.get(estrategia, estrategia)

    print("━" * 62)
    print(f"  🎱  Apuestas sugeridas para el sorteo del {para}")
    print(f"  Estrategia : {etiqueta_est}")
    print(f"  Candidatos : {len(candidatos)} números  |  "
          f"Combinaciones posibles: {total:,}  |  Mostrando: {len(apuestas)}")
    print("━" * 62)
    print(f"  {'#':>3}  {'COMBINACIÓN':^32}  {'FREC. TOTAL':>11}")
    print("  " + "─" * 58)
    for i, combo in enumerate(apuestas, 1):
        nums_str  = " - ".join(f"{n:2d}" for n in combo)
        frec_total = sum(freq.get(n, 0) for n in combo)
        print(f"  {i:>3}.  {nums_str:^32}  {frec_total:>11,}")
    print("━" * 62)
    print(f"\n  ℹ️  Frecuencia = nº de veces que cada número ha salido en el histórico.")
    print(f"  ℹ️  Recuerda: la lotería es aleatoria. ¡Juega con responsabilidad!")

# ──────────────────────────────────────────────────────────
# Comandos de consulta
# ──────────────────────────────────────────────────────────

def cmd_descargar(fichero: Path, desde: date, hasta: date):
    print(f"\n🎱 Descargando La Primitiva  {desde} → {hasta}")
    historico  = leer_historico(fichero)
    print(f"   Histórico existente: {len(historico)} sorteos\n")
    nuevos_raw = obtener_sorteos_rango(desde, hasta)
    insertados = fusionar(historico, nuevos_raw)
    guardar_historico(historico, fichero)
    print(f"\n✅ {insertados} nuevos sorteos añadidos  ({len(historico)} en total → {fichero})")


def cmd_ultimos(fichero: Path, n: int):
    historico = leer_historico(fichero)
    if not historico:
        print("⚠️  No hay datos."); return
    ultimos = sorted(historico.values(), key=lambda s: s["fecha"], reverse=True)[:n]
    print(f"\n{'FECHA':<12} {'NÚMEROS':^28} {'COMP':>5} {'R':>3} {'BOTE (€)':>14}")
    print("─" * 68)
    for s in ultimos:
        nums = f"{s['num1']:2d}-{s['num2']:2d}-{s['num3']:2d}-{s['num4']:2d}-{s['num5']:2d}-{s['num6']:2d}"
        comp = f"{s['complementario']:2d}" if s["complementario"] else "  "
        rein = f"{s['reintegro']:1d}"       if s["reintegro"]      else " "
        bote = f"{s['bote']:>14,.0f}"       if s["bote"]           else "             -"
        print(f"{s['fecha']:<12}  {nums:^28}  {comp:>4} {rein:>3} {bote}")


def cmd_stats(fichero: Path):
    historico = leer_historico(fichero)
    if not historico:
        print("⚠️  No hay datos aún."); return
    lista   = sorted(historico.values(), key=lambda s: s["fecha"])
    freq    = _calcular_frecuencias(historico)
    top10   = freq.most_common(10)
    bot10   = freq.most_common()[-10:][::-1]
    print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  📊  Estadísticas de La Primitiva
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Total sorteos     : {len(lista):>6,}
  Primer sorteo     : {lista[0]["fecha"]}
  Último sorteo     : {lista[-1]["fecha"]}

  🔥 10 más frecuentes  : {', '.join(f'{n}({c}x)' for n,c in top10)}
  ❄️  10 menos frecuentes: {', '.join(f'{n}({c}x)' for n,c in bot10)}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
""")


def cmd_buscar(fichero: Path, numeros: list):
    historico = leer_historico(fichero)
    campos    = {"num1","num2","num3","num4","num5","num6"}
    encontrados = [
        s for s in sorted(historico.values(), key=lambda x: x["fecha"], reverse=True)
        if all(n in {s[k] for k in campos} for n in numeros)
    ]
    print(f"\n🔍 Sorteos con {numeros}: {len(encontrados)} encontrados\n")
    for s in encontrados:
        nums = f"{s['num1']:2d}-{s['num2']:2d}-{s['num3']:2d}-{s['num4']:2d}-{s['num5']:2d}-{s['num6']:2d}"
        print(f"  {s['fecha']}  {nums}  comp:{s['complementario']}  R:{s['reintegro']}")


def cmd_init_config(ruta: Path):
    if ruta.exists():
        print(f"⚠️  Ya existe '{ruta}'. Bórralo primero si quieres regenerarlo.")
        return
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(CONFIG_EJEMPLO, f, ensure_ascii=False, indent=2)
    print(f"✅ Fichero de configuración creado: {ruta}")
    print("   Edítalo con tus números y preferencias antes de usar --apuestas.")

# ──────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Histórico y generador de apuestas de La Primitiva",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # Almacenamiento
    parser.add_argument("--formato",  choices=["json","csv"], default="json",
                        help="Formato del histórico (defecto: json)")
    parser.add_argument("--fichero",  metavar="RUTA",
                        help="Ruta del fichero de datos")
    # Descarga
    parser.add_argument("--desde",    metavar="YYYY-MM-DD")
    parser.add_argument("--hasta",    metavar="YYYY-MM-DD")
    # Consultas
    parser.add_argument("--ultimos",  metavar="N", type=int)
    parser.add_argument("--stats",    action="store_true")
    parser.add_argument("--buscar",   metavar="N1,N2,...")
    # Apuestas
    parser.add_argument("--apuestas", action="store_true",
                        help="Genera apuestas usando el fichero de configuración")
    parser.add_argument("--config",   metavar="RUTA", default="config.json",
                        help="Fichero de configuración (defecto: config.json)")
    parser.add_argument("--para",     metavar="YYYY-MM-DD",
                        help="Fecha del sorteo objetivo (defecto: hoy)")
    parser.add_argument("--init-config", action="store_true",
                        help="Crea un fichero config.json de ejemplo")

    args    = parser.parse_args()
    fichero = Path(args.fichero) if args.fichero else Path(f"primitiva.{args.formato}")

    # ── Init config ──
    if args.init_config:
        cmd_init_config(Path(args.config))
        return

    # ── Consultas rápidas ──
    if args.ultimos:
        cmd_ultimos(fichero, args.ultimos); return
    if args.stats:
        cmd_stats(fichero); return
    if args.buscar:
        nums = [int(x) for x in args.buscar.split(",") if x.strip().isdigit()]
        cmd_buscar(fichero, nums); return

    # ── Generador de apuestas ──
    if args.apuestas:
        cfg_path = Path(args.config)
        if not cfg_path.exists():
            print(f"❌ No se encuentra '{cfg_path}'.")
            print("   Ejecuta primero:  python primitiva_historico.py --init-config")
            sys.exit(1)
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
        cfg.pop("_ayuda", None)   # quitar comentarios del ejemplo

        try:
            para = date.fromisoformat(args.para) if args.para else date.today()
        except ValueError as e:
            print(f"❌ Fecha inválida: {e}"); sys.exit(1)

        historico = leer_historico(fichero)
        cmd_apuestas(historico, cfg, para)
        return

    # ── Modo descarga (defecto) ──
    hoy = date.today()
    try:
        desde = date.fromisoformat(args.desde) if args.desde else date(2000, 1, 1)
        hasta = date.fromisoformat(args.hasta) if args.hasta else hoy
    except ValueError as e:
        print(f"❌ Fecha inválida: {e}"); sys.exit(1)

    if desde > hasta:
        print("❌ La fecha de inicio no puede ser posterior a la fecha fin.")
        sys.exit(1)

    cmd_descargar(fichero, desde, hasta)
    print(); cmd_ultimos(fichero, 5)
    print(); cmd_stats(fichero)


if __name__ == "__main__":
    main()
