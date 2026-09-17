"""Run a real team mission from the terminal, without creating or sending email."""
import argparse
import json
from dotenv import load_dotenv
from engine import Team
from storage import ROOT, Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", type=int, default=8)
    parser.add_argument("--queries", type=int, default=16)
    parser.add_argument("--assess", type=int, default=24)
    parser.add_argument("--prepare", help="Prepare the application with this stable ID")
    args = parser.parse_args()
    if not 1 <= args.max <= 32 or not 1 <= args.queries <= 16 or not 1 <= args.assess <= 32:
        parser.error("Limits: max 1..32, queries 1..16, assess 1..32")
    load_dotenv(ROOT / ".env")
    store = Store()
    store.migrate()
    kind = "prepare" if args.prepare else "hunt"
    payload = {"max": args.max, "query_limit": args.queries, "assessment_limit": args.assess, "profile": store.get("profile")}
    if args.prepare:
        payload["application_id"] = args.prepare
    run = store.new_run(kind, payload)
    print(f"Mission {run['id']}", flush=True)
    Team(store).execute(run["id"])
    result = store.run(run["id"])
    print(json.dumps({k: result[k] for k in ("id", "status", "summary", "errors")}, ensure_ascii=True, indent=2))
    if result["status"] in {"failed", "budget_paused"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
