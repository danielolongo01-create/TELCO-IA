from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from rag.rag_engine import VectorMemoryStore

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
DB_PATH = BASE_DIR / "telemetry_history.db"
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5"

CELL_NAMES: Dict[str, str] = {
    "0x1A2B3": "Kinshasa Secteur A (Gombe)",
    "0x1A2B4": "Kinshasa Secteur A (Gombe)",
    "0x1A2B5": "Kinshasa Secteur A (Gombe)",
    "0x2B3C4": "Kinshasa Secteur B (Limete)",
    "0x2B3C5": "Kinshasa Secteur B (Limete)",
    "0x3C4D5": "Kinshasa Secteur C (Ngaliema)",
    "0x3C4D6": "Kinshasa Secteur C (Ngaliema)",
}

EPISODE_COOLDOWN_S = int(os.getenv("NOC_EPISODE_COOLDOWN_S", "300"))  # 1 épisode / cellule / 5 min

THRESHOLDS = {
    "rsrp": -110.0,
    "sinr": 0.0,
    "bler": 15.0,
}

SYSTEM_PROMPT = (
    "Tu es un ingénieur expert NOC 5G spécialisé dans la gestion des réseaux à Kinshasa. "
    "Tu analyses RSRP, SINR, BLER, PRB, perte de paquets et latence ainsi que le contexte RAG fourni. "
    "Appuie-toi en priorité sur les FICHES DE RÉFÉRENCE, les INCIDENTS PASSÉS et les MESURES EN DIRECT du contexte ; si une information manque, dis-le au lieu de l'inventer. "
    "Réponds de manière pédagogique et naturelle. Distingue toujours les mesures réelles des valeurs estimées. "
    "Explique les causes probables avec prudence et propose des actions techniques concrètes. "
    "IMPORTANT : les MESURES EN DIRECT de la sonde te sont fournies automatiquement dans chaque échange. "
    "Utilise-les directement, cite les valeurs chiffrées, et ne demande JAMAIS à l'utilisateur de te les fournir. "
    "Si l'utilisateur te salue ou pose une question générale, réponds brièvement puis résume l'état actuel du lien. "
    "Si le contexte indique qu'aucune mesure n'est disponible ou qu'elle est ancienne, dis-le clairement. "
    "N'utilise aucune balise <think>."
)


