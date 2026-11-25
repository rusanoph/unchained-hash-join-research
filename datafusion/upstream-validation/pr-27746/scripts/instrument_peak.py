"""Generate a diagnostic worker with upstream PeakRecordingPool instrumentation."""

from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding="utf-8")


def replace_once(old, new):
    global source
    assert source.count(old) == 1, old
    source = source.replace(old, new)


replace_once(
    "use datafusion::execution::runtime_env::RuntimeEnvBuilder;",
    """use datafusion::execution::memory_pool::{
    GreedyMemoryPool, MemoryPool, PeakRecordingPool, UnboundedMemoryPool,
};
use datafusion::execution::runtime_env::RuntimeEnvBuilder;""",
)
start = source.index('    let ctx = match arg(&args, "--memory-limit-mb")')
end = source.index("\n\n    let t_load", start)
source = source[:start] + """    let inner_pool: Arc<dyn MemoryPool> = match arg(&args, "--memory-limit-mb")
        .map(|value| value.parse::<usize>().unwrap())
    {
        Some(mb) => Arc::new(GreedyMemoryPool::new(mb << 20)),
        None => Arc::new(UnboundedMemoryPool::default()),
    };
    let recorder = Arc::new(PeakRecordingPool::new(inner_pool));
    let runtime = RuntimeEnvBuilder::new()
        .with_memory_pool(recorder.clone())
        .build_arc()?;
    let ctx = SessionContext::new_with_config_rt(cfg, runtime);""" + source[end:]
replace_once("        let t = Instant::now();", "        recorder.reset_peak();\n        let t = Instant::now();")
replace_once("            samples.push(s);", '            s["pool_peak_bytes"] = json!(recorder.peak_reserved());\n            samples.push(s);')
print(source, end="")
