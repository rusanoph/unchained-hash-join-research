"""Prepare external inputs with the archived source checks and export recipes."""

import argparse
import csv
import gzip
import json
from pathlib import Path
import re
import subprocess
from urllib.request import urlretrieve

from digests import sha256

ROOT = Path(__file__).resolve().parents[1]
GRAPHS = {"soc-Epinions1": "epinions", "web-Google": "webgoogle", "ca-AstroPh": "astroph",
          "wiki-Vote": "wikivote", "soc-Slashdot0902": "slashdot"}
HOLDOUT = {"email-Enron": ("enron", 183831, True), "ca-HepPh": ("hepph", 118521, True),
           "amazon0302": ("amazon", 1234877, False)}
IMDB_SHA256 = "25f9d893c54f903366e0c263f88db0d429dbc2b159d4987ebc1e203242a7e988"


def literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def acquire(directory, name, expected, url):
    file = directory / name
    if not file.exists():
        temporary = file.with_suffix(file.suffix + ".partial")
        urlretrieve(url, temporary)
        if sha256(temporary) != expected:
            raise ValueError("Source checksum mismatch: " + url)
        temporary.replace(file)
    if sha256(file) != expected:
        raise ValueError("Source checksum mismatch: " + str(file))
    return file


def copy(sql, output, archive_group_size=True):
    group = ", ROW_GROUP_SIZE 122880" if archive_group_size else ""
    return f"COPY ({sql}) TO {literal(output)} (FORMAT PARQUET, COMPRESSION ZSTD{group});"


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("kind", choices=("graphs", "holdout", "enron", "job", "sf10"))
    cli.add_argument("--duckdb", required=True, type=Path)
    cli.add_argument("--sources", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    cli.add_argument("--threads", type=int, default=1)
    cli.add_argument("--database", type=Path, help="Existing DuckDB JOB or SF10 snapshot")
    args = cli.parse_args()
    args.sources.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True)
    receipt = json.loads((ROOT / "workloads/input-receipt.json").read_bytes())

    def query(sql, db=None, readonly=False):
        options = ["-readonly"] if readonly else []
        return subprocess.check_output([str(args.duckdb.resolve()), "-bail", "-batch",
                                        "-noheader", "-list", *options, str(db or ":memory:"), "-c",
                                        f"SET threads={args.threads};\n" + sql], text=True).strip()

    if args.kind in ("graphs", "holdout", "enron"):
        holdout = args.kind != "graphs"
        hashes = receipt["holdout_sources_sha256" if holdout else "original_graph_sources_sha256"]
        definitions = HOLDOUT if holdout else GRAPHS
        if args.kind == "enron":
            definitions = {"email-Enron": HOLDOUT["email-Enron"]}
        for name, definition in definitions.items():
            gz = acquire(args.sources, name + ".txt.gz", hashes[name + ".txt.gz"],
                         f"https://snap.stanford.edu/data/{name}.txt.gz")
            if holdout:
                table, expected, undirected = definition
                select = (f"SELECT * FROM read_csv({literal(gz.resolve())}, delim=chr(9), header=false, "
                          "skip=4, auto_detect=false, columns={'src':'INTEGER','dst':'INTEGER'})")
                if undirected:
                    select += " WHERE src <= dst"
                file_name, shuffle_name = table + "_file", table + "_shuffle"
                order = "hash(src, dst, 20261118), src, dst"
            else:
                table = definition
                tsv = args.out.parent / (table + ".tsv")
                with gzip.open(gz, "rt") as stream, tsv.open("x", newline="\n") as dest:
                    dest.writelines(line for line in stream if not line.startswith("#"))
                select = (f"SELECT src,dst FROM read_csv({literal(tsv.resolve())}, delim=chr(9), "
                          "header=false, columns={'src':'INTEGER','dst':'INTEGER'})")
                expected = None
                file_name, shuffle_name = table, table + "_sh"
                order = "hash(src, dst)"
            sql = f"CREATE TABLE edges AS {select}; SELECT count(*) FROM edges;"
            sql += copy("SELECT * FROM edges", args.out / (file_name + ".parquet"), not holdout)
            sql += copy("SELECT * FROM edges ORDER BY " + order, args.out / (shuffle_name + ".parquet"), not holdout)
            count = int(query(sql))
            if expected is not None and count != expected:
                raise ValueError(f"{table}: expected {expected} rows, got {count}")
    else:
        database = args.database or args.out.parent / (args.kind + ".duckdb")
        if args.database and not database.is_file():
            raise FileNotFoundError(database)
        if not args.database:
            if database.exists():
                raise FileExistsError(database)
            if args.kind == "sf10":
                query("LOAD tpch; CALL dbgen(sf=10);", database)
            else:
                tar = acquire(args.sources, "imdb.tgz", IMDB_SHA256, "https://event.cwi.nl/da/job/imdb.tgz")
                csv = args.sources / "imdb-csv"
                csv.mkdir(exist_ok=True)
                subprocess.run(["tar", "-xzf", tar, "-C", csv], check=True)
                sql = (csv / "schematext.sql").read_text()
                for file in sorted(csv.glob("*.csv")):
                    if not re.fullmatch(r"[a-z_]+", file.stem):
                        raise ValueError(file)
                    sql += f"\nCOPY {file.stem} FROM {literal(file.resolve())} (DELIMITER ',', QUOTE '\"', ESCAPE '\\');"
                query(sql, database)
        tables = sorted(p.removesuffix(".parquet") for p in receipt["job_parquet_sha256"]) if args.kind == "job" else (
            "customer", "lineitem", "nation", "orders", "part", "partsupp", "region", "supplier")
        cohort = "job" if args.kind == "job" else "tpch-sf10"
        with (ROOT / "workloads/expected-row-counts.csv").open() as stream:
            expected_counts = {table: int(n) for group, table, n in csv.reader(stream) if group == cohort}
        for table in tables:
            count = int(query(f'SELECT count(*) FROM "{table}"', database, readonly=True))
            if count != expected_counts[table]:
                raise ValueError(f"{table}: expected {expected_counts[table]} rows, got {count}")
            query(copy(f'SELECT * FROM "{table}"', args.out / (table + ".parquet")), database, readonly=True)
    reference = (json.loads((ROOT / "results/raw/hj-official-sf10-002-registration.json").read_bytes())["inputs"]
                 if args.kind == "sf10" else receipt[{"graphs": "graph", "enron": "holdout"}.get(args.kind, args.kind)
                                                     + "_parquet_sha256"])
    for file in sorted(args.out.glob("*.parquet")):
        key = file.stem if args.kind == "sf10" else file.name
        got = sha256(file)
        print(f"{got}  {file.name}  {'matched' if got == reference.get(key) else 'different or unrecorded'}")


if __name__ == "__main__":
    main()
