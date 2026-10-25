#!/usr/bin/env bash
# =============================================================================
# c1_link.sh  -  Task C1 of the AoC plan
# Runs the single-link validation scenario in Artery: three cars on a straight
# road, CPMs with a fixed period T and a known loss p, the grid of the plan
# T in {1, 0.5, 0.2, 0.1} s x p in {0, 0.1, 0.3, 0.5} (16 runs of 330 s).
# Exports every run to parquet and checks each one against its T and p.
#
# What it does, step by step:
#   1. installs the AoC sources in <root>/artery/scenarios/aoc (logger + AocCpService) and builds them
#   2. writes the scenario in <root>/artery/scenarios/aoc-link and builds its road with netconvert
#   3. checks the Python environment of A8 (<root>/aoc-venv)
#   4. runs the 16 runs of the grid, several at a time
#   5. exports every run to parquet and checks the grid (period, loss, the receiver got the object)
#   6. writes a PASS/FAIL report
#
# It needs the installation of A6 and a run of a8_logger.sh (Python environment).
#
# Usage:
#   bash c1_link.sh                   the grid (16 runs, 15 to 40 minutes depending on the machine)
#   bash c1_link.sh --quick           one run of 30 s (T = 0.5 s, p = 0.3), to check the chain
#   bash c1_link.sh --check DIR       export again and check the runs of one result folder
#   bash c1_link.sh --help
#
# Options are set with environment variables, for example:
#   C1_NTFY_TOPIC=my-secret-topic C1_JOBS=4 bash c1_link.sh
#
#   C1_ROOT         install folder of A6            (default: $HOME/aoc-sim)
#   C1_JOBS         runs in parallel, and compile jobs (default: from CPU and RAM)
#   C1_SIM_TIME     simulated time of a run of the grid (default: 330s)
#   C1_NTFY_TOPIC   relay topic for the chat notice (default: empty = no relay)
#   C1_NTFY_SERVER  relay server                    (default: https://ntfy.sh)
#
# Tested on a clean Ubuntu 22.04 with the A6 and A8 installations (x86_64).
# =============================================================================

C1_SCRIPT_VERSION="1.0"

# ----------------------------------------------------------------------------
# 0. Start from a clean environment (conda and old OMNeT++ paths break the tools).
# ----------------------------------------------------------------------------
if [ -z "${C1_CLEAN_ENV:-}" ]; then
    keep=()
    for v in HOME USER LOGNAME TERM DISPLAY WAYLAND_DISPLAY XAUTHORITY \
             XDG_RUNTIME_DIR XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS \
             http_proxy https_proxy no_proxy all_proxy \
             HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY \
             SSL_CERT_FILE SSL_CERT_DIR CURL_CA_BUNDLE \
             PIP_CERT PIP_INDEX_URL PIP_TRUSTED_HOST REQUESTS_CA_BUNDLE; do
        if [ -n "${!v:-}" ]; then keep+=("$v=${!v}"); fi
    done
    while IFS='=' read -r name value; do
        case "$name" in C1_*) keep+=("$name=$value") ;; esac
    done < <(env)
    exec env -i "${keep[@]}" C1_CLEAN_ENV=1 \
        LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        bash "$0" "$@"
fi

set -u
set -o pipefail
umask 022

C1_ROOT="${C1_ROOT:-$HOME/aoc-sim}"
C1_SIM_TIME="${C1_SIM_TIME:-330s}"
C1_NTFY_TOPIC="${C1_NTFY_TOPIC:-}"
C1_NTFY_SERVER="${C1_NTFY_SERVER:-https://ntfy.sh}"

ARTERY_DIR="$C1_ROOT/artery"
BUILD_DIR="$ARTERY_DIR/build"
ENV_FILE="$C1_ROOT/env.sh"
C1_DIR="$C1_ROOT/c1"
LOG_DIR="$C1_DIR/logs"
REPORT="$C1_DIR/c1_report.txt"
EXPORTER="$C1_DIR/c1_export.py"
CHECKER="$C1_DIR/c1_link_check.py"
VENV="$C1_ROOT/aoc-venv"
EXT_DIR="$ARTERY_DIR/scenarios/aoc"
LINK_DIR="$ARTERY_DIR/scenarios/aoc-link"
CMAKE_FILE="$ARTERY_DIR/scenarios/CMakeLists.txt"
LIB="$BUILD_DIR/scenarios/aoc/libartery_aoc.so"
CONFIG="link"
N_RUNS=16

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else G=""; R=""; Y=""; N=""; fi
say()  { printf '%s\n' "[C1 $(date +%H:%M:%S)] $*"; }
warn() { printf '%s\n' "${Y}[C1 $(date +%H:%M:%S)] WARNING: $*${N}"; }
fmt_dur() { local s="$1"; printf '%dm%02ds' $((s / 60)) $((s % 60)); }

