#!/usr/bin/env python3
"""Run an explicitly configured 5-split, 2-estimator deterministic Iris-like task."""
import argparse
import json
import sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

SUPPORTED = {"nearest_centroid", "nearest_neighbor"}


def parse_features(row, index):
    features = [Decimal(str(value)) for value in row["features"]]
    if not features or not all(value.is_finite() for value in features):
        raise ValueError(f"invalid features at dataset index {index}")
    return features


def distance(left, right):
    return sum((a - b) ** 2 for a, b in zip(left, right))


def predict(name, train, features):
    if name == "nearest_neighbor":
        ranked = sorted((distance(features, row[1]), str(row[2]), row[0]) for row in train)
        return ranked[0][1]
    groups = defaultdict(list)
    for index, values, label in train:
        groups[label].append(values)
    centroids = []
    for label, rows in groups.items():
        count = Decimal(len(rows))
        centroid = [sum(row[position] for row in rows) / count for position in range(len(rows[0]))]
        centroids.append((distance(features, centroid), str(label)))
    return min(centroids, key=lambda item: (item[0], item[1]))[1]


def run(config):
    errors = []
    if not config.get("config_source"):
        errors.append({"code": "CONFIG_SOURCE_REQUIRED"})
    rows = []
    try:
        for index, row in enumerate(config.get("dataset", [])):
            rows.append((index, parse_features(row, index), str(row["label"])))
    except (InvalidOperation, ValueError, TypeError, KeyError) as exc:
        errors.append({"code": "DATASET_INVALID", "message": str(exc)})
    if not rows:
        errors.append({"code": "DATASET_EMPTY"})
    if len({len(row[1]) for row in rows}) > 1:
        errors.append({"code": "FEATURE_LENGTH_MISMATCH"})
    splits = config.get("splits", [])
    estimators = config.get("estimators", [])
    if len(splits) != 5:
        errors.append({"code": "FIVE_SPLITS_REQUIRED", "actual": len(splits)})
    if len(estimators) != 2 or len(set(estimators)) != 2:
        errors.append({"code": "TWO_DISTINCT_ESTIMATORS_REQUIRED", "actual": estimators})
    unsupported = [name for name in estimators if name not in SUPPORTED]
    if unsupported:
        errors.append({"code": "ESTIMATOR_NOT_IMPLEMENTED", "values": unsupported,
                       "action": "use the exact task-requested estimator from the verified runtime instead of relabeling this fallback"})
    results = []
    if not errors:
        for split_index, split in enumerate(splits):
            train_indices, test_indices = split.get("train_indices", []), split.get("test_indices", [])
            valid_index = lambda i: isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(rows)
            if not train_indices or not test_indices or set(train_indices) & set(test_indices) or not all(valid_index(i) for i in train_indices + test_indices):
                errors.append({"code": "SPLIT_INDICES_INVALID", "split": split_index})
                continue
            if len(set(train_indices)) != len(train_indices) or len(set(test_indices)) != len(test_indices):
                errors.append({"code": "SPLIT_DUPLICATE_INDEX", "split": split_index})
                continue
            train = [rows[index] for index in train_indices]
            tests = [rows[index] for index in test_indices]
            for estimator in estimators:
                predictions = []
                correct = 0
                try:
                    for index, features, label in tests:
                        predicted = predict(estimator, train, features)
                        correct += int(predicted == label)
                        predictions.append({"dataset_index": index, "actual": label, "predicted": predicted})
                    results.append({"split": split.get("id", split_index), "estimator": estimator,
                                    "train_indices": train_indices, "test_indices": test_indices,
                                    "correct": correct, "total": len(tests), "accuracy": str(Decimal(correct) / Decimal(len(tests))),
                                    "predictions": predictions, "execution_status": "actually_computed_from_supplied_rows"})
                except (InvalidOperation, ValueError, TypeError, IndexError) as exc:
                    errors.append({"code": "ESTIMATOR_RUNTIME_FAILURE", "split": split_index, "estimator": estimator, "message": str(exc)})
    return {"case_id": config.get("case_id"), "status": "pass" if not errors and len(results) == 10 else "failed",
            "config_source": config.get("config_source"), "randomness": "none; explicit train/test indices and deterministic tie order",
            "completed_combinations": len(results), "results": results, "errors": errors,
            "scope": "deterministic computation on supplied synthetic or case data; not a wet experiment or official score"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    try:
        raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = run(raw.get("iris", raw))
        code = 0 if result["status"] == "pass" else 2
    except (OSError, json.JSONDecodeError, TypeError, KeyError, AttributeError) as exc:
        result = {"status": "failed", "completed_combinations": 0, "errors": [{"code": "INPUT_OR_RUNTIME_ERROR", "message": str(exc)}]}
        code = 2
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{result['status']}: {output}; combinations={result['completed_combinations']}; exit={code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
