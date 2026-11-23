// hashman-df-worker (schema 1): runs one SQL statement on in-memory tables and prints one JSON record.
// One process = one observation. Tables come from Parquet files (loaded into MemTables) or a seeded synthetic
// generator. The same source builds against the pristine and the patched DataFusion tree; telemetry and the
// effective-setting echo exist only with `--features hashman` (patched tree).
use datafusion::arrow::array::{ArrayRef, Int32Array, Int64Array, RecordBatch, StringArray};
use datafusion::arrow::datatypes::{DataType, Field, Schema};
use datafusion::arrow::util::display::{ArrayFormatter, FormatOptions};
use datafusion::datasource::MemTable;
use datafusion::execution::runtime_env::RuntimeEnvBuilder;
use datafusion::physical_plan::{displayable, execute_stream};
use datafusion::prelude::*;
use futures::StreamExt;
use serde_json::{json, Value};
use std::io::Write;
use std::sync::Arc;
use std::time::Instant;

const WORKER_SCHEMA: u32 = 1;

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1).cloned())
}
fn flag(args: &[String], name: &str, default: bool) -> bool {
    arg(args, name).map(|s| s == "true").unwrap_or(default)
}

struct Rng(u64);
impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut x = self.0;
        x = (x ^ (x >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        x = (x ^ (x >> 27)).wrapping_mul(0x94D049BB133111EB);
        x ^ (x >> 31)
    }
    fn below(&mut self, n: u64) -> u64 {
        ((self.next() as u128 * n as u128) >> 64) as u64
    }
}

/// Seeded synthetic tables `b(k, p1, p2)` (build candidate) and `p(k, v)` (probe candidate).
fn synthetic(spec: &Value, batch_rows: usize) -> (Vec<RecordBatch>, Vec<RecordBatch>, Value) {
    let n = spec["build_rows"].as_u64().unwrap() as usize;
    let mult = spec["mult"].as_u64().unwrap_or(1).max(1) as usize;
    let probe_rows = spec["probe_rows"].as_u64().unwrap_or(n as u64) as usize;
    let order = spec["order"].as_str().unwrap_or("shuffled").to_string();
    let seed = spec["seed"].as_u64().unwrap_or(1);
    let key_type = spec["key_type"].as_str().unwrap_or("int64").to_string();
    let stride = spec["key_stride"].as_u64().unwrap_or(1);
    let hit_rate = spec["hit_rate"].as_f64().unwrap_or(1.0);
    let distinct = (n / mult).max(1);
    let mut rng = Rng(seed);
    let mut keys: Vec<u64> = (0..n).map(|i| (i / mult).min(distinct - 1) as u64).collect();
    match order.as_str() {
        "shuffled" => {
            for i in (1..n).rev() {
                let j = rng.below(i as u64 + 1) as usize;
                keys.swap(i, j);
            }
        }
        "sorted" => {}
        other => panic!("unknown order {other}"),
    }
    let probe_keys: Vec<u64> = (0..probe_rows)
        .map(|_| {
            if hit_rate >= 1.0 || (rng.next() >> 11) as f64 / (1u64 << 53) as f64 <= hit_rate {
                rng.below(distinct as u64)
            } else {
                distinct as u64 + rng.below(distinct as u64)
            }
        })
        .collect();
    let key_array = |ks: &[u64]| -> ArrayRef {
        match key_type.as_str() {
            "int64" => Arc::new(Int64Array::from_iter_values(ks.iter().map(|&k| (k * stride) as i64))),
            "int32" => Arc::new(Int32Array::from_iter_values(ks.iter().map(|&k| (k * stride) as i32))),
            "utf8" => Arc::new(StringArray::from_iter_values(ks.iter().map(|&k| format!("key-{:012}", k * stride)))),
            other => panic!("unknown key_type {other}"),
        }
    };
    let kt = match key_type.as_str() {
        "int64" => DataType::Int64,
        "int32" => DataType::Int32,
        _ => DataType::Utf8,
    };
    let b_schema = Arc::new(Schema::new(vec![
        Field::new("k", kt.clone(), false),
        Field::new("p1", DataType::Int64, false),
        Field::new("p2", DataType::Int64, false),
    ]));
    let p_schema = Arc::new(Schema::new(vec![Field::new("k", kt, false), Field::new("v", DataType::Int64, false)]));
    let mut b = vec![];
    for start in (0..n).step_by(batch_rows) {
        let end = (start + batch_rows).min(n);
        b.push(
            RecordBatch::try_new(
                b_schema.clone(),
                vec![
                    key_array(&keys[start..end]),
                    Arc::new(Int64Array::from_iter_values((start..end).map(|i| i as i64))),
                    Arc::new(Int64Array::from_iter_values((start..end).map(|i| (i as i64) * 3))),
                ],
            )
            .unwrap(),
        );
    }
    let mut p = vec![];
    for start in (0..probe_rows).step_by(batch_rows) {
        let end = (start + batch_rows).min(probe_rows);
        p.push(
            RecordBatch::try_new(
                p_schema.clone(),
                vec![key_array(&probe_keys[start..end]), Arc::new(Int64Array::from_iter_values((start..end).map(|i| i as i64)))],
            )
            .unwrap(),
        );
    }
    (b, p, json!({"build_rows": n, "mult": mult, "distinct": distinct, "probe_rows": probe_rows, "order": order,
                  "seed": seed, "key_type": key_type, "key_stride": stride, "hit_rate": hit_rate}))
}