# Hide the home folder, the user name and the host name in what leaves the PC.
scrub() {
    local u="${USER:-$(id -un)}" h
    h="$(hostname 2>/dev/null || echo host)"
    sed -e "s|$HOME|~|g" -e "s|\b$u\b|USER|g" -e "s|\b$h\b|HOST|g"
}

RELAY_OK=0
relay() {
    local title="$1" body="$2"
    RELAY_OK=0
    [ -n "$C1_NTFY_TOPIC" ] || return 0
    body="$(printf '%s' "$body" | scrub)"
    body="${body:0:3400}"          # the relay accepts 4096 bytes per message
    if command -v curl >/dev/null 2>&1; then
        printf '%s' "$body" | curl -fsS -m 20 -H "Title: $title" --data-binary @- \
            "$C1_NTFY_SERVER/$C1_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 20 -t 1 -O /dev/null --header="Title: $title" --post-data="$body" \
            "$C1_NTFY_SERVER/$C1_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    fi
    return 0
}

desktop_notify() {
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -u "$1" -t 0 "$2" "$3" >/dev/null 2>&1 || true
    printf '\a'
}

usage() { sed -n '2,35p' "$0" | sed 's/^# \{0,1\}//'; }

default_jobs() {
    local cores mem_kb by_mem
    cores="$(nproc 2>/dev/null || echo 2)"
    mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 4000000)"
    by_mem=$((mem_kb / 1900000))
    [ "$by_mem" -lt 1 ] && by_mem=1
    [ "$cores" -gt "$by_mem" ] && cores="$by_mem"
    echo "$cores"
}
C1_JOBS="${C1_JOBS:-$(default_jobs)}"

# ----------------------------------------------------------------------------
# 1. Files written by this script
# ----------------------------------------------------------------------------
# write stdin to the file $1, only if the content changes; sets CHANGED=1 in that case
CHANGED=0
put_file() {
    local dest="$1" tmp="$1.a8-new"
    cat > "$tmp" || return 1
    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then rm -f "$tmp"; return 0; fi
    mv -f "$tmp" "$dest" || return 1
    CHANGED=1
}

write_sources() {
    mkdir -p "$EXT_DIR" || return 1
    put_file "$EXT_DIR/CMakeLists.txt" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/CMakeLists.txt@@
C1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.ned" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.ned@@
C1_FILE_EOF
    put_file "$EXT_DIR/AocWorld.ned" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocWorld.ned@@
C1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.h" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.h@@
C1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.cc" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.cc@@
C1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.ned" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocCpService.ned@@
C1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.h" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocCpService.h@@
C1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.cc" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocCpService.cc@@
C1_FILE_EOF
}

# the scenario: the OMNeT++ configuration, the SUMO files, the services and the sensors
write_scenario() {
    mkdir -p "$LINK_DIR" || return 1
    put_file "$LINK_DIR/omnetpp.ini" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/link/omnetpp.ini@@
C1_FILE_EOF
    put_file "$LINK_DIR/link.nod.xml" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/link/link.nod.xml@@
C1_FILE_EOF
    put_file "$LINK_DIR/link.edg.xml" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/link/link.edg.xml@@
C1_FILE_EOF
    put_file "$LINK_DIR/link.rou.xml" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/link/link.rou.xml@@
C1_FILE_EOF
    put_file "$LINK_DIR/link.sumo.cfg" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/link/link.sumo.cfg@@
C1_FILE_EOF
    put_file "$LINK_DIR/services-link.xml" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-link.xml@@
C1_FILE_EOF
    put_file "$LINK_DIR/sensors-aoc.xml" <<'C1_FILE_EOF' || return 1
@@FILE:artery-ext/config/sensors-aoc.xml@@
C1_FILE_EOF
}

