"""Exporte la télémétrie SQLite en CSV pour l'analyse et l'entraînement."""
from pathlib import Path
import csv
import sqlite3

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "telemetry_history.db"
OUT = ROOT / "data" / "telemetry_dataset.csv"


def main() -> None:
    if not DB.exists():
        raise SystemExit("telemetry_history.db n'existe pas encore. Lancez le backend et envoyez des mesures.")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    cursor = con.execute(
        """
        SELECT ts, cell_id, source, rsrp, sinr, bler, bler_estimated,
               prb_load, packet_loss, latency_ms, status
        FROM telemetry ORDER BY ts ASC
        """
    )
    rows = cursor.fetchall()
    headers = [column[0] for column in cursor.description]

    with OUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)

    con.close()
    print(f"Dataset exporté : {OUT}")
    print(f"Nombre de mesures : {len(rows)}")


if __name__ == "__main__":
    main()
