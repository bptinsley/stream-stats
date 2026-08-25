cmake_minimum_required(VERSION 3.20)
project(stream_stats_cpp LANGUAGES CXX)

add_library(stream_stats_cpp SHARED src/stream_stats_cpp.cpp)
target_compile_features(stream_stats_cpp PRIVATE cxx_std_20)
target_include_directories(stream_stats_cpp PRIVATE ../c/include)
target_compile_options(stream_stats_cpp PRIVATE
  $<$<CXX_COMPILER_ID:AppleClang,Clang,GNU>:-O3;-fvisibility=hidden;-fno-fast-math;-ffp-contract=off>
)
set_target_properties(stream_stats_cpp PROPERTIES OUTPUT_NAME stream_stats_cpp)

add_executable(stream_stats_cpp_test tests/test_native.cpp)
target_compile_features(stream_stats_cpp_test PRIVATE cxx_std_20)
target_include_directories(stream_stats_cpp_test PRIVATE ../c/include)
target_link_libraries(stream_stats_cpp_test PRIVATE stream_stats_cpp)
enable_testing()
add_test(NAME stream_stats_cpp_native COMMAND stream_stats_cpp_test)