write_exporter() {
    put_file "$EXPORTER" <<'C1_FILE_EOF' || return 1
@@FILE:src/aoc/io/export.py@@
C1_FILE_EOF
    put_file "$CHECKER" <<'C1_FILE_EOF' || return 1
@@FILE:src/aoc/analysis/link.py@@
C1_FILE_EOF
}

# one block at the end of scenarios/CMakeLists.txt tells CMake to build the AoC sources (the same block as A8)
patch_cmake() {
    if grep -q 'add_subdirectory(aoc)' "$CMAKE_FILE"; then return 0; fi
    [ -f "$CMAKE_FILE.a8-backup" ] || cp -p "$CMAKE_FILE" "$CMAKE_FILE.a8-backup" || return 1
    printf '\n# AoC logger (task A8 of the AoC plan)\nif(WITH_ENVMOD)\n    add_subdirectory(aoc)\nendif()\n' >> "$CMAKE_FILE" || return 1
    CHANGED=1
}

# ----------------------------------------------------------------------------
# 2. Report and end of the script
# ----------------------------------------------------------------------------
STEP_LINES=""
step_done() { STEP_LINES="${STEP_LINES}  $(printf '%-14s %-8s %s' "$1" "$2" "$3")"$'\n'; }

versions() {
    local o s
    o="$(command -v opp_run 2>/dev/null)"; [ -n "$o" ] && o="$(basename "$(dirname "$(dirname "$o")")")"
    s="$(sumo --version 2>/dev/null | head -n 1)"
    printf 'omnetpp:  %s\n' "${o:-not found}"
    printf 'sumo:     %s\n' "${s:-not found}"
    printf 'artery:   %s\n' "$(git -C "$ARTERY_DIR" log -1 --format='%h %cs' 2>/dev/null || echo unknown)"
    local cxx
    cxx="$(sed -n 's/^CMAKE_CXX_COMPILER:[A-Z]*=//p' "$BUILD_DIR/CMakeCache.txt" 2>/dev/null | head -n 1)"
    printf 'compiler: %s %s\n' "${cxx:-unknown}" "$( [ -n "$cxx" ] && "$cxx" -dumpfullversion -dumpversion 2>/dev/null | head -n 1 )"
    printf 'python:   %s\n' "$("$VENV/bin/python" -c 'import sys, pandas, pyarrow; print("%d.%d.%d, pandas %s, pyarrow %s" % (sys.version_info[0], sys.version_info[1], sys.version_info[2], pandas.__version__, pyarrow.__version__))' 2>/dev/null || python3 --version 2>&1)"
}

REPORT_TEXT=""
write_report() {
    # $1 verdict, $2 result folder, $3 summary text, $4 extra text
    REPORT_TEXT="$({
        echo "===== C1 REPORT BEGIN ====="
        echo "result:   $1"
        echo "date:     $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "script:   c1_link.sh $C1_SCRIPT_VERSION"
        versions
        echo "scenario: scenarios/aoc-link, config $CONFIG, network AocWorld, sim time $SIM_TIME"
        echo "results:  $2"
        echo "steps:"
        printf '%s' "$STEP_LINES"
        [ -n "$3" ] && printf '%s\n' "$3"
        [ -n "$4" ] && printf '%s\n' "$4"
        echo "===== C1 REPORT END ====="
    })"
    if [ -d "$C1_ROOT" ] && mkdir -p "$C1_DIR" 2>/dev/null; then
        printf '%s\n' "$REPORT_TEXT" > "$REPORT" 2>/dev/null || true
    fi
}

