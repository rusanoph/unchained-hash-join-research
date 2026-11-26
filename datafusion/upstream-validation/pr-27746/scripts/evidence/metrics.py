"""Parse HashJoinExec metrics from rendered DataFusion plans."""

import re

TIME_SCALE = {"ns": 1e-6, "µs": 1e-3, "us": 1e-3, "ms": 1, "s": 1e3}
ROW_METRICS = (
    "build_input_rows", "input_rows", "output_rows", "build_mem_used",
    "array_map_created_count", "dense_index_created_count", "probe_hit_rate", "avg_fanout",
)


def parse(text):
    for depth, line in enumerate(text.splitlines(), start=1):
        if "HashJoinExec:" not in line or ", metrics=[" not in line:
            continue
        operator, metrics = line.rsplit(", metrics=[", 1)
        values = dict(re.findall(r"(?:^|, )([a-zA-Z0-9_]+)=([^,\]]+)", metrics))
        record = {"plan_line": depth, "operator": operator}
        for key in ("build_time", "join_time", "elapsed_compute"):
            match = re.fullmatch(r"([.\d]+)\s*(ns|µs|us|ms|s)", values.get(key, ""))
            if match:
                record[key + "_ms"] = float(match[1]) * TIME_SCALE[match[2]]
        record.update((key, values[key]) for key in ROW_METRICS if key in values)
        yield record
