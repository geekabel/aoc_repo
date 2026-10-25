# AoC extension of Artery: a logger for collective perception experiments (A8)
# and a CP service with a controlled loss of received CPMs (C1).
# Added to the build by one line in scenarios/CMakeLists.txt: add_subdirectory(aoc)
add_artery_feature(aoc AocLogger.cc AocCpService.cc)
target_link_libraries(aoc PUBLIC envmod)
