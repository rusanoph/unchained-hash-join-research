"""Derive SNAP count/sum oracles and two-input key statistics from the archived Parquet tables."""

import argparse
import json
from pathlib import Path
import re
import subprocess

from digests import sha256

PACKAGE = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdb", type=Path, required=True)
    parser.add_argument("--graphs", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    args = parser.parse_args()
    con = [str(args.duckdb), "-batch", "-init", "/dev/null", "-json", ":memory:"]
    historical = json.loads((PACKAGE / "workloads/historical.json").read_text(encoding="utf-8-sig"))
    inputs = {}
    rows = {}
    for query in historical["queries"]:
        if query["data"] not in ("graphs", "holdout"):
            continue
        sql = query["sql"]
        name, = set(re.findall(r"\b(?:FROM|JOIN)\s+(\w+)\s+[abc]\b", sql, re.I))
        source = getattr(args, query["data"]) / f"{name}.parquet"
        if name not in inputs:
            inputs[name] = sha256(source)
        source_literal = "'" + str(source.resolve()).replace("'", "''") + "'"
        view = f"SET threads=1; CREATE VIEW {name} AS SELECT * FROM read_parquet({source_literal});"

        def fetch(text):
            raw = subprocess.check_output([*con, "-c", view + text], text=True)
            return json.loads(raw)

        record, = fetch(sql)
        expected = [[None if value is None else str(value) for value in record.values()]]
        if "oracle" in query:
            assert expected == query["oracle"], query["id"]
        result = {"expected": expected, "table": name}
        join = re.search(r"\bON\s+a\.(src|dst)\s*=\s*b\.(src|dst)", sql, re.I)
        if sql.upper().count(" JOIN ") == 1 and join:
            left, right = join.groups()
            condition = " WHERE src % 64 = 0" if re.search(r"WHERE\s+a\.src\s*%\s*64\s*=\s*0", sql, re.I) else ""
            if not condition:
                assert "WHERE " not in sql.upper(), query["id"]
            result["frequencies"], = fetch(f"""
                WITH a AS (SELECT {left} AS k, count(*) AS f FROM {name}{condition} GROUP BY 1),
                b AS (SELECT {right} AS k, count(*) AS f FROM {name} GROUP BY 1),
                v AS (SELECT k, a.f AS l, b.f AS r FROM a FULL JOIN b USING (k)),
                totals AS (SELECT sum(l) AS left_rows, sum(r) AS right_rows FROM v)
                SELECT first(left_rows) AS left_rows, first(right_rows) AS right_rows,
                    count(l) AS left_distinct, count(r) AS right_distinct,
                    count(*) FILTER (WHERE l IS NOT NULL AND r IS NOT NULL) AS shared_keys,
                    max(l) AS max_left_frequency, max(r) AS max_right_frequency,
                    min(k) FILTER (WHERE l IS NOT NULL) AS min_left_key,
                    max(k) FILTER (WHERE l IS NOT NULL) AS max_left_key,
                    min(k) FILTER (WHERE r IS NOT NULL) AS min_right_key,
                    max(k) FILTER (WHERE r IS NOT NULL) AS max_right_key,
                    sum(l::DOUBLE * l) / first(left_rows) AS left_size_biased_multiplicity,
                    sum(r::DOUBLE * r) / first(right_rows) AS right_size_biased_multiplicity,
                    sum(l) FILTER (WHERE r IS NOT NULL) AS matching_left_rows,
                    sum(r) FILTER (WHERE l IS NOT NULL) AS matching_right_rows,
                    sum(l::HUGEINT * r) AS matches,
                    least(1.0, sum(least(coalesce(l, 0)::DOUBLE / left_rows,
                                       coalesce(r, 0)::DOUBLE / right_rows))) AS distribution_overlap
                FROM v CROSS JOIN totals
            """)
            assert str(result["frequencies"]["matches"]) == expected[0][0], query["id"]
            result.update(left_key=left, right_key=right, left_predicate=condition.strip())
        rows[query["id"]] = result
    print(json.dumps({"inputs": inputs, "queries": rows}, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
