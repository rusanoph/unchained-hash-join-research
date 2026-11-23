#include "duckdb.hpp"
#include "duckdb/execution/hashman_state.hpp"
#include <nlohmann/json.hpp>
#include <chrono>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <thread>
#include <sys/resource.h>
#include <unistd.h>

using namespace duckdb;
using Json = nlohmann::json;
static string read_file(const string &path) {
    std::ifstream f(path);
    if (!f) throw std::runtime_error("Cannot read "+path);
    return string(std::istreambuf_iterator<char>(f), std::istreambuf_iterator<char>());
}
static string sql_quote(const string &s) {
    string out="'";
    for(char c:s) { if(c=='\'') out+='\''; out+=c; }
    return out+"'";
}
static void checked(Connection &con, const string &sql) {
    auto r=con.Query(sql);
    for (QueryResult *p=r.get(); p; p=p->next.get()) if (p->HasError()) throw std::runtime_error(p->GetError());
}
static uint64_t ns(std::chrono::steady_clock::time_point start) {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now()-start).count();
}
static Json telemetry(const shared_ptr<HashmanQueryState> &state) {
    Json operators=Json::array(),plan=Json::array(),runtime_parameters=Json::object();
    if (!state) return {{"operators",operators},{"physical_plan",plan}};
    for (auto &node:state->plan) {
        Json parameters=Json::object();
        for (auto &entry:node.parameters) parameters[entry.first]=entry.second;
        for (auto &entry:node.runtime_parameters) runtime_parameters[node.id][entry.first]=entry.second;
        plan.push_back({{"node_id",node.id},{"name",node.type},{"children",node.children},
                        {"types",node.types},{"parameters",parameters}});
    }
    for (auto &entry:state->records) {
        auto &r=*entry;
        operators.push_back({{"operator_id",r.node_id},{"actual_impl",r.actual},{"reason",r.reason},
            {"probe_kernel",r.probe_kernel},
            {"lookup_kernel",r.lookup_kernel},{"host_filters",r.host_filters},{"host_bloom",r.host_bloom},
            {"native_perfect_hash",r.native_perfect_hash},
            {"directory_format",r.directory_format},{"staging_released",r.staging_released},
            {"native_first_attempted",r.native_first_attempted},
            {"exact_ranges",r.exact_ranges},{"exact_lookup",r.exact_lookup},{"refine_prefetch",r.refine_prefetch},{"partitioned_build",r.partitioned_build},{"partition_bits",r.partition_bits},{"key_order_pairs",r.key_order_pairs},{"key_order_ascending",r.key_order_ascending},{"variable_width",r.variable_width},{"range_duplicate_keys",r.exact_ranges ? Json(r.range_duplicate_keys) : Json(nullptr)},
            {"staging_before_release_bytes",r.staging_before_release_bytes},
            {"build_rows",r.build_rows},{"slots",r.slots},{"stride_bytes",r.stride},
            {"final_allocation_bytes",r.allocation_bytes},{"matches",r.matches},
            {"finalize_ns",r.finalize_ns},{"bloom",r.bloom},{"partitions",1},
            {"source_retention",r.staging_released ? "released_after_filters" : "query_lifetime"}});
        if (r.census) {
            operators.back()["census"]={{"build_rows",r.census_rows},{"distinct_hashes",r.census_distinct},{"max_duplicates",r.census_max_dup},
                {"rows_in_keys_ge8",r.census_rows_ge8},{"rows_in_keys_ge16",r.census_rows_ge16},{"census_ns",r.census_ns},
                {"entry_size",r.census_entry_size},{"conditions",r.census_conditions},{"equality_predicates",r.census_equality},
                {"b_support_reason",r.census_support},{"join_type",r.census_join_type},{"key_types",r.census_key_types},
                {"layout_types",r.census_payload_types}};
        }
        if (r.profiled) {
            auto &op=operators.back();
            op["profile"]={{"global_init_ns",r.global_init_ns},{"local_init_ns",r.local_init_ns},
                {"key_clustering_ns_nested",r.key_clustering_ns},{"clustering_key_comparisons",r.clustering_key_checks},
                {"exact_range_key_comparisons",r.exact_range_key_checks},{"prefix_candidate_rows_potential",r.prefix_candidate_rows_potential},
                {"sink_ns",r.sink_ns},{"combine_ns",r.combine_ns},{"prepare_ns",r.prepare_ns},
                {"physical_finalize_ns",r.physical_finalize_ns},{"native_init_ns",r.native_init_ns},
                {"native_finalize_ns",r.native_finalize_ns},{"pointer_alloc_ns_nested",r.pointer_alloc_ns},
                {"operator_init_ns",r.operator_init_ns},{"probe_ns",r.probe_ns},
                {"unchained_core_ns_nested",r.core_ns},{"count_ns_nested",r.count_ns},{"scatter_ns_nested",r.scatter_ns},
                {"build_active_ns",r.global_init_ns+r.local_init_ns+r.sink_ns+r.combine_ns+r.prepare_ns+
                    r.physical_finalize_ns+r.native_init_ns+r.native_finalize_ns},
                {"probe_active_ns",r.operator_init_ns+r.probe_ns},
                {"lookup_ns_nested",r.lookup_ns},{"refine_ns_nested",r.refine_ns},{"emit_ns_nested",r.emit_ns},
                {"native_directory_key_checks",r.actual=="native" ? Json(r.native_key_checks) : Json(nullptr)},
                {"native_duplicate_chains",r.actual=="native" ? Json(r.native_duplicate_chains) : Json(nullptr)},
                {"resolve_rounds",r.resolve_rounds},{"advance_rounds",r.advance_rounds},
                {"compaction_rows",r.compaction_rows},{"gather_calls",r.gather_calls},
                {"probe_input_rows",r.probe_input_rows},{"probe_nonnull_rows",r.probe_nonnull_rows},
                {"output_rows",r.output_rows},{"probe_calls",r.probe_calls},
                {"range_candidate_checks",r.actual=="unchained_offsets_p1" ? Json(r.candidate_checks) : Json(nullptr)},
                {"bloom_reject_rows",r.bloom_reject_rows},{"empty_bucket_rows",r.empty_bucket_rows},
                {"retained_staging_bytes",r.staging_bytes},{"scratch_bytes",r.scratch_bytes},
                {"native_directory_bytes",r.native_directory_bytes},
                {"observed_engine_memory_max_bytes",r.observed_engine_memory_max}};
        }
    }
    return {{"operators",operators},{"physical_plan",plan},{"runtime_plan_parameters",runtime_parameters}};
}
// Only enabled for lifecycle validation. Interrupt from another host thread after
// actual treatment publication, rather than killing the worker process.
struct ReadyInterrupt {
    std::atomic<bool> stop{false}, fired{false};
    std::thread thread;
    ReadyInterrupt(Connection &con, HashmanQueryState &state) : thread([&,this] {
        while (!stop.load()) {
            if (state.unchained_ready.load()) { fired=true; con.Interrupt(); return; }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
    }) {}
    ~ReadyInterrupt() { stop=true; thread.join(); }
};
struct MemoryProfile {
    BufferManager &manager;
    std::atomic<bool> stop{false};
    std::thread thread;
    uint64_t engine_peak=0,rss_peak=0,samples=0;
    explicit MemoryProfile(ClientContext &context):manager(BufferManager::GetBufferManager(context)) {
        sample();thread=std::thread([this]{
            while(!stop.load()) {sample();std::this_thread::sleep_for(std::chrono::milliseconds(1));}
        });
    }
    void sample() {
        engine_peak=std::max<uint64_t>(engine_peak,manager.GetUsedMemory());
        uint64_t pages=0,resident=0;std::ifstream f("/proc/self/statm");f>>pages>>resident;
        rss_peak=std::max<uint64_t>(rss_peak,resident*sysconf(_SC_PAGESIZE));++samples;
    }
    void finish(){stop=true;if(thread.joinable())thread.join();sample();}
    ~MemoryProfile(){if(thread.joinable())finish();}
    Json json() const {
        struct rusage usage;getrusage(RUSAGE_SELF,&usage);
        return {{"engine_used_sampled_peak_bytes",engine_peak},{"rss_sampled_peak_bytes",rss_peak},
                {"process_lifetime_peak_rss_bytes",uint64_t(usage.ru_maxrss)*1024},
                {"samples",samples},{"sample_interval_ms",1},{"sampled_peak_is_lower_bound",true}};
    }
};
int main(int argc, char **argv) {
    shared_ptr<HashmanQueryState> state;
    uint64_t emitted_rows=0;
    try {
        std::map<string,string> options;
        for (int i=1;i<argc;i+=2) {
            if (i+1==argc || string(argv[i]).substr(0,2)!="--") throw std::runtime_error("Expected --name value");
            options[string(argv[i]).substr(2)]=argv[i+1];
        }
        auto get=[&](const string &k,const string &fallback) { auto it=options.find(k);return it==options.end()?fallback:it->second; };
        DBConfig config;
        if(get("read-only","false")=="true") config.options.access_mode=AccessMode::READ_ONLY;
        config.AddExtensionOption("hashman_join_mode","Experimental join implementation",LogicalType::VARCHAR,Value("native"));
        config.AddExtensionOption("hashman_bloom","Enable per-slot Bloom",LogicalType::BOOLEAN,Value(true));
        config.AddExtensionOption("hashman_controlled","Disable perfect hash in both arms",LogicalType::BOOLEAN,Value(false));
        config.AddExtensionOption("hashman_test_hash","Validation-only hash transform",LogicalType::VARCHAR,Value("none"));
        config.AddExtensionOption("hashman_test_fault","Validation-only failure injection",LogicalType::VARCHAR,Value("none"));
        config.AddExtensionOption("hashman_profile","Diagnostic serial phase/candidate profiling",LogicalType::BOOLEAN,Value(false));
        config.AddExtensionOption("hashman_directory_adjust","Directory log2 slots adjustment",LogicalType::BIGINT,Value(int64_t(0)));
        config.AddExtensionOption("hashman_probe_kernel","Experimental probe implementation",LogicalType::VARCHAR,Value("legacy"));
        config.AddExtensionOption("hashman_lookup_kernel","Experimental directory lookup implementation",LogicalType::VARCHAR,Value("legacy"));
        config.AddExtensionOption("hashman_host_filters","Support host runtime filters for Unchained",LogicalType::BOOLEAN,Value(false));
        config.AddExtensionOption("hashman_directory_format","Offsets64 or packed48 directory",LogicalType::VARCHAR,Value("offsets64"));
        config.AddExtensionOption("hashman_release_staging","Release fixed rows after filter publication",LogicalType::BOOLEAN,Value(false));
        config.AddExtensionOption("hashman_native_first","Preserve native perfect hash in prefer mode",LogicalType::BOOLEAN,Value(false));
        config.AddExtensionOption("hashman_exact_ranges","Cluster full keys inside prefix ranges",LogicalType::BOOLEAN,Value(false));
        config.AddExtensionOption("hashman_census","Measurement only: record the build-side shape of every hash join",LogicalType::BOOLEAN,Value(false));
        config.AddExtensionOption("hashman_refine_prefetch","Software prefetch for the exact refine pass: none|lookup|ahead|both",LogicalType::VARCHAR,Value("none"));
        config.AddExtensionOption("hashman_partitioned_build","Build by the sink's radix partitions with the slot window under the salt: off|aligned|auto",LogicalType::VARCHAR,Value("off"));
        config.AddExtensionOption("hashman_min_build_bytes","Builds below this many staging bytes stay on the native join (0 = off)",LogicalType::BIGINT,Value::BIGINT(0));
        config.AddExtensionOption("hashman_radix_bits","Radix bits of the build sink with the aligned partitioned build (0 = DuckDB default 4)",LogicalType::BIGINT,Value::BIGINT(0));
        config.AddExtensionOption("hashman_exact_lookup","Exact range refinement: separate pass or fused into lookup",LogicalType::VARCHAR,Value("separate"));
        const string database=get("database",":memory:");
        DuckDB db(database.c_str(),&config); Connection con(db);
        checked(con,"LOAD tpch");
        con.Query("LOAD tpcds"); // present only in builds that include it; a failure here is not an error
        // Register before QueryBegin so all executions reset query-local evidence.
        state=GetHashmanState(*con.context);
        const auto mode=get("mode","native");
        if (mode!="native" && mode!="unchained_required" && mode!="unchained_prefer") throw std::runtime_error("Invalid mode");
        auto threads=std::stoul(get("threads","1"));
        if (!threads || threads>256) throw std::runtime_error("Invalid thread count");
        const bool profile=get("profile","false")=="true";
        const int directory_adjust=std::stoi(get("directory-adjust","0"));
        if (directory_adjust < -1 || directory_adjust > 1 || (profile && threads!=1))
            throw std::runtime_error("Profile requires serial execution and directory-adjust in [-1,1]");
        checked(con,"SET threads="+std::to_string(threads));
        checked(con,"SET memory_limit="+sql_quote(get("memory-limit","1GB")));
        checked(con,"SET hashman_join_mode="+sql_quote(mode));
        auto bloom=get("bloom","true");
        if(bloom!="true" && bloom!="false") throw std::runtime_error("Invalid Bloom flag");
        checked(con,"SET hashman_bloom="+bloom);
        checked(con,string("SET hashman_profile=")+(profile?"true":"false"));
        checked(con,"SET hashman_directory_adjust="+std::to_string(directory_adjust));
        const auto probe_kernel=get("probe-kernel","legacy");
        if(probe_kernel!="legacy" && probe_kernel!="typed" && probe_kernel!="fused" && probe_kernel!="interleaved")
            throw std::runtime_error("Invalid probe kernel");
        checked(con,"SET hashman_probe_kernel="+sql_quote(probe_kernel));
        const auto lookup_kernel=get("lookup-kernel","legacy");
        if(lookup_kernel!="legacy" && lookup_kernel!="cached" && lookup_kernel!="tags")
            throw std::runtime_error("Invalid lookup kernel");
        checked(con,"SET hashman_lookup_kernel="+sql_quote(lookup_kernel));
        const auto host_filters=get("host-filters","false");
        if(host_filters!="true" && host_filters!="false") throw std::runtime_error("Invalid host filters flag");
        checked(con,"SET hashman_host_filters="+host_filters);
        const auto directory_format=get("directory-format","offsets64");
        const auto release_staging=get("release-staging","false");
        if(directory_format!="offsets64" && directory_format!="packed48") throw std::runtime_error("Invalid directory format");
        if(release_staging!="true" && release_staging!="false") throw std::runtime_error("Invalid release staging flag");
        checked(con,"SET hashman_directory_format="+sql_quote(directory_format));
        checked(con,"SET hashman_release_staging="+release_staging);
        const auto native_first=get("native-first","false");
        if(native_first!="true" && native_first!="false") throw std::runtime_error("Invalid native first flag");
        checked(con,"SET hashman_native_first="+native_first);
        const auto exact_ranges=get("exact-ranges","false");
        if(exact_ranges!="true" && exact_ranges!="false")throw std::runtime_error("Invalid exact ranges flag");
        checked(con,"SET hashman_exact_ranges="+exact_ranges);
        const auto exact_lookup=get("exact-lookup","separate");
        if(exact_lookup!="separate" && exact_lookup!="fused" && exact_lookup!="separate_nodiv" &&
           exact_lookup!="separate_inline" && exact_lookup!="separate_nodiv_inline")throw std::runtime_error("Invalid exact lookup");
        if(exact_lookup!="separate" && exact_ranges!="true")throw std::runtime_error("Exact lookup variants require exact ranges");
        if(exact_lookup=="fused" && lookup_kernel=="legacy")
            throw std::runtime_error("Fused exact lookup requires cached/tags lookup");
        checked(con,"SET hashman_exact_lookup="+sql_quote(exact_lookup));
        const auto census=get("census","false");
        if(census!="true" && census!="false") throw std::runtime_error("Invalid census flag");
        checked(con,"SET hashman_census="+census);
        const auto refine_prefetch=get("refine-prefetch","none");
        if(refine_prefetch!="none" && refine_prefetch!="lookup" && refine_prefetch!="ahead" && refine_prefetch!="both")
            throw std::runtime_error("Invalid refine prefetch");
        if(refine_prefetch!="none" && (exact_ranges!="true" || exact_lookup=="fused"))
            throw std::runtime_error("Refine prefetch requires exact ranges and a separate refine pass");
        if((refine_prefetch=="lookup" || refine_prefetch=="both") && lookup_kernel=="legacy")
            throw std::runtime_error("Lookup prefetch requires cached/tags lookup");
        checked(con,"SET hashman_refine_prefetch="+sql_quote(refine_prefetch));
        const auto partitioned_build=get("partitioned-build","off");
        if(partitioned_build!="off" && partitioned_build!="aligned" && partitioned_build!="auto") throw std::runtime_error("Invalid partitioned build");
        if(partitioned_build!="off" && directory_format!="packed48") throw std::runtime_error("Partitioned build requires the packed48 directory");
        checked(con,"SET hashman_partitioned_build="+sql_quote(partitioned_build));
        const auto radix_bits_text=get("radix-bits","0");
        int radix_bits=0;
        try { radix_bits=std::stoi(radix_bits_text); } catch(...) { throw std::runtime_error("Invalid radix bits"); }
        if(radix_bits<0 || radix_bits>12 || (radix_bits>0 && partitioned_build=="off")) throw std::runtime_error("Radix bits need a partitioned build (aligned or auto) and must be within 0..12");
        checked(con,"SET hashman_radix_bits="+std::to_string(radix_bits));
        const auto min_build_bytes_text=get("min-build-bytes","0");
        long long min_build_bytes=0;
        try { min_build_bytes=std::stoll(min_build_bytes_text); } catch(...) { throw std::runtime_error("Invalid min build bytes"); }
        if(min_build_bytes<0) throw std::runtime_error("Min build bytes must not be negative");
        checked(con,"SET hashman_min_build_bytes="+std::to_string(min_build_bytes));
        const auto controlled_value=get("controlled","true");
        if(controlled_value!="true" && controlled_value!="false") throw std::runtime_error("Invalid controlled flag");
        const bool controlled=controlled_value=="true";
        if (controlled) {
            checked(con,"SET hashman_controlled=true");
            checked(con,"SET disabled_optimizers='join_filter_pushdown'");
        }
        if (options.count("setup")) checked(con,read_file(options["setup"]));
        const auto test_hash=get("test-hash","none"),test_fault=get("test-fault","none");
        if (test_hash!="none" && test_hash!="constant" && test_hash!="bucket" && test_hash!="tag")
            throw std::runtime_error("Invalid test hash");
        if (test_fault!="none" && test_fault!="allocation" && test_fault!="budget")
            throw std::runtime_error("Invalid test fault");
        checked(con,"SET hashman_test_hash="+sql_quote(test_hash));
        checked(con,"SET hashman_test_fault="+sql_quote(test_fault));
        const string sql=options.count("query")?read_file(options["query"]):"SELECT 1";
        if (options.count("plan")) {
            auto plan=con.Query("EXPLAIN (FORMAT JSON) "+sql);
            if (plan->HasError()) throw std::runtime_error(plan->GetError());
            std::ofstream f(options["plan"]);
            for(idx_t i=0;i<plan->RowCount();++i) f << plan->GetValue(1,i).ToString() << '\n';
        }
        const unsigned warmups=std::stoul(get("warmups","2"));
        const unsigned repetitions=std::stoul(get("repetitions","1"));
        if (warmups>10000 || !repetitions || repetitions>10000) throw std::runtime_error("Invalid repeats");
        const bool prepared=get("prepared","false")=="true";
        const bool early_close=get("early-close-first","false")=="true";
        const bool cancel=get("cancel-after-ready","false")=="true";
        if ((early_close || cancel) && (warmups || repetitions<2))
            throw std::runtime_error("Lifecycle checks require zero warmups and at least two repetitions");
        auto statement=prepared ? con.Prepare(sql) : nullptr;
        if (statement && statement->HasError()) throw std::runtime_error(statement->GetError());
        Json observations=Json::array();
        for (unsigned iteration=0;iteration<warmups+repetitions;++iteration) {
            const bool measured=iteration>=warmups;
            emitted_rows=0;
            std::ofstream output;
            if (measured && options.count("result")) {
                auto path=options["result"];
                if (repetitions!=1) path+="."+std::to_string(iteration-warmups);
                output.open(path);
                if (!output) throw std::runtime_error("Cannot create result file");
            }
            unique_ptr<ReadyInterrupt> interrupter;
            if (cancel && iteration==0) {
                state->unchained_ready=false;
                interrupter=make_uniq<ReadyInterrupt>(con,*state);
            }
            unique_ptr<MemoryProfile> memory_profile;
            if (profile) memory_profile=make_uniq<MemoryProfile>(*con.context);
            const auto started=std::chrono::steady_clock::now();
            unique_ptr<QueryResult> result;
            uint64_t count=0, checksum=0;
            bool complete=true;
            try {
            vector<Value> values;
            result=statement ? statement->Execute(values,true) : con.SendQuery(sql);
            if (result->HasError()) throw std::runtime_error(result->GetError());
            if (mode=="unchained_required") {
                bool has_join=false;
                for (auto &node:state->plan) if (node.type=="HASH_JOIN") has_join=true;
                if (!has_join) throw std::runtime_error("Required query has no physical HASH_JOIN");
            }
            while (auto chunk=result->Fetch()) {
                for(idx_t row=0;row<chunk->size();++row) {
                    Json exported;
                    for(idx_t col=0;col<chunk->ColumnCount();++col) {
                        auto value=chunk->GetValue(col,row);
                        checksum += value.Hash();
                        if (output.is_open()) {
                            if(value.IsNull()) exported.push_back(nullptr);
                            else exported.push_back(value.ToString());
                        }
                    }
                    if (output.is_open()) output << exported.dump() << '\n';
                }
                count += chunk->size();
                emitted_rows=count;
                if (early_close && iteration==0) { complete=false; break; }
            }
            if (result->HasError()) throw std::runtime_error(result->GetError());
            } catch (const std::exception &e) {
                if (!interrupter || !interrupter->fired.load()) throw;
                auto sample=telemetry(state);
                sample["status"]="cancelled"; sample["error"]=e.what();
                sample["result_rows"]=count;
                observations.push_back(sample);
                result.reset(); interrupter.reset();
                continue;
            }
            if (interrupter) throw std::runtime_error("Cancellation check completed without interruption");
            const auto elapsed=ns(started);
            if (memory_profile) memory_profile->finish();
            if (!measured) continue;
            size_t executed=0;
            for(auto &r:state->records) if (r->actual=="unchained_offsets_p1") ++executed;
            if(mode=="unchained_required" && !executed) throw std::runtime_error("Required Unchained was not executed");
            Json types=Json::array();
            for(auto &type:result->types) types.push_back(type.ToString());
            auto sample=telemetry(state);
            if (memory_profile) sample["memory_profile"]=memory_profile->json();
            sample.update({{"statement_wall_ns",elapsed},{"result_rows",count},{"sink_checksum",checksum},
                {"sink",output.is_open()?"typed_text_export":"value_hash_drain"},
                {"executed_unchained_joins",executed},{"types",types},{"drain_complete",complete}});
            observations.push_back(sample);
            // Destruction also closes an intentionally unconsumed streaming result.
            result.reset();
        }
        std::cout << Json({{"schema_version",17},{"engine","duckdb"},
            {"engine_commit","069cc9f9b5be802405797faecc284961b07c70ef"},
            {"engine_version",DuckDB::LibraryVersion()},{"mode",mode},
            {"requested_threads",threads},{"prepared",prepared},
            {"profile",profile},{"bloom",bloom=="true"},{"directory_adjust",directory_adjust},
            {"probe_kernel",probe_kernel},{"controlled",controlled},
            {"lookup_kernel",lookup_kernel},{"host_filters",host_filters=="true"},
            {"directory_format",directory_format},{"release_staging",release_staging=="true"},
            {"native_first",native_first=="true"},
            {"exact_ranges",exact_ranges=="true"},{"exact_lookup",exact_lookup},{"refine_prefetch",refine_prefetch},{"partitioned_build",partitioned_build},{"radix_bits",radix_bits},{"min_build_bytes",min_build_bytes},{"census",census=="true"},
            {"test_hash",test_hash},{"test_fault",test_fault},{"samples",observations}}).dump() << '\n';
        return 0;
    } catch(const std::exception &e) {
        auto failure=telemetry(state);
        failure.update({{"status","failed"},{"error",e.what()},{"result_rows_emitted",emitted_rows}});
        std::cerr << failure.dump() << '\n';
        return 1;
    }
}