finish_fail() {
    # $1 short reason, $2 hint, $3 result folder or "", $4 log excerpt or ""
    local extra="reason:   $1"$'\n'"what to do: $2"
    [ -n "${4:-}" ] && extra="$extra"$'\n'"log excerpt:"$'\n'"$4"
    write_report "FAIL" "${3:-none}" "" "$extra" 2>/dev/null
    echo
    printf '%s\n' "$REPORT_TEXT"
    printf '%s\n' "${R}[C1 $(date +%H:%M:%S)] C1 FAIL: $1${N}"
    printf '%s\n' "[C1 $(date +%H:%M:%S)] What to do: $2"
    relay "C1 FAIL" "$REPORT_TEXT"
    [ -n "$C1_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
    desktop_notify critical "C1 FAIL" "$1"
    exit 1
}

# the lines of a build log that matter: errors first, else the end
excerpt() {
    local f="$1" e
    e="$(grep -n -E ' error |: error:|Error [0-9]|CMake Error|fatal error|No such file|ERROR:|Could not' "$f" 2>/dev/null | cut -c1-300 | head -n 12)"
    if [ -n "$e" ]; then printf '%s\n' "$e"; else grep -v '^[[:space:]]*$' "$f" 2>/dev/null | tail -n 10 | cut -c1-300; fi
}

# ----------------------------------------------------------------------------
# 3. Modes
# ----------------------------------------------------------------------------
MODE="run"
CHECK_DIR=""
case "${1:-}" in
    "") ;;
    --quick) CONFIG="link_quick"; N_RUNS=1; C1_SIM_TIME="30s" ;;
    --check) MODE="check"; CHECK_DIR="${2:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
esac
SIM_TIME="$C1_SIM_TIME"

# export every run of a result folder and check the grid; prints the check and returns its code
export_and_check() {
    local res="$1" grid_opt="$2" dir rc=0 n=0 failed=0
    for dir in "$res"/aoc-*; do
        [ -d "$dir" ] && [ -f "$dir/cpm_tx.csv" ] || continue
        n=$((n + 1))
        if ! "$VENV/bin/python" "$EXPORTER" "$dir" > "$dir/export.log" 2>&1; then
            failed=$((failed + 1))
            say "export FAILED for $(basename "$dir"): $(tail -n 3 "$dir/export.log" | cut -c1-200 | tr '\n' ' ')"
        fi
    done
    [ "$n" -gt 0 ] || { echo "no run folder aoc-* with logs in $res"; return 3; }
    [ "$failed" -eq 0 ] || { echo "$failed of $n exports failed"; return 3; }
    "$VENV/bin/python" "$CHECKER" "$res" $grid_opt --single-link --out "$res/c1_grid.csv"
    rc=$?
    return $rc
}

if [ "$MODE" = "check" ]; then
    [ -n "$CHECK_DIR" ] || CHECK_DIR="$(readlink -f "$C1_DIR/latest" 2>/dev/null)"
    [ -n "$CHECK_DIR" ] && [ -d "$CHECK_DIR" ] || { echo "C1: give the result folder of a run of this script."; exit 2; }
    [ -x "$VENV/bin/python" ] || { echo "C1: $VENV is missing. Run 'bash a8_logger.sh' once first."; exit 2; }
    mkdir -p "$C1_DIR" && write_exporter || { echo "C1: cannot write $EXPORTER"; exit 2; }
    GRID_OPT=""; [ "$(ls -d "$CHECK_DIR"/aoc-* 2>/dev/null | wc -l)" -ge 16 ] && GRID_OPT="--grid"
    export_and_check "$CHECK_DIR" "$GRID_OPT"
    exit $?
fi

say "C1 starts. Install folder: $C1_ROOT. Configuration: $CONFIG ($N_RUNS run(s) of $SIM_TIME)."

# ---- checks before any change
if [ ! -f "$ENV_FILE" ]; then
    finish_fail "The file $ENV_FILE does not exist." "Task A6 is not installed in $C1_ROOT. Run a6_install.sh first, or set C1_ROOT to the right folder." "" ""
fi
if [ ! -f "$BUILD_DIR/CMakeCache.txt" ] || [ ! -f "$ARTERY_DIR/tools/run_artery.py" ] || [ ! -f "$CMAKE_FILE" ]; then
    finish_fail "Artery is not built in $ARTERY_DIR." "Run: bash a6_install.sh --verify   and fix what it reports." "" ""
fi
if [ ! -f "$ARTERY_DIR/src/artery/envmod/service/CpService.ned" ] || [ ! -f "$BUILD_DIR/src/artery/envmod/libartery_envmod.so" ]; then
    finish_fail "This Artery build has no collective perception service." "Run: bash a6_install.sh --redo artery_build" "" ""
fi
set +u
# shellcheck disable=SC1090
. "$ENV_FILE" >/dev/null 2>&1
set -u
for tool in cmake python3 sumo netconvert; do
    command -v "$tool" >/dev/null 2>&1 || finish_fail "The program '$tool' was not found after loading $ENV_FILE." "Run: bash a6_install.sh --verify" "" ""
