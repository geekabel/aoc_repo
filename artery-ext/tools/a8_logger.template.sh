#!/usr/bin/env bash
# =============================================================================
# a8_logger.sh  -  Task A8 of the AoC plan
# Adds the AoC logger to Artery, runs the collective perception example with
# it, and exports the logs to parquet with the schema of the plan.
#
# What it does, step by step:
#   1. copies the logger sources to <root>/artery/scenarios/aoc and adds one
#      block at the end of <root>/artery/scenarios/CMakeLists.txt
#      (a copy of the original file is kept as CMakeLists.txt.a8-backup)
#   2. builds the logger library (about one minute)
#   3. creates a Python environment with pandas and pyarrow in <root>/aoc-venv
#   4. runs the example with the logger (network AocWorld)
#   5. exports the logs to parquet and checks them
#   6. writes a PASS/FAIL report
#
# It needs the installation of A6 and an internet connection for step 3.
#
# Usage:
#   bash a8_logger.sh                 all the steps (steps already done are skipped)
#   bash a8_logger.sh --export DIR    export again the logs of one run (folder aoc-<config>-<run>)
#   bash a8_logger.sh --help
#
# Options are set with environment variables, for example:
#   A8_NTFY_TOPIC=my-secret-topic A8_SIM_TIME=120s bash a8_logger.sh
#
#   A8_ROOT         install folder of A6            (default: $HOME/aoc-sim)
#   A8_SIM_TIME     simulated time                  (default: 60s)
#   A8_SENSORS      aoc = radars only, the sensors file for AoC runs (default)
#                   example = the sensors of the Artery example (with the CAM and see-through sensors)
#   A8_JOBS         parallel compile jobs           (default: from CPU and RAM)
#   A8_NTFY_TOPIC   relay topic for the chat notice (default: empty = no relay)
#   A8_NTFY_SERVER  relay server                    (default: https://ntfy.sh)
#
# Tested on a clean Ubuntu 22.04 with the A6 installation (x86_64).
# =============================================================================

A8_SCRIPT_VERSION="1.1"

# ----------------------------------------------------------------------------
# 0. Start from a clean environment (conda and old OMNeT++ paths break the tools).
# ----------------------------------------------------------------------------
if [ -z "${A8_CLEAN_ENV:-}" ]; then
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
        case "$name" in A8_*) keep+=("$name=$value") ;; esac
    done < <(env)
    exec env -i "${keep[@]}" A8_CLEAN_ENV=1 \
        LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        bash "$0" "$@"
fi

set -u
set -o pipefail
umask 022

A8_ROOT="${A8_ROOT:-$HOME/aoc-sim}"
A8_SIM_TIME="${A8_SIM_TIME:-60s}"
A8_SENSORS="${A8_SENSORS:-aoc}"
A8_NTFY_TOPIC="${A8_NTFY_TOPIC:-}"
A8_NTFY_SERVER="${A8_NTFY_SERVER:-https://ntfy.sh}"

ARTERY_DIR="$A8_ROOT/artery"
BUILD_DIR="$ARTERY_DIR/build"
ENV_FILE="$A8_ROOT/env.sh"
A8_DIR="$A8_ROOT/a8"
LOG_DIR="$A8_DIR/logs"
REPORT="$A8_DIR/a8_report.txt"
EXPORTER="$A8_DIR/a8_export.py"
VENV="$A8_ROOT/aoc-venv"
EXT_DIR="$ARTERY_DIR/scenarios/aoc"
CMAKE_FILE="$ARTERY_DIR/scenarios/CMakeLists.txt"
SERVICES="$ARTERY_DIR/scenarios/artery/services-cp.xml"
SENSORS_AOC="$ARTERY_DIR/scenarios/artery/sensors-aoc.xml"
LIB="$BUILD_DIR/scenarios/aoc/libartery_aoc.so"
SCENARIO="scenarios/artery"
CONFIG="envmod"

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else G=""; R=""; Y=""; N=""; fi
say()  { printf '%s\n' "[A8 $(date +%H:%M:%S)] $*"; }
warn() { printf '%s\n' "${Y}[A8 $(date +%H:%M:%S)] WARNING: $*${N}"; }
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
    [ -n "$A8_NTFY_TOPIC" ] || return 0
    body="$(printf '%s' "$body" | scrub)"
    body="${body:0:3400}"          # the relay accepts 4096 bytes per message
    if command -v curl >/dev/null 2>&1; then
        printf '%s' "$body" | curl -fsS -m 20 -H "Title: $title" --data-binary @- \
            "$A8_NTFY_SERVER/$A8_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 20 -t 1 -O /dev/null --header="Title: $title" --post-data="$body" \
            "$A8_NTFY_SERVER/$A8_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
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
A8_JOBS="${A8_JOBS:-$(default_jobs)}"

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
    put_file "$EXT_DIR/CMakeLists.txt" <<'A8_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/CMakeLists.txt@@
