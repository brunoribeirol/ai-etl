"""CLI entry point — python -m ai_etl run --spec "..." --output ./runs/"""

import argparse
import uuid

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(prog="ai_etl")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a pipeline from a NL spec")
    run_parser.add_argument("--spec", required=True, help="Natural language pipeline specification")
    run_parser.add_argument("--output", default="./runs", help="Directory for audit output")

    args = parser.parse_args()

    if args.command == "run":
        from ai_etl.audit.db import save_run
        from ai_etl.core.graph import build_graph
        from ai_etl.core.state import initial_state

        run_id = str(uuid.uuid4())
        state = initial_state(spec=args.spec, run_id=run_id)

        print(f"[ai-etl] Starting pipeline run {run_id}")
        graph = build_graph()
        final_state = graph.invoke(state)

        json_path = save_run(final_state, log_dir=args.output)
        status = final_state["status"]
        print(f"[ai-etl] Pipeline {status}. Audit log: {json_path}")

        if final_state.get("error"):
            print(f"[ai-etl] Error: {final_state['error']}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