done
if ! "$VENV/bin/python" -c 'import pandas, pyarrow, numpy' >/dev/null 2>&1; then
    finish_fail "The Python environment $VENV is missing or incomplete." "Run: bash a8_logger.sh   once (it creates the environment)." "" ""
fi
mkdir -p "$LOG_DIR" || finish_fail "Cannot create $LOG_DIR." "Check the rights of $C1_ROOT." "" ""
relay "C1 START" "C1 started $(date '+%H:%M:%S'). Steps: build the AoC sources, write the scenario, $N_RUNS run(s) of $SIM_TIME, export and check. A second message follows with the result."

# ---- step 1: sources and build
T0=$(date +%s)
CHANGED=0
write_sources || finish_fail "Cannot write the AoC sources to $EXT_DIR." "Check the rights of the Artery folder." "" ""
patch_cmake || finish_fail "Cannot change $CMAKE_FILE." "Check the rights of the Artery folder." "" ""
SRC_CHANGED="$CHANGED"
step_done "sources" "OK" "$( [ "$SRC_CHANGED" = 1 ] && echo "installed in scenarios/aoc" || echo "already up to date" )"
if [ "$SRC_CHANGED" = 1 ] || [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null \
   || ! nm -D "$LIB" 2>/dev/null | grep -q AocCpService; then
    say "Building the AoC sources with $C1_JOBS jobs. Logs: $LOG_DIR/cmake.log and build.log"
    if ! ( cd "$ARTERY_DIR" && cmake -S . -B build ) > "$LOG_DIR/cmake.log" 2>&1; then
        step_done "build" "FAILED" "cmake configure"
        finish_fail "CMake could not configure Artery with the AoC sources." "Read $LOG_DIR/cmake.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/cmake.log")"
    fi
    if ! ( cd "$ARTERY_DIR" && cmake --build build --target aoc -j "$C1_JOBS" ) > "$LOG_DIR/build.log" 2>&1; then
        step_done "build" "FAILED" "compile"
        finish_fail "The AoC sources did not compile." "Read $LOG_DIR/build.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/build.log")"
    fi
    if [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null; then
        step_done "build" "FAILED" "library not registered"
        finish_fail "The build ended but the AoC library is not registered in run-artery.ini." "Paste the report in the chat." "" "$(tail -n 8 "$LOG_DIR/cmake.log" | cut -c1-300)"
    fi
    step_done "build" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
else
    step_done "build" "OK" "already built"
fi

# ---- step 2: the scenario and its road
T0=$(date +%s)
CHANGED=0
write_scenario || finish_fail "Cannot write the scenario to $LINK_DIR." "Check the rights of the Artery folder." "" ""
if [ "$CHANGED" = 1 ] || [ ! -f "$LINK_DIR/link.net.xml" ]; then
    if ! ( cd "$LINK_DIR" && netconvert --node-files link.nod.xml --edge-files link.edg.xml --proj.utm --output-file link.net.xml ) > "$LOG_DIR/netconvert.log" 2>&1; then
        step_done "scenario" "FAILED" "netconvert"
        finish_fail "netconvert could not build the road." "Read $LOG_DIR/netconvert.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/netconvert.log")"
    fi
fi
if ! grep -q 'projParameter="+proj=utm' "$LINK_DIR/link.net.xml"; then
    step_done "scenario" "FAILED" "no UTM projection in the road file"
    finish_fail "The road file has no UTM projection: Artery could not convert positions to latitude and longitude." "Paste the report in the chat." "" "$(grep -m1 '<location' "$LINK_DIR/link.net.xml" | cut -c1-300)"
fi
ROAD_LENGTH="$(grep -o 'length="[0-9.]*"' "$LINK_DIR/link.net.xml" | head -n 1 | cut -d'"' -f2)"
step_done "scenario" "OK" "road of ${ROAD_LENGTH:-?} m in scenarios/aoc-link"
write_exporter || finish_fail "Cannot write $EXPORTER." "Check the rights of $C1_DIR." "" ""