A8_FILE_EOF
    put_file "$EXT_DIR/AocLogger.ned" <<'A8_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.ned@@
A8_FILE_EOF
    put_file "$EXT_DIR/AocWorld.ned" <<'A8_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocWorld.ned@@
A8_FILE_EOF
    put_file "$EXT_DIR/AocLogger.h" <<'A8_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.h@@
A8_FILE_EOF
    put_file "$EXT_DIR/AocLogger.cc" <<'A8_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.cc@@
A8_FILE_EOF
}

write_exporter() {
    put_file "$EXPORTER" <<'A8_FILE_EOF'
@@FILE:src/aoc/io/export.py@@
A8_FILE_EOF
}

write_services() {
    put_file "$SERVICES" <<'A8_FILE_EOF'
@@FILE:artery-ext/config/services-cp.xml@@
A8_FILE_EOF
}

# Sensors for AoC runs. Two sensors of the Artery example are left out:
#  - the see-through sensor: it has no line-of-sight check, so it detects objects behind obstacles
#  - the CAM sensor: it puts every vehicle known from its CAMs into the local model, and so into the CPMs
write_sensors() {
    put_file "$SENSORS_AOC" <<'A8_FILE_EOF'
@@FILE:artery-ext/config/sensors-aoc.xml@@
A8_FILE_EOF
}

# one block at the end of scenarios/CMakeLists.txt tells CMake to build the logger
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
        echo "===== A8 REPORT BEGIN ====="
        echo "result:   $1"
        echo "date:     $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "script:   a8_logger.sh $A8_SCRIPT_VERSION"
        versions
        echo "scenario: $SCENARIO, config $CONFIG, network AocWorld, sim time $A8_SIM_TIME"
        echo "sensors:  $SENSORS_TEXT"
        echo "results:  $2"
        echo "steps:"
        printf '%s' "$STEP_LINES"
        [ -n "$3" ] && printf '%s\n' "$3"
        [ -n "$4" ] && printf '%s\n' "$4"
        echo "===== A8 REPORT END ====="
    })"
    if [ -d "$A8_ROOT" ] && mkdir -p "$A8_DIR" 2>/dev/null; then
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
    printf '%s\n' "${R}[A8 $(date +%H:%M:%S)] A8 FAIL: $1${N}"
    printf '%s\n' "[A8 $(date +%H:%M:%S)] What to do: $2"
    relay "A8 FAIL" "$REPORT_TEXT"
    [ -n "$A8_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
    desktop_notify critical "A8 FAIL" "$1"
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
case "$A8_SENSORS" in
    aoc) SENSORS_TEXT="aoc (4 radars with line-of-sight check; no CAM sensor, no see-through sensor)" ;;
    example) SENSORS_TEXT="example (the sensors of the Artery example, with the CAM and see-through sensors)" ;;
    *) echo "A8_SENSORS must be 'aoc' or 'example', not '$A8_SENSORS'."; exit 2 ;;
esac

MODE="run"
EXPORT_DIR=""
case "${1:-}" in
    "") ;;
    --export) MODE="export"; EXPORT_DIR="${2:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
esac

if [ "$MODE" = "export" ]; then
    [ -n "$EXPORT_DIR" ] || EXPORT_DIR="$(ls -d "$A8_DIR"/latest/aoc-* 2>/dev/null | head -n 1)"
    [ -n "$EXPORT_DIR" ] && [ -d "$EXPORT_DIR" ] || { echo "A8: give the folder aoc-<config>-<run> of a run."; exit 2; }
    [ -x "$VENV/bin/python" ] || { echo "A8: $VENV is missing. Run 'bash a8_logger.sh' once first."; exit 2; }
    mkdir -p "$A8_DIR" && write_exporter || { echo "A8: cannot write $EXPORTER"; exit 2; }
    "$VENV/bin/python" "$EXPORTER" "$EXPORT_DIR"
    exit $?
fi

say "A8 starts. Install folder: $A8_ROOT"

# ---- checks before any change
if [ ! -f "$ENV_FILE" ]; then
    finish_fail "The file $ENV_FILE does not exist." "Task A6 is not installed in $A8_ROOT. Run a6_install.sh first, or set A8_ROOT to the right folder." "" ""
fi
if [ ! -f "$BUILD_DIR/CMakeCache.txt" ] || [ ! -f "$ARTERY_DIR/tools/run_artery.py" ] || [ ! -f "$CMAKE_FILE" ]; then
    finish_fail "Artery is not built in $ARTERY_DIR." "Run: bash a6_install.sh --verify   and fix what it reports." "" ""
