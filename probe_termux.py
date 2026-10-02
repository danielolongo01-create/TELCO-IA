"""Sonde Android Termux -> backend NOC.

Installation sur le téléphone :
    pkg update
    pkg install python termux-api iputils   # + app Termux:API (F-Droid) + permission Localisation
    pip install requests

Configuration rapide : modifier SERVER, CELL_ID et SOURCE ci-dessous.
Puis :
    python probe_termux.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Any

import requests

def resolve_server_url() -> str:
    """URL passée en argument > variable NOC_SERVER > erreur claire."""
    url = sys.argv[1] if len(sys.argv) > 1 else os.getenv("NOC_SERVER", "")
    url = url.strip().strip('"')
    if not url:
        sys.exit("Usage : python probe_termux.py \"https://xxx.trycloudflare.com\"")
    if "/api/v1/telemetry/ingest" not in url:
        url = url.rstrip("/") + "/api/v1/telemetry/ingest"
    if "URL-ACTUELLE" in url.upper() or "VRAIE-URL" in url.upper():
        sys.exit("Vous avez collé l'URL d'exemple : copiez la vraie URL du tunnel.")
    return url


SERVER = resolve_server_url()
CELL_ID = os.getenv("NOC_CELL_ID", "0x1A2B3")
SOURCE = os.getenv("NOC_SOURCE", "probe-01")
INTERVAL_S = int(os.getenv("NOC_INTERVAL_S", "3"))
PING_TARGET = os.getenv("NOC_PING_TARGET", "8.8.8.8")


def run(command: list[str], timeout: int = 15) -> str:
    return subprocess.check_output(command, timeout=timeout, text=True)


def first_number(*values: Any) -> float | None:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def read_lte() -> dict[str, Any] | None:
    raw = run(["termux-telephony-cellinfo"])
    cells = json.loads(raw)
    if isinstance(cells, dict):
        cells = cells.get("cellInfo", []) or cells.get("cells", [])

    for wanted in ("lte", "nr"):
        for cell in cells:
            if str(cell.get("type", "")).lower() == wanted and cell.get("registered") is True:
                return cell
    seen = [(c.get("type"), c.get("registered")) for c in cells]
    print(f"[DEBUG] Cellules vues (type, registered) : {seen}")
    return None


def ping_stats() -> tuple[float | None, float | None]:
    """Retourne (latence moyenne ms, perte %).

    Ces mesures servent de proxy réseau pour le BLER lorsque le BLER radio
    réel n'est pas exposé par Android/Termux.
    """
    try:
        output = run(
            ["ping", "-c", "2", "-W", "1", PING_TARGET],
            timeout=6,
        )
    except Exception:
        return None, 100.0

    loss_match = re.search(r"([\d.]+)%\s+packet loss", output)
    rtt_match = re.search(r"=\s*[\d.]+/([\d.]+)/", output)

    latency = float(rtt_match.group(1)) if rtt_match else None
    loss = float(loss_match.group(1)) if loss_match else None
    return latency, loss


_gps_state = {"last_msg": None}


def _gps_log(msg: str) -> None:
    if msg != _gps_state["last_msg"]:
        print(msg)
        _gps_state["last_msg"] = msg


def get_location() -> tuple[float | None, float | None, float | None]:
    """Position via Termux:API : réseau (rapide) > GPS > dernière position connue."""
    for provider, mode, timeout in (("network", "once", 15), ("gps", "once", 30), ("gps", "last", 10)):
        try:
            out = run(["termux-location", "-p", provider, "-r", mode], timeout=timeout)
            d = json.loads(out)
            if "latitude" in d:
                _gps_log(f"[GPS] OK via {provider}/{mode} : {d['latitude']:.5f}, {d['longitude']:.5f}")
                return d["latitude"], d["longitude"], d.get("accuracy")
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue
    _gps_log("[GPS] Aucune position : activez la Localisation Android et accordez-la à Termux:API")
    return None, None, None


# --- Mesures lentes (ping, GPS) faites en arrière-plan : la boucle reste à INTERVAL_S ---
_slow: dict[str, Any] = {"latency": None, "loss": None, "lat": None, "lon": None, "acc": None}


def _ping_loop() -> None:
    while True:
        _slow["latency"], _slow["loss"] = ping_stats()
        time.sleep(1)


def _gps_loop() -> None:
    while True:
        lat, lon, acc = get_location()
        if lat is not None:
            _slow["lat"], _slow["lon"], _slow["acc"] = lat, lon, acc
        time.sleep(5)


def build_payload(cell: dict[str, Any]) -> dict[str, Any]:
    rsrp = first_number(cell.get("rsrp"), cell.get("ssRsrp"), cell.get("dbm"))
    sinr = first_number(cell.get("rssnr"), cell.get("sinr"), cell.get("ssSinr"))
    latency, packet_loss = _slow["latency"], _slow["loss"]

    payload: dict[str, Any] = {
        "cell_id": CELL_ID,
        "source": SOURCE,
        "rsrp": rsrp,
        "sinr": sinr,
        "packet_loss": packet_loss,
        "latency_ms": latency,
        "lat": _slow["lat"],
        "lon": _slow["lon"],
        "accuracy": _slow["acc"],
    }
    return {key: value for key, value in payload.items() if value is not None}


def main() -> None:
    print("=" * 60)
    print("NOC TELEMETRY PROBE")
    print(f"SERVER : {SERVER}")
    print(f"CELL   : {CELL_ID}")
    print(f"SOURCE : {SOURCE}")
    print(f"INTERVAL : {INTERVAL_S}s")
    print("=" * 60)

    session = requests.Session()
    threading.Thread(target=_ping_loop, daemon=True).start()
    threading.Thread(target=_gps_loop, daemon=True).start()

    while True:
        try:
            cell = read_lte()
            if not cell:
                print("[WARN] Aucune cellule LTE enregistrée")
            else:
                payload = build_payload(cell)
                if "rsrp" not in payload:
                    print("[WARN] Cellule LTE trouvée, mais RSRP indisponible")
                else:
                    response = session.post(SERVER, json=payload, timeout=8)
                    print(f"[OK] HTTP {response.status_code} | {response.text}")
        except KeyboardInterrupt:
            print("\nArrêt de la sonde.")
            break
        except Exception as exc:
            print(f"[ERROR] {exc}")

        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    main()