# ---- step 3: the runs, several at a time
STAMP="$(date +%Y%m%d-%H%M%S)"
RES="$C1_DIR/results/$STAMP-$CONFIG"
mkdir -p "$RES" || finish_fail "Cannot create $RES." "Check the rights of $C1_DIR." "" ""
say "Running $N_RUNS run(s) of $SIM_TIME, $C1_JOBS at a time. Logs: $RES/run-N.log"
T0=$(date +%s)
one_run() {
    local n="$1"
    (
        cd "$ARTERY_DIR" || exit 97
        timeout 7200 python3 tools/run_artery.py -l build/run-artery.ini -s scenarios/aoc-link -- \
            -u Cmdenv -c "$CONFIG" -r "$n" "--sim-time-limit=$SIM_TIME" \
            "--result-dir=$RES" --debug-on-errors=false --cmdenv-express-mode=true
    ) > "$RES/run-$n.log" 2>&1
    echo "$n $?" >> "$RES/exit-codes.txt"
}
: > "$RES/exit-codes.txt"
running=0
for n in $(seq 0 $((N_RUNS - 1))); do
    one_run "$n" &
    running=$((running + 1))
    if [ "$running" -ge "$C1_JOBS" ]; then wait -n 2>/dev/null || wait; running=$((running - 1)); fi
done
wait
FAILED_RUNS="$(awk '$2 != 0 {print $1}' "$RES/exit-codes.txt" | sort -n | tr '\n' ' ')"
if [ -n "$FAILED_RUNS" ]; then
    FIRST="$(echo "$FAILED_RUNS" | awk '{print $1}')"
    step_done "simulation" "FAILED" "runs $FAILED_RUNS"
    HINT="Read $RES/run-$FIRST.log. "
    if grep -q "AocCpService\|AocWorld" "$RES/run-$FIRST.log"; then HINT="${HINT}A module of the AoC library was not found: the library is not loaded or not built. Paste the report in the chat."
    elif grep -q "Connection refused\|TraCI" "$RES/run-$FIRST.log"; then HINT="${HINT}Artery could not talk to SUMO. Check that 'sumo --version' works after 'source $ENV_FILE'."
    else HINT="${HINT}Paste the report in the chat."; fi
    finish_fail "$(echo "$FAILED_RUNS" | wc -w) run(s) ended with an error." "$HINT" "$RES" "$(grep -v '^[[:space:]]*$' "$RES/run-$FIRST.log" | tail -n 10 | cut -c1-300)"
fi
N_DONE="$(ls -d "$RES"/aoc-* 2>/dev/null | wc -l)"
if [ "$N_DONE" -lt "$N_RUNS" ]; then
    finish_fail "The simulations ran but only $N_DONE of $N_RUNS wrote logs." "Paste the report in the chat." "$RES" "$(tail -n 6 "$RES/run-0.log" | cut -c1-300)"
fi
step_done "simulation" "OK" "$N_DONE runs in $(fmt_dur $(( $(date +%s) - T0 )))"

# ---- step 4: export and check
say "Exporting $N_DONE run(s) to parquet and checking the grid."
T0=$(date +%s)
GRID_OPT=""; [ "$CONFIG" = "link" ] && GRID_OPT="--grid"
SUMMARY="$(export_and_check "$RES" "$GRID_OPT" 2>&1)"
ERC=$?
if [ "$ERC" -ne 0 ]; then
    step_done "export+check" "FAILED" "exit code $ERC"
    finish_fail "The export or the check of the grid failed (exit code $ERC)." "Paste the report in the chat." "$RES" "$(printf '%s\n' "$SUMMARY" | tail -n 30 | cut -c1-300)"
fi
step_done "export+check" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
ln -sfn "$RES" "$C1_DIR/latest"

EXTRA="where the files are:"$'\n'"  results:       $RES  (also $C1_DIR/latest)"$'\n'"  one run:       $RES/aoc-$CONFIG-N/parquet"$'\n'"  grid table:    $RES/c1_grid.csv"
write_report "PASS" "$RES" "$SUMMARY" "$EXTRA"
echo
printf '%s\n' "$REPORT_TEXT"
printf '%s\n' "${G}[C1 $(date +%H:%M:%S)] C1 PASS. Report: $REPORT${N}"
relay "C1 PASS" "$REPORT_TEXT"
[ -n "$C1_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
desktop_notify normal "C1 PASS" "The single-link runs are exported and checked. Report: $REPORT"
exit 0