fi
if [ ! -f "$ARTERY_DIR/src/artery/envmod/service/CpService.ned" ] || [ ! -f "$BUILD_DIR/src/artery/envmod/libartery_envmod.so" ]; then
    finish_fail "This Artery build has no collective perception service." "Run: bash a6_install.sh --redo artery_build" "" ""
fi
if ! getent hosts localhost >/dev/null 2>&1; then
    finish_fail "The name 'localhost' does not resolve on this machine." "Add the line '127.0.0.1 localhost' to /etc/hosts." "" ""
fi
set +u
# shellcheck disable=SC1090
. "$ENV_FILE" >/dev/null 2>&1
set -u
for tool in cmake python3 sumo; do
    command -v "$tool" >/dev/null 2>&1 || finish_fail "The program '$tool' was not found after loading $ENV_FILE." "Run: bash a6_install.sh --verify" "" ""
done
mkdir -p "$LOG_DIR" || finish_fail "Cannot create $LOG_DIR." "Check the rights of $A8_ROOT." "" ""
relay "A8 START" "A8 started $(date '+%H:%M:%S'). Steps: build the logger, Python environment, run $A8_SIM_TIME, export. A second message follows with the result."

# ---- step 1: sources
T0=$(date +%s)
CHANGED=0
write_sources || finish_fail "Cannot write the logger sources to $EXT_DIR." "Check the rights of the Artery folder." "" ""
patch_cmake || finish_fail "Cannot change $CMAKE_FILE." "Check the rights of the Artery folder." "" ""
SRC_CHANGED="$CHANGED"
step_done "sources" "OK" "$( [ "$SRC_CHANGED" = 1 ] && echo "installed in scenarios/aoc" || echo "already up to date" )"

