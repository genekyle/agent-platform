import json
from pathlib import Path

from settings import settings
from training import build_grounding_dataset, train_grounding_model


def main() -> None:
    artifacts_root = Path(settings.observer_artifacts_dir)
    manifest = build_grounding_dataset(artifacts_root, captures=[])
    result = train_grounding_model(artifacts_root, dataset_manifest=manifest)
    print(json.dumps({"dataset": manifest, "training": result}, indent=2))


if __name__ == "__main__":
    main()
