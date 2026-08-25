cmake_minimum_required(VERSION 3.20)
project(stream_stats_c LANGUAGES C)

add_library(stream_stats_c SHARED src/stream_stats_window.c)
target_compile_features(stream_stats_c PRIVATE c_std_11)
target_include_directories(stream_stats_c PUBLIC include)
target_compile_options(stream_stats_c PRIVATE
  $<$<C_COMPILER_ID:AppleClang,Clang,GNU>:-O3;-fvisibility=hidden;-fno-fast-math;-ffp-contract=off>
)
set_target_properties(stream_stats_c PROPERTIES OUTPUT_NAME stream_stats_c)

add_executable(stream_stats_c_test tests/test_native.c)
target_link_libraries(stream_stats_c_test PRIVATE stream_stats_c m)
enable_testing()
add_test(NAME stream_stats_c_native COMMAND stream_stats_c_test)