# ---- step 2: build
T0=$(date +%s)
if [ "$SRC_CHANGED" = 1 ] || [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null; then
    say "Building the logger with $A8_JOBS jobs. Logs: $LOG_DIR/cmake.log and build.log"
    if ! ( cd "$ARTERY_DIR" && cmake -S . -B build ) > "$LOG_DIR/cmake.log" 2>&1; then
        step_done "build" "FAILED" "cmake configure"
        finish_fail "CMake could not configure Artery with the logger." "Read $LOG_DIR/cmake.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/cmake.log")"
    fi
    if ! ( cd "$ARTERY_DIR" && cmake --build build --target aoc -j "$A8_JOBS" ) > "$LOG_DIR/build.log" 2>&1; then
        step_done "build" "FAILED" "compile"
        finish_fail "The logger did not compile." "Read $LOG_DIR/build.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/build.log")"
    fi
    if [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null; then
        step_done "build" "FAILED" "library not registered"
        finish_fail "The build ended but the logger library is not registered in run-artery.ini." "Paste the report in the chat." "" "$(tail -n 8 "$LOG_DIR/cmake.log" | cut -c1-300)"
    fi
    step_done "build" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
else
    step_done "build" "OK" "already built"
fi

# ---- step 3: Python environment
T0=$(date +%s)
if "$VENV/bin/python" -c 'import pandas, pyarrow, numpy' >/dev/null 2>&1; then
    step_done "python" "OK" "already installed"
else
    say "Creating the Python environment $VENV (pandas, pyarrow). Log: $LOG_DIR/python.log"
    {
        python3 -m venv "$VENV" && "$VENV/bin/python" -m pip install --upgrade pip &&
        "$VENV/bin/python" -m pip install 'pandas>=2.0,<3' 'pyarrow>=14'
    } > "$LOG_DIR/python.log" 2>&1
    if ! "$VENV/bin/python" -c 'import pandas, pyarrow, numpy' >/dev/null 2>&1; then
        step_done "python" "FAILED" ""
        HINT="Read $LOG_DIR/python.log. "
        if grep -qi "ensurepip\|python3-venv" "$LOG_DIR/python.log"; then HINT="${HINT}Run: sudo apt install python3-venv"
        elif grep -qi "connection\|timed out\|proxy\|Temporary failure\|SSL" "$LOG_DIR/python.log"; then HINT="${HINT}pip could not reach the internet. Check the connection or the proxy, then run the script again."
        else HINT="${HINT}Paste the report in the chat."; fi
        finish_fail "The Python environment could not be created." "$HINT" "" "$(excerpt "$LOG_DIR/python.log")"
    fi
    step_done "python" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
fi
write_exporter || finish_fail "Cannot write $EXPORTER." "Check the rights of $A8_DIR." "" ""
write_services || finish_fail "Cannot write $SERVICES." "Check the rights of the Artery folder." "" ""
write_sensors || finish_fail "Cannot write $SENSORS_AOC." "Check the rights of the Artery folder." "" ""
SENSOR_OPTS=()
[ "$A8_SENSORS" = "aoc" ] && SENSOR_OPTS=('--*.node[*].environmentModel.sensors=xmldoc("sensors-aoc.xml")')

# ---- step 4: simulation with the logger
STAMP="$(date +%Y%m%d-%H%M%S)"
RES="$A8_DIR/results/$STAMP"
mkdir -p "$RES" || finish_fail "Cannot create $RES." "Check the rights of $A8_DIR." "" ""
SIMLOG="$RES/sim.log"
say "Running the simulation with the logger ($A8_SIM_TIME of simulated time, sensors: $A8_SENSORS). Log: $SIMLOG"
T0=$(date +%s)
(
    cd "$ARTERY_DIR" || exit 97
    timeout 7200 python3 tools/run_artery.py -l build/run-artery.ini -s "$SCENARIO" -- \
        -u Cmdenv -c "$CONFIG" "--sim-time-limit=$A8_SIM_TIME" \
        '--network=AocWorld' \
        '--*.node[*].middleware.services=xmldoc("services-cp.xml")' \
        "${SENSOR_OPTS[@]}" \
        '--**.result-recording-modes=all' \
        '--**.middleware.**.vector-recording=true' \
        '--**.pos*.vector-recording=true' \
        '--**.ChannelLoad*.vector-recording=true' \
        '--**.vector-recording=false' \
        '--**.scalar-recording=true' \
        '--outputvectormanager-class=omnetpp::envir::cIndexedFileOutputVectorManager' \
        "--result-dir=$RES" --debug-on-errors=false --cmdenv-express-mode=true
) > "$SIMLOG" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    step_done "simulation" "FAILED" "exit code $RC"
    HINT="Read $SIMLOG. "
    if grep -q "Could not read option sim-time-limit" "$SIMLOG"; then HINT="${HINT}A8_SIM_TIME='$A8_SIM_TIME' is not a valid time. Use a number with a unit, for example 60s."
    elif grep -q "AocWorld" "$SIMLOG"; then HINT="${HINT}The network AocWorld was not found: the logger library is not loaded. Paste the report in the chat."
    elif grep -q "Connection refused" "$SIMLOG"; then HINT="${HINT}Artery could not reach SUMO. Check that 'sumo --version' works after 'source $ENV_FILE'."
    else HINT="${HINT}Paste the report in the chat."; fi
    finish_fail "The simulation ended with exit code $RC." "$HINT" "$RES" "$(grep -v '^[[:space:]]*$' "$SIMLOG" | tail -n 10 | cut -c1-300)"
fi
step_done "simulation" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
RUN_DIR="$(ls -d "$RES"/aoc-* 2>/dev/null | head -n 1)"
if [ -z "$RUN_DIR" ] || [ ! -f "$RUN_DIR/cpm_tx.csv" ]; then
    finish_fail "The simulation ran but the logger wrote no file." "Paste the report in the chat." "$RES" "$(grep -v '^[[:space:]]*$' "$SIMLOG" | tail -n 6 | cut -c1-300)"
fi

# ---- step 5: export and checks
say "Exporting to parquet."
T0=$(date +%s)
SUMMARY="$("$VENV/bin/python" "$EXPORTER" "$RUN_DIR" 2>&1)"
ERC=$?
if [ "$ERC" -ne 0 ]; then
    step_done "export" "FAILED" "exit code $ERC"
    finish_fail "The export or one of its checks failed (exit code $ERC)." "Paste the report in the chat." "$RES" "$(printf '%s\n' "$SUMMARY" | tail -n 25 | cut -c1-300)"
fi
step_done "export" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
ln -sfn "$RES" "$A8_DIR/latest"

EXTRA="where the files are:"$'\n'"  logger (CSV):  $RUN_DIR"$'\n'"  parquet:       $RUN_DIR/parquet"$'\n'"  main table:    $RUN_DIR/parquet/observations.parquet"
write_report "PASS" "$RES" "$SUMMARY" "$EXTRA"
echo
printf '%s\n' "$REPORT_TEXT"
printf '%s\n' "${G}[A8 $(date +%H:%M:%S)] A8 PASS. Report: $REPORT${N}"
say "Parquet files: $A8_DIR/latest/$(basename "$RUN_DIR")/parquet"
say "Python with pandas and pyarrow: $VENV/bin/python"
relay "A8 PASS" "$REPORT_TEXT"
[ -n "$A8_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
desktop_notify normal "A8 PASS" "Logs exported to parquet. Report: $REPORT"
exit 0
