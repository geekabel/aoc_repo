# AoC extension of Artery: a logger for collective perception experiments.
# Added to the build by one line in scenarios/CMakeLists.txt: add_subdirectory(aoc)
add_artery_feature(aoc AocLogger.cc)
target_link_libraries(aoc PUBLIC envmod)
