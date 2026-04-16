from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.domain.models import JobRequest
from app.orchestrator.service import Orchestrator
from app.storage.job_store import JobStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="text2cypher-qa-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-job")
    create.add_argument("--schema-file")
    create.add_argument("--schema-url")
    create.add_argument("--schema-inline-json")
    create.add_argument("--mode", default="offline", choices=["online", "offline"])
    create.add_argument("--tugraph-from-env", action="store_true")
    create.add_argument("--tugraph-base-url")
    create.add_argument("--tugraph-user")
    create.add_argument("--tugraph-password")
    create.add_argument("--tugraph-graph")
    create.add_argument("--target-qa-count", type=int, default=10)

    run = subparsers.add_parser("run-job")
    run.add_argument("job_id")

    subparsers.add_parser("list-jobs")

    show = subparsers.add_parser("show-job")
    show.add_argument("job_id")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    orchestrator = Orchestrator()
    store = JobStore()

    if args.command == "create-job":
        if not any([args.schema_file, args.schema_url, args.schema_inline_json]):
            parser.error("One of --schema-file, --schema-url, or --schema-inline-json is required.")

        schema_source = {"type": "inline"}
        schema_input = None
        if args.schema_file:
            schema_source = {"type": "file", "file_path": args.schema_file}
        elif args.schema_url:
            schema_source = {"type": "url", "url": args.schema_url}
        elif args.schema_inline_json:
            schema_input = json.loads(args.schema_inline_json)
            schema_source = {"type": "inline", "inline_json": schema_input}

        tugraph_source = {"type": "env" if args.tugraph_from_env else "inline"}
        tugraph_config = {
            "base_url": args.tugraph_base_url,
            "username": args.tugraph_user,
            "password": args.tugraph_password,
            "graph": args.tugraph_graph,
        }
        job = orchestrator.create_job(
            JobRequest(
                mode=args.mode,
                schema_input=schema_input,
                schema_source=schema_source,
                tugraph_source=tugraph_source,
                tugraph_config=tugraph_config,
                output_config={"target_qa_count": args.target_qa_count},
            )
        )
        print(job.model_dump_json(indent=2))
        return

    if args.command == "run-job":
        job = orchestrator.run_job(args.job_id)
        print(job.model_dump_json(indent=2))
        return

    if args.command == "list-jobs":
        print(json.dumps([job.model_dump() for job in store.list()], ensure_ascii=False, indent=2))
        return

    if args.command == "show-job":
        print(store.get(args.job_id).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