fn fnv(bytes: &[u8]) -> u64 {
    let mut h = 0xcbf29ce484222325u64;
    for &b in bytes {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/// Order-independent digest of a result: (count, xor of row hashes, wrapping sum of row hashes).
/// Floating point cells are rounded to 6 significant digits so reordered summation does not flip the digest.
struct Digest {
    rows: u64,
    xor: u64,
    sum: u64,
}
impl Digest {
    fn add(&mut self, batch: &RecordBatch, dump: &mut Option<std::io::BufWriter<std::fs::File>>) {
        let opts = FormatOptions::default().with_null("NULL");
        let fmts: Vec<_> = batch.columns().iter().map(|c| ArrayFormatter::try_new(c.as_ref(), &opts).unwrap()).collect();
        let mut line = String::new();
        for r in 0..batch.num_rows() {
            line.clear();
            for (ci, f) in fmts.iter().enumerate() {
                if ci > 0 {
                    line.push('\u{1f}');
                }
                let col = batch.column(ci);
                match col.data_type() {
                    DataType::Float32 | DataType::Float64 => {
                        let s = f.value(r).to_string();
                        match s.parse::<f64>() {
                            Ok(x) if x.is_finite() => line.push_str(&format!("{:.5e}", x)),
                            _ => line.push_str(&s),
                        }
                    }
                    _ => line.push_str(&f.value(r).to_string()),
                }
            }
            let h = fnv(line.as_bytes());
            self.rows += 1;
            self.xor ^= h;
            self.sum = self.sum.wrapping_add(h);
            if let Some(w) = dump.as_mut() {
                writeln!(w, "{}", line.replace('\u{1f}', "\t")).unwrap();
            }
        }
    }
}

async fn load_parquet(ctx: &SessionContext, data: &str, name: &str, parts: usize, words: Option<&std::collections::HashSet<&str>>) -> datafusion::error::Result<()> {
    let mut df = ctx.read_parquet(format!("{}/{}.parquet", data, name), ParquetReadOptions::default()).await?;
    if let Some(words) = words {
        // keep only columns whose name occurs as a word in the SQL text (column names are never rewritten by the
        // lab's queries); a table none of whose columns are named keeps its first column so that row counts survive
        let all: Vec<String> = df.schema().fields().iter().map(|f| f.name().clone()).collect();
        let mut keep: Vec<&str> = all.iter().map(|s| s.as_str()).filter(|c| words.contains(c.to_lowercase().as_str())).collect();
        if keep.is_empty() {
            keep.push(all[0].as_str());
        }
        if keep.len() < all.len() {
            df = df.select_columns(&keep)?;
        }
    }
    let schema = Arc::new(df.schema().as_arrow().clone());
    let batches = df.collect().await?;
    register_batches(ctx, name, schema, batches, parts)
}

fn register_batches(ctx: &SessionContext, name: &str, schema: Arc<Schema>, batches: Vec<RecordBatch>, parts: usize) -> datafusion::error::Result<()> {
    let parts = parts.max(1);
    let mut chunks: Vec<Vec<RecordBatch>> = vec![vec![]; parts];
    for (i, b) in batches.into_iter().enumerate() {
        chunks[i % parts].push(b);
    }
    ctx.register_table(name, Arc::new(MemTable::try_new(schema, chunks)?))?;
    Ok(())
}

#[tokio::main]
async fn main() -> datafusion::error::Result<()> {
    // A worker that dies (allocation beyond the campaign ceiling aborts the process) must not leave a core dump: WSL writes
    // one as large as the process into the Windows temp folder and a few of them filled the disk. execve resets the flag,
    // so it has to be set here, not by the parent.
    #[cfg(target_os = "linux")]
    unsafe {
        libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0);
    }
    let args: Vec<String> = std::env::args().collect();
    let sql = match (arg(&args, "--sql"), arg(&args, "--sql-file")) {
        (Some(s), _) => s,
        (None, Some(f)) => std::fs::read_to_string(f).expect("sql file"),
        _ => panic!("--sql or --sql-file required"),
    };
    let parts: usize = arg(&args, "--target-partitions").map(|s| s.parse().unwrap()).unwrap_or(1);
    let batch_size: usize = arg(&args, "--batch-size").map(|s| s.parse().unwrap()).unwrap_or(8192);
    let warmups: usize = arg(&args, "--warmups").map(|s| s.parse().unwrap()).unwrap_or(2);
    let reps: usize = arg(&args, "--repetitions").map(|s| s.parse().unwrap()).unwrap_or(1);
    let want_plan_metrics = flag(&args, "--explain", false);
    let mut cfg = SessionConfig::new().with_target_partitions(parts).with_batch_size(batch_size);
    let mut set_echo = vec![];
    for kv in args.windows(2).filter(|w| w[0] == "--set").map(|w| w[1].clone()) {
        let (k, v) = kv.split_once('=').expect("--set key=value");
        cfg.options_mut().set(k, v)?;
        set_echo.push(kv);
    }
    let ctx = match arg(&args, "--memory-limit-mb").map(|s| s.parse::<usize>().unwrap()) {
        Some(mb) => SessionContext::new_with_config_rt(cfg, RuntimeEnvBuilder::new().with_memory_limit(mb << 20, 1.0).build_arc()?),
        None => SessionContext::new_with_config(cfg),
    };

    let t_load = Instant::now();
    #[allow(unused_assignments)]
    let mut dataset = Value::Null;
    if let Some(spec) = arg(&args, "--synthetic") {
        let spec: Value = serde_json::from_str(&spec).expect("--synthetic JSON");
        let (b, p, echo) = synthetic(&spec, batch_size);
        let bs = b[0].schema();
        let ps = p[0].schema();
        register_batches(&ctx, "b", bs, b, parts)?;
        register_batches(&ctx, "p", ps, p, parts)?;
        dataset = echo;
    } else if let Some(data) = arg(&args, "--data") {
        let lower = sql.to_lowercase();
        let words: std::collections::HashSet<&str> = lower.split(|c: char| !(c.is_alphanumeric() || c == '_')).collect();
        let mut names: Vec<String> = std::fs::read_dir(&data)
            .unwrap()
            .filter_map(|e| e.unwrap().file_name().into_string().unwrap().strip_suffix(".parquet").map(|s| s.to_string()))
            .filter(|n| words.contains(n.to_lowercase().as_str()))
            .collect();
        names.sort();
        let prune = flag(&args, "--prune-columns", false);
        let direct = flag(&args, "--parquet-direct", false);
        for n in &names {
            if direct {
                // end-to-end mode: DataFusion scans the Parquet files itself (decode inside the measured statement)
                ctx.register_parquet(n.as_str(), format!("{}/{}.parquet", data, n), ParquetReadOptions::default()).await?;
            } else {
                load_parquet(&ctx, &data, n, parts, if prune { Some(&words) } else { None }).await?;
            }
        }
        dataset = json!({"data_dir": data, "tables": names});
    }
    if let Some(path) = arg(&args, "--setup-sql") {
        // deterministic table definitions (CREATE TABLE ... AS SELECT ...) separated by ';'
        let text = std::fs::read_to_string(&path).expect("setup sql");
        let mut n = 0;
        for stmt in text.split(';').map(|s| s.trim()).filter(|s| !s.is_empty()) {
            ctx.sql(stmt).await?.collect().await?;
            n += 1;
        }
        dataset = json!({"setup_sql": path, "statements": n});
    }
    let load_ns = t_load.elapsed().as_nanos() as u64;

    let mut dump = arg(&args, "--result-file").map(|p| std::io::BufWriter::new(std::fs::File::create(p).expect("result file")));
    let mut samples = vec![];
    let mut plan_text = String::new();
    for i in 0..warmups + reps {
        let measured = i >= warmups;
        #[cfg(feature = "hashman")]
        let _ = datafusion::physical_plan::joins::hashman::take_report();
        let t = Instant::now();
        let df = ctx.sql(&sql).await?;
        let plan = df.create_physical_plan().await?;
        let plan_ns = t.elapsed().as_nanos() as u64;
        if measured && plan_text.is_empty() {
            plan_text = displayable(plan.as_ref()).indent(false).to_string();
        }
        let mut stream = execute_stream(plan.clone(), ctx.task_ctx())?;
        let mut digest = Digest { rows: 0, xor: 0, sum: 0 };
        let last = i + 1 == warmups + reps;
        let mut d = if last { dump.take() } else { None };
        while let Some(b) = stream.next().await {
            digest.add(&b?, &mut d);
        }
        if let Some(mut w) = d {
            w.flush().unwrap();
        }
        let wall = t.elapsed().as_nanos() as u64;
        if measured {
            #[allow(unused_mut)]
            let mut s = json!({"statement_wall_ns": wall, "plan_ns": plan_ns, "rows": digest.rows,
                               "digest": format!("{:016x}-{:016x}", digest.xor, digest.sum)});
            #[cfg(feature = "hashman")]
            {
                s["hashman"] = serde_json::from_str(&datafusion::physical_plan::joins::hashman::take_report()).unwrap();
            }
            if want_plan_metrics {
                use datafusion::physical_plan::display::DisplayableExecutionPlan;
                s["plan_with_metrics"] = Value::String(DisplayableExecutionPlan::with_metrics(plan.as_ref()).indent(false).to_string());
            }
            samples.push(s);
        }
    }

    let mut effective = json!({"target_partitions": parts, "batch_size": batch_size, "set": set_echo});
    #[cfg(feature = "hashman")]
    {
        let st = ctx.state();
        let e = &st.config_options().execution;
        effective["hashman_join"] = json!(e.hashman_join);
        effective["hashman_join_min_build_rows"] = json!(e.hashman_join_min_build_rows);
        effective["hashman_join_min_multiplicity"] = json!(e.hashman_join_min_multiplicity);
        effective["hashman_join_profile"] = json!(e.hashman_join_profile);
        effective["hashman_join_max_locality"] = json!(e.hashman_join_max_locality);
        effective["hashman_join_reorder"] = json!(e.hashman_join_reorder);
        effective["hashman_join_prefetch"] = json!(e.hashman_join_prefetch);
        effective["perfect_hash_join_small_build_threshold"] = json!(e.perfect_hash_join_small_build_threshold);
        effective["perfect_hash_join_min_key_density"] = json!(e.perfect_hash_join_min_key_density);
    }
    let out = json!({
        "schema_version": WORKER_SCHEMA,
        "datafusion_version": datafusion::DATAFUSION_VERSION,
        "hashman_compiled": cfg!(feature = "hashman"),
        "effective": effective,
        "dataset": dataset,
        "load_ns": load_ns,
        "warmups": warmups,
        "physical_plan": plan_text,
        "samples": samples,
    });
    println!("{}", out);
    Ok(())
}
