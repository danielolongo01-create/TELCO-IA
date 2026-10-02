# NOC AI Monitoring — Kinshasa

Projet de supervision télécom combinant télémétrie réelle Android/Termux, historique SQLite, alertes, mémoire épisodique ChromaDB, RAG et Qwen via Ollama.

## Architecture

```text
Android / Termux
      │
      │ RSRP, SINR, packet loss, latency
      ▼
POST /api/v1/telemetry/ingest
      │
      ├── estimation BLER si BLER réel absent
      ├── alertes RSRP / SINR / BLER
      ├── SQLite telemetry_history.db
      └── WebSocket /ws/telemetry
                    │
                    ▼
              Dashboard NOC

Chat → ChromaDB / mémoire épisodique → Ollama / Qwen
```

## 1. Ouvrir dans VS Code

Ouvre simplement le dossier `noc-app` dans VS Code.

## 2. Lancer sous WSL

Dans le terminal WSL de VS Code :

```bash
cd /chemin/vers/noc-app
bash run_wsl.sh
```

Ou manuellement :

```bash
python3 -m venv .venv-wsl
source .venv-wsl/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Puis ouvre :

```text
http://localhost:8000
```

Test de santé :

```text
http://localhost:8000/health
```

## 3. Ollama / Qwen

Le dashboard et la télémétrie fonctionnent sans Ollama. Pour le chat IA, Ollama doit tourner sur le PC et le modèle doit être disponible :

```bash
ollama pull qwen2.5
ollama run qwen2.5
```

Si Ollama tourne côté Windows et le backend côté WSL, vérifie que l'URL utilisée depuis WSL est accessible. La configuration actuelle utilise `http://localhost:11434/api/chat`.

## 4. Tester sans téléphone

Backend lancé :

```bash
python scripts/test_telemetry.py
```

Ou :

```bash
curl -X POST http://127.0.0.1:8000/api/v1/telemetry/ingest \
  -H "Content-Type: application/json" \
  -d '{"cell_id":"0x2B3C4","rsrp":-113.3,"sinr":2.7,"packet_loss":8}'
```

Le BLER sera estimé et `bler_estimated` vaudra `true`.

## 5. Téléphone Android / Termux

Sur le téléphone :

```bash
pkg update
pkg install python termux-api iputils
pip install requests
```

Il faut également installer l'application Termux:API correspondante et lui accorder les permissions nécessaires.

Dans `probe_termux.py`, modifie au minimum :

```python
SERVER = "http://IP_DU_PC:8000/api/v1/telemetry/ingest"
CELL_ID = "0x1A2B3"
SOURCE = "probe-01"
```

Puis :

```bash
python probe_termux.py
```

Le téléphone et le PC doivent pouvoir communiquer sur le même réseau local/hotspot. Si Windows Firewall bloque le port 8000, autorise Python/Uvicorn sur le réseau privé.

## 6. Deux téléphones

Utilise la même URL backend mais un identifiant différent :

Téléphone 1 :

```text
CELL_ID=0x1A2B3
SOURCE=probe-01
```

Téléphone 2 :

```text
CELL_ID=0x2B3C4
SOURCE=probe-02
```

Les deux alimentent la même base SQLite, avec `source` pour distinguer les sondes.

## 7. Historique

Pour une cellule :

```text
http://localhost:8000/api/v1/telemetry/history/0x2B3C4
```

Dernières mesures toutes cellules :

```text
http://localhost:8000/api/v1/telemetry/latest
```

## 8. Export du dataset

Après plusieurs jours de mesures :

```bash
python scripts/export_dataset.py
```

Le fichier sera créé ici :

```text
data/telemetry_dataset.csv
```

Il servira ensuite à construire le détecteur d'anomalies.

## 9. BLER réel

Tant que la sonde Android ne fournit pas le BLER radio réel, le backend calcule un proxy à partir du SINR et de la perte de paquets.

Les mesures sont explicitement marquées :

```json
"bler_estimated": true
```

Lorsqu'un vrai BLER est fourni dans la requête :

```json
"bler": 12.4
```

le backend conserve cette valeur comme mesure réelle et retourne :

```json
"bler_estimated": false
```

Le proxy devra ensuite être recalibré et validé avec les données PM réelles de l'opérateur.

## Structure

```text
noc-app/
├── main.py
├── probe_termux.py
├── requirements.txt
├── run_wsl.sh
├── run_windows.ps1
├── .env.example
├── .gitignore
├── README.md
├── telemetry_history.db          # créé automatiquement après lancement
├── frontend/
│   └── index.html
├── rag/
│   ├── __init__.py
│   ├── rag_engine.py
│   └── vector_store.py
├── data/
│   └── chroma_db/                # créé/rempli automatiquement
└── scripts/
    ├── test_telemetry.py
    └── export_dataset.py
```
