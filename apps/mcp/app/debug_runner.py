import argparse
import asyncio
import json

from app.artifacts import write_observation_artifact
from app.fixtures import list_observer_fixtures, load_observer_fixture


def main():
    parser = argparse.ArgumentParser(description="Run the vision-first observer against fixture or live data.")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    fixture_parser = subparsers.add_parser("fixture", help="Emit a named observer fixture")
    fixture_parser.add_argument("name", choices=list_observer_fixtures())

    live_parser = subparsers.add_parser("live", help="Run against live MCP")
    live_parser.add_argument("--scenario", default="live_capture")

    args = parser.parse_args()

    if args.mode == "fixture":
        artifact = load_observer_fixture(args.name)
        path = write_observation_artifact(artifact)
        print(json.dumps(artifact, indent=2))
        print(f"\nartifact_path={path}")
        return

    from app.main import observe_live_capture

    artifact = asyncio.run(observe_live_capture(scenario=args.scenario))
    path = write_observation_artifact(artifact)
    print(json.dumps(artifact, indent=2))
    print(f"\nartifact_path={path}")


if __name__ == "__main__":
    main()
