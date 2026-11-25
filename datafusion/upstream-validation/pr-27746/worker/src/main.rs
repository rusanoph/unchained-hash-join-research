// hashman-df-worker (schema 1): runs one SQL statement on in-memory tables and prints one JSON record.
// One process = one observation. Tables come from Parquet files (loaded into MemTables) or a seeded synthetic
// generator. The same source builds against the pristine and the patched DataFusion tree; telemetry and the
// effective-setting echo exist only with `--features hashman` (patched tree).
use datafusion::arrow::array::RecordBatch;
use datafusion::arrow::datatypes::Schema;
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

const WORKER_SCHEMA: u32 = 2;

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1).cloned())
}
fn flag(args: &[String], name: &str, default: bool) -> bool {
    arg(args, name).map(|s| s == "true").unwrap_or(default)
}

fn fnv(bytes: &[u8]) -> u64 {
    let mut h = 0xcbf29ce484222325u64;
    for &b in bytes {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h
}

/// Order-independent multiset fingerprint, including duplicate rows and typed NULLs.
struct Digest {
    rows: u64,
    xor: u64,
    sum: u64,
}
impl Digest {
    fn add(&mut self, batch: &RecordBatch, dump: &mut Option<std::io::BufWriter<std::fs::File>>) {
        let opts = FormatOptions::default();
        let formatters: Vec<_> = batch.columns().iter()
            .map(|column| ArrayFormatter::try_new(column.as_ref(), &opts).unwrap())
            .collect();
        for row in 0..batch.num_rows() {
            let cells: Vec<Option<String>> = batch.columns().iter().zip(&formatters)
                .map(|(column, formatter)| {
                    (!column.is_null(row)).then(|| formatter.value(row).to_string())
                })
                .collect();
            let encoded = serde_json::to_vec(&cells).unwrap();
            let fingerprint = fnv(&encoded);
            self.rows += 1;
            self.xor ^= fingerprint;
            self.sum = self.sum.wrapping_add(fingerprint);
            if let Some(writer) = dump.as_mut() {
                writer.write_all(&encoded).unwrap();
                writer.write_all(b"\n").unwrap();
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

#[tokio::main(flavor = "current_thread")]
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
    if let Some(data) = arg(&args, "--data") {
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
        let t = Instant::now();
        let df = ctx.sql(&sql).await?;
        let plan = df.create_physical_plan().await?;
        let plan_ns = t.elapsed().as_nanos() as u64;
        let schema: Vec<_> = plan.schema().fields().iter()
            .map(|field| json!([field.name(), field.data_type().to_string()]))
            .collect();
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
            if plan_text.is_empty() {
                plan_text = displayable(plan.as_ref()).indent(false).to_string();
            }
            #[allow(unused_mut)]
            let mut s = json!({"statement_wall_ns": wall, "plan_ns": plan_ns, "schema": schema, "rows": digest.rows,
                               "digest": format!("{:016x}-{:016x}", digest.xor, digest.sum)});
            if want_plan_metrics {
                use datafusion::physical_plan::display::DisplayableExecutionPlan;
                s["plan_with_metrics"] = Value::String(DisplayableExecutionPlan::with_metrics(plan.as_ref()).indent(false).to_string());
            }
            samples.push(s);
        }
    }

    let effective = json!({"target_partitions": parts, "batch_size": batch_size, "set": set_echo});
    let out = json!({
        "schema_version": WORKER_SCHEMA,
        "datafusion_version": datafusion::DATAFUSION_VERSION,
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
