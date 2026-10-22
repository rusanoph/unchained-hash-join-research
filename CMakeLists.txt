cmake_minimum_required(VERSION 3.16)
project(unchained_join LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(UNCHAINED_SANITIZERS "Build the tests with AddressSanitizer and UndefinedBehaviorSanitizer" ON)
option(UNCHAINED_BENCHMARKS "Build the build-kernel micro-benchmark" ON)

# Header-only core library: no dependency on DuckDB.
add_library(unchained INTERFACE)
add_library(unchained::unchained ALIAS unchained)
target_include_directories(unchained INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/core/include)

enable_testing()

function(unchained_test name source)
  add_executable(${name} ${source})
  target_link_libraries(${name} PRIVATE unchained::unchained)
  target_compile_options(${name} PRIVATE -Wall -Wextra -Wpedantic)
  if(UNCHAINED_SANITIZERS)
    target_compile_options(${name} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(${name} PRIVATE -fsanitize=address,undefined)
  endif()
  add_test(NAME ${name} COMMAND ${name})
endfunction()

unchained_test(test_table core/tests/test_table.cpp)
unchained_test(test_key_ranges core/tests/test_key_ranges.cpp)
unchained_test(test_partitioned_build core/tests/test_partitioned_build.cpp)

if(UNCHAINED_BENCHMARKS)
  add_executable(bench_build_partitioning core/tests/bench_build_partitioning.cpp)
  target_link_libraries(bench_build_partitioning PRIVATE unchained::unchained)
  target_compile_options(bench_build_partitioning PRIVATE -O2 -march=native)
endif()
