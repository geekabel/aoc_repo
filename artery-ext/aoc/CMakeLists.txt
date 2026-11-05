# AoC extension of Artery: a logger for collective perception experiments (A8)
# a CP service with the AoC generation rule (E1) and a controlled loss of received CPMs (C1), a source of channel load (E2).
# Added to the build by one line in scenarios/CMakeLists.txt: add_subdirectory(aoc)
add_artery_feature(aoc AocLogger.cc AocCpService.cc AocLoadService.cc)
target_link_libraries(aoc PUBLIC envmod)
