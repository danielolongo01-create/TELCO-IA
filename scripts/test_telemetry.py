"""Envoie une télémétrie de test au backend local."""
import argparse
import json
import urllib.request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/api/v1/telemetry/ingest")
    parser.add_argument("--cell-id", default="0x2B3C4")
    parser.add_argument("--rsrp", type=float, default=-113.3)
    parser.add_argument("--sinr", type=float, default=2.7)
    parser.add_argument("--packet-loss", type=float, default=8.0)
    args = parser.parse_args()

    payload = {
        "cell_id": args.cell_id,
        "source": "local-test",
        "rsrp": args.rsrp,
        "sinr": args.sinr,
        "packet_loss": args.packet_loss,
    }
    request = urllib.request.Request(
        args.url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        print(response.read().decode())


if __name__ == "__main__":
    main()