def init_db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            cell_id TEXT NOT NULL,
            source TEXT NOT NULL,
            rsrp REAL NOT NULL,
            sinr REAL,
            bler REAL,
            bler_estimated INTEGER NOT NULL DEFAULT 1,
            prb_load REAL,
            packet_loss REAL,
            latency_ms REAL,
            status TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            cell_id TEXT NOT NULL,
            anomaly_type TEXT NOT NULL,
            summary TEXT NOT NULL
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_ep_ts ON episodes(ts)")
    for col in ("lat REAL", "lon REAL", "accuracy REAL"):
        try:
            con.execute(f"ALTER TABLE telemetry ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
    con.execute("CREATE INDEX IF NOT EXISTS idx_cell_ts ON telemetry(cell_id, ts)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_source_ts ON telemetry(source, ts)")
    con.commit()
    return con


def estimate_bler(sinr: Optional[float], packet_loss: Optional[float]) -> float:
    """Proxy provisoire du BLER.

    Cette heuristique devra être recalibrée dès que des valeurs BLER réelles
    provenant d'un opérateur ou d'un équipement radio seront disponibles.
    """
    estimated = 0.5
    if sinr is not None:
        estimated += max(0.0, 8.0 - sinr) * 1.2
    if packet_loss is not None:
        estimated += packet_loss * 0.8
    return round(min(max(estimated, 0.0), 100.0), 2)


def evaluate_alert(rsrp: float, sinr: Optional[float], bler: float, cell_id: str) -> dict:
    reasons: list[str] = []
    types: list[str] = []
    if rsrp < THRESHOLDS["rsrp"]:
        reasons.append(f"RSRP {rsrp} dBm (seuil {THRESHOLDS['rsrp']})")
        types.append("rsrp_faible")
    if sinr is not None and sinr < THRESHOLDS["sinr"]:
        reasons.append(f"SINR {sinr} dB (seuil {THRESHOLDS['sinr']})")
        types.append("sinr_faible")
    if bler >= THRESHOLDS["bler"]:
        reasons.append(f"BLER {bler}% (seuil {THRESHOLDS['bler']}%)")
        types.append("bler_eleve")

    if not reasons:
        return {"has_alert": False, "message": "", "reasons": [], "types": []}

    name = CELL_NAMES.get(cell_id, cell_id)
    return {
        "has_alert": True,
        "message": f"DÉGRADATION CRITIQUE sur {name} ({cell_id}) : " + "; ".join(reasons) + ".",
        "reasons": reasons,
        "types": types,
    }


last_episode_ts: Dict[str, float] = {}


def maybe_record_episode(data, bler: float, bler_estimated: bool, alert: dict, now: float) -> bool:
    """Enregistre un épisode (SQLite + mémoire vectorielle) si alerte et délai écoulé."""
    if not alert["has_alert"] or db is None:
        return False
    if now - last_episode_ts.get(data.cell_id, 0.0) < EPISODE_COOLDOWN_S:
        return False
    last_episode_ts[data.cell_id] = now

    name = CELL_NAMES.get(data.cell_id, data.cell_id)
    when = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    parts = [f"{when} - Cellule {data.cell_id} ({name}) : " + "; ".join(alert["reasons"]) + "."]
    if data.packet_loss is not None:
        parts.append(f"Perte de paquets {data.packet_loss}%.")
    if data.latency_ms is not None:
        parts.append(f"Latence {data.latency_ms} ms.")
    parts.append(f"BLER {'estimé' if bler_estimated else 'réel'} {bler}%.")
    summary = " ".join(parts)
    anomaly = "+".join(alert["types"])

    cur = db.execute(
        "INSERT INTO episodes (ts, cell_id, anomaly_type, summary) VALUES (?, ?, ?, ?)",
        (now, data.cell_id, anomaly, summary),
    )
    db.commit()
    if rag_store is not None:
        try:
            rag_store.index_episode(cur.lastrowid, data.cell_id, anomaly, summary)
        except Exception as exc:
            print(f"[RAG] indexation épisode impossible : {exc}")
    return True


def _grade(value: Optional[float], bands: list[tuple[float, str]], default: str) -> str:
    if value is None:
        return "inconnu"
    for limit, label in bands:
        if value >= limit:
            return label
    return default


def _rsrp_quality(v: Optional[float]) -> str:
    return _grade(v, [(-80, "excellent"), (-90, "bon"), (-100, "moyen"), (-110, "faible")], "critique")


def _sinr_quality(v: Optional[float]) -> str:
    return _grade(v, [(20, "excellent"), (13, "bon"), (0, "moyen")], "mauvais")


def _val(v: Optional[float], unit: str) -> str:
    return "non disponible" if v is None else f"{v} {unit}"


def get_live_snapshot(cell_id: str) -> Optional[dict]:
    """Dernière mesure : cellule demandée > dernière mesure toutes cellules > base SQLite."""
    live = latest_state.get(cell_id)
    if live is None and latest_state:
        live = max(latest_state.values(), key=lambda p: p["ts"])
    if live is None and db is not None:
        row = db.execute(
            "SELECT ts, cell_id, source, rsrp, sinr, bler, bler_estimated, prb_load, packet_loss, "
            "latency_ms, lat, lon, accuracy FROM telemetry ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if row:
            live = dict(row)
            live["bler_estimated"] = bool(live["bler_estimated"])
            live["alert"] = {"has_alert": False, "message": ""}
    return live


def build_live_context(cell_id: str) -> str:
    """Bloc texte décrivant les mesures temps réel, injecté dans le prompt du LLM."""
    live = get_live_snapshot(cell_id)
    if live is None:
        return (
            "=== MESURES EN DIRECT ===\n"
            "Aucune mesure n'a encore été reçue de la sonde. Indique-le à l'utilisateur "
            "et suggère de vérifier que probe_termux.py tourne sur le téléphone."
        )

    now = time.time()
    age = int(now - live["ts"])
    if age <= 30:
        fresh = "FLUX ACTIF (mesure récente)"
    elif age <= 300:
        fresh = "mesure un peu ancienne : la sonde semble ralentie ou arrêtée"
    else:
        fresh = "MESURE ANCIENNE : la sonde ne transmet plus, dis-le à l'utilisateur"

    lines = [
        f"=== MESURES EN DIRECT (sonde {live.get('source')}, cellule {live.get('cell_id')}, il y a {age} s) ===",
        f"État du flux : {fresh}",
        f"RSRP : {_val(live.get('rsrp'), 'dBm')} (qualité {_rsrp_quality(live.get('rsrp'))})",
        f"SINR : {_val(live.get('sinr'), 'dB')} (qualité {_sinr_quality(live.get('sinr'))})",
        f"BLER : {_val(live.get('bler'), '%')} ({'ESTIMÉ à partir du SINR et de la perte de paquets, pas une mesure radio' if live.get('bler_estimated') else 'réel'})",
        f"Perte de paquets : {_val(live.get('packet_loss'), '%')}",
        f"Latence : {_val(live.get('latency_ms'), 'ms')}",
    ]
    if live.get("prb_load") is not None:
        lines.append(f"Charge PRB : {live['prb_load']} %")
    if live.get("lat") is not None and live.get("lon") is not None:
        lines.append(f"Position de la sonde : {live['lat']:.5f}, {live['lon']:.5f}")
    alert = live.get("alert") or {}
    lines.append("Alerte en cours : " + (alert["message"] if alert.get("has_alert") else "aucune"))

    # Tendance sur les 2 dernières minutes
    if db is not None:
        r = db.execute(
            "SELECT COUNT(*) n, AVG(rsrp) rsrp_avg, MIN(rsrp) rsrp_min, MAX(rsrp) rsrp_max, "
            "AVG(sinr) sinr_avg, MIN(sinr) sinr_min, AVG(bler) bler_avg, MAX(bler) bler_max, "
            "AVG(latency_ms) lat_avg FROM telemetry WHERE cell_id = ? AND ts > ?",
            (live.get("cell_id"), now - 120),
        ).fetchone()
        if r and r["n"] and r["n"] > 1:
            def f(x):  # arrondi sûr
                return "n/a" if x is None else round(x, 1)
            lines.append(
                f"Tendance sur 2 min ({r['n']} mesures) : RSRP moy {f(r['rsrp_avg'])} "
                f"(min {f(r['rsrp_min'])}, max {f(r['rsrp_max'])}) dBm ; SINR moy {f(r['sinr_avg'])} dB "
                f"(min {f(r['sinr_min'])}) ; BLER moy {f(r['bler_avg'])} % (max {f(r['bler_max'])}) ; "
                f"latence moy {f(r['lat_avg'])} ms"
            )
    lines.append(
        f"Seuils d'alerte : RSRP < {THRESHOLDS['rsrp']} dBm, SINR < {THRESHOLDS['sinr']} dB, BLER >= {THRESHOLDS['bler']} %."
    )
    return "\n".join(lines)


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                dead.append(connection)
        for connection in dead:
            self.disconnect(connection)


manager = ConnectionManager()
db: sqlite3.Connection | None = None
rag_store: VectorMemoryStore | None = None
conversation_history: List[Dict[str, str]] = []
latest_state: Dict[str, dict] = {}  # dernière mesure reçue par cellule


@asynccontextmanager
async def lifespan(_: FastAPI):
    global db, rag_store
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = init_db()
    try:
        rag_store = VectorMemoryStore(persist_dir="data/chroma_db")
        n = rag_store.ingest_knowledge()
        print(f"[RAG] base de connaissances prête ({n} fiche(s) (ré)indexée(s))")
    except Exception as exc:
        print(f"[RAG] erreur à l'initialisation : {exc}")
    yield
    if db is not None:
        db.close()
        db = None


app = FastAPI(title="NOC AI Monitoring Kinshasa", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def read_root() -> HTMLResponse:
    return HTMLResponse((FRONTEND_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "noc-app",
        "database": DB_PATH.name,
        "ollama_url": OLLAMA_URL,
        "ollama_model": OLLAMA_MODEL,
    }


class ChatRequest(BaseModel):
    cell_id: str = "0x1A2B3"
    message: str = Field(..., min_length=1, max_length=5000)


async def generate_qwen_response(messages_history: List[Dict[str, str]], context_episodes: str) -> str:
    full_system_prompt = SYSTEM_PROMPT
    if context_episodes.strip():
        full_system_prompt += (
            "\n\n--- CONTEXTE RAG / MÉMOIRE ÉPISODIQUE ---\n"
            + context_episodes
            + "\n--- FIN DU CONTEXTE ---"
        )

    messages = [{"role": "system", "content": full_system_prompt}] + messages_history

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": 500, "temperature": 0.3},
                },
            )
            response.raise_for_status()
            content = response.json().get("message", {}).get("content", "")
            if content:
                return content
    except Exception as exc:
        print(f"[OLLAMA] {exc}")

    return "Désolé, l'agent LLM Qwen local est inaccessible. Vérifiez qu'Ollama est lancé et que le modèle qwen2.5 est installé."


@app.post("/api/v1/chat")
async def chat_endpoint(request: ChatRequest) -> dict:
    user_msg = request.message.strip()
    rag_context = ""

    if rag_store is not None:
        try:
            knowledge = rag_store.search_knowledge(user_msg, top_k=3)
            if knowledge:
                rag_context += "FICHES DE RÉFÉRENCE :\n" + "\n".join(
                    f"• {k['summary']}" for k in knowledge if k.get("summary")
                )
        except Exception as exc:
            print(f"[RAG knowledge] {exc}")
        try:
            episodes = rag_store.search_similar_episodes(
                f"Cellule {request.cell_id}: {user_msg}", top_k=2, cell_id=request.cell_id
            )
            if episodes:
                rag_context += "\n\nINCIDENTS PASSÉS SIMILAIRES :\n" + "\n".join(
                    f"• {e['summary']}" for e in episodes if e.get("summary")
                )
        except Exception as exc:
            print(f"[RAG episodes] {exc}")

    live_block = build_live_context(request.cell_id)
    # Les mesures passent en tête du prompt système (là où le modèle les lit le mieux)
    rag_context = live_block + "\n\n" + rag_context

    # Historique conservé propre ; les mesures sont ajoutées au dernier message utilisateur uniquement
    conversation_history.append(
        {"role": "user", "content": f"[Cellule {request.cell_id}] {user_msg}"}
    )
    recent_history = [dict(m) for m in conversation_history[-6:]]
    recent_history[-1]["content"] += "\n\n(Mesures actuelles de la sonde, à utiliser pour répondre :\n" + live_block + ")"
    answer = await generate_qwen_response(recent_history, rag_context)
    conversation_history.append({"role": "assistant", "content": answer})

    return {"response": answer, "cell_id": request.cell_id}


class TelemetryIngest(BaseModel):
    cell_id: str = Field(default="0x1A2B3", min_length=1, max_length=64)
    source: str = Field(default="probe", min_length=1, max_length=64)
    rsrp: float = Field(..., ge=-140, le=-40, description="dBm")
    sinr: Optional[float] = Field(None, ge=-30, le=50, description="dB")
    bler: Optional[float] = Field(None, ge=0, le=100, description="% réel si fourni")
    prb_load: Optional[float] = Field(None, ge=0, le=100)
    packet_loss: Optional[float] = Field(None, ge=0, le=100, description="%")
    latency_ms: Optional[float] = Field(None, ge=0, le=60000)
    lat: Optional[float] = Field(None, ge=-90, le=90)
    lon: Optional[float] = Field(None, ge=-180, le=180)
    accuracy: Optional[float] = Field(None, ge=0)


@app.post("/api/v1/telemetry/ingest")
async def ingest_telemetry(data: TelemetryIngest) -> dict:
    if db is None:
        raise RuntimeError("Base de données non initialisée")

    bler_estimated = data.bler is None
    bler = data.bler if data.bler is not None else estimate_bler(data.sinr, data.packet_loss)
    alert = evaluate_alert(data.rsrp, data.sinr, bler, data.cell_id)
    now = time.time()

    db.execute(
        """
        INSERT INTO telemetry
        (ts, cell_id, source, rsrp, sinr, bler, bler_estimated,
         prb_load, packet_loss, latency_ms, status, lat, lon, accuracy)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now,
            data.cell_id,
            data.source,
            data.rsrp,
            data.sinr,
            bler,
            int(bler_estimated),
            data.prb_load,
            data.packet_loss,
            data.latency_ms,
            "critical" if alert["has_alert"] else "ok",
            data.lat,
            data.lon,
            data.accuracy,
        ),
    )
    db.commit()
    episode_recorded = maybe_record_episode(data, bler, bler_estimated, alert, now)

    payload = {
        "episode_recorded": episode_recorded,
        "cell_id": data.cell_id,
        "rsrp": data.rsrp,
        "sinr": data.sinr,
        "bler": bler,
        "bler_estimated": bler_estimated,
        "prb_load": data.prb_load,
        "packet_loss": data.packet_loss,
        "latency_ms": data.latency_ms,
        "lat": data.lat,
        "lon": data.lon,
        "accuracy": data.accuracy,
        "source": data.source,
        "alert": alert,
        "current_telemetry": data.model_dump(),
        "ts": now,
    }
    latest_state[data.cell_id] = payload
    await manager.broadcast(payload)

    return {
        "status": "success",
        "event": "CRITICAL_ALERT" if alert["has_alert"] else "STREAMING",
        "cell_id": data.cell_id,
        "bler": bler,
        "bler_estimated": bler_estimated,
        "episode_recorded": episode_recorded,
    }


@app.get("/api/v1/episodes")
async def list_episodes(
    limit: int = Query(default=20, ge=1, le=200),
    cell_id: Optional[str] = None,
) -> list[dict]:
    if db is None:
        raise RuntimeError("Base de données non initialisée")
    if cell_id:
        rows = db.execute(
            "SELECT id, ts, cell_id, anomaly_type, summary FROM episodes WHERE cell_id = ? ORDER BY ts DESC LIMIT ?",
            (cell_id, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT id, ts, cell_id, anomaly_type, summary FROM episodes ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/v1/telemetry/history/{cell_id}")
async def get_history(
    cell_id: str,
    limit: int = Query(default=200, ge=1, le=5000),
) -> list[dict]:
    if db is None:
        raise RuntimeError("Base de données non initialisée")

    rows = db.execute(
        """
        SELECT ts, cell_id, source, rsrp, sinr, bler, bler_estimated,
               prb_load, packet_loss, latency_ms, status, lat, lon, accuracy
        FROM telemetry
        WHERE cell_id = ?
        ORDER BY ts DESC
        LIMIT ?
        """,
        (cell_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


@app.get("/api/v1/telemetry/latest")
async def latest_telemetry(limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
    if db is None:
        raise RuntimeError("Base de données non initialisée")
    rows = db.execute(
        """
        SELECT ts, cell_id, source, rsrp, sinr, bler, bler_estimated,
               prb_load, packet_loss, latency_ms, status, lat, lon, accuracy
        FROM telemetry ORDER BY ts DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]


@app.websocket("/ws/telemetry")
async def websocket_telemetry(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        for state in latest_state.values():
            await websocket.send_json(state)
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
