"""Register the low-probe build and memory cases from review #27746."""

import argparse
import json


def case(name, key, probes, count, checksum, general=False):
    extra = ", CAST(i % 3 AS VARCHAR) AS s" if general else ""
    return {
        "id": name,
        "settings": ["datafusion.optimizer.join_reordering=false"],
        "setup": f"CREATE TABLE b AS SELECT {key} AS k, i AS v{extra} FROM generate_series(1, 1000000) AS gen(i)",
        "sql": "SELECT COUNT(*), SUM(b.v) FROM b JOIN (VALUES " + probes
               + (") p(k, s) ON b.k = p.k AND b.s = p.s" if general else ") p(k) ON b.k = p.k"),
        "oracle": [[str(count), str(checksum)]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", nargs="+", choices=("A", "B", "C"), default=("A", "B", "C"))
    parser.add_argument("--memory-limit-mb", type=int)
    args = parser.parse_args()
    config = {
        "arms": args.arms,
        "cpu": [2],
        "partitions": 1,
        "batch_size": 8192,
        "blocks": 5,
        "seed": 27746005 + (args.memory_limit_mb or 0),
        "warmups": 0,
        "address_space_gib": 4,
        "timeout_seconds": 90,
        "custom": [
            case("dense_unique", "i", "(11), (1000000), (1000001)", 2, 1000011),
            case("dense_dup100", "i % 10000", "(0), (11), (10001)", 200, 100001100),
            case("sparse_int_unique", "i * 101", "(1111), (101000000), (101000001)", 2, 1000011),
            case("sparse_int_dup100", "(i % 10000) * 1009", "(0), (11099), (10100000)", 200, 100001100),
            case("mixed_singletons_hot_key", "CASE WHEN i <= 250000 THEN 0 ELSE i END",
                 "(0), (1000000), (1000001)", 250001, 31251125000),
            case("general_composite", "i % 10000", "(0, '0'), (1, '1'), (10001, '2')", 67, 33660034,
                 general=True),
        ],
    }
    if args.memory_limit_mb:
        config["memory_limit_mb"] = args.memory_limit_mb
    print(json.dumps(config, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
