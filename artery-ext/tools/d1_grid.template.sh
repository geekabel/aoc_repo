#!/usr/bin/env bash
# =============================================================================
# d1_grid.sh  -  Task D1 of the AoC plan
# Runs the core grid of the plan (experiment E3) in Artery: the occluded-VRU
# intersection with background traffic, density {low, medium, high} x
# penetration {10, 25, 50, 100} % x message config {etsi, 2hz, 10hz} x 12 seeds
# = 432 runs of 330 s (seeds 0 to 9 for the evaluation, 10 and 11 for the
# calibration). Exports every run to parquet (the observations of the actors of
# the events and of 8 other equipped cars), packs the CSV logs, and indexes the results.
#
# The grid takes hours. The script can be stopped and started again: a run
# whose export is done is not run again, and --runs or --cell run a part of it.
#
# What it does, step by step:
#   1. installs the AoC sources in <root>/artery/scenarios/aoc (logger + AocCpService) and builds them
#   2. writes the scenario in <root>/artery/scenarios/aoc-grid, builds its road and its buildings
#   3. checks the Python environment of A8 (<root>/aoc-venv)
#   4. runs the runs asked for, several at a time; each run is exported as soon as it ends, then its CSV
#      logs are packed with gzip
#   5. indexes the results (one row per run) and lists what is missing
#   6. writes a PASS/FAIL report (PASS: every run asked for is exported)
#
# It needs the installation of A6 and a run of a8_logger.sh (Python environment).
#
# Usage:
#   bash d1_grid.sh                          the 432 runs that are not done yet (hours; stop and start again at will)
#   bash d1_grid.sh --quick                  one run of 60 s (low, 100, etsi), to check the chain
#   bash d1_grid.sh --control-quick          three runs of 60 s (low, 100) with the generation rules etsi, etsi_abs and aoc
#                                            of AocCpService (task E1), then the check of the rule against the ETSI runs
#   bash d1_grid.sh --cell low 100 etsi      the 12 runs of one cell
#   bash d1_grid.sh --runs 3,17-20,41        these run numbers (ranges allowed)
#   bash d1_grid.sh --check [DIR]            index the results folder again (default: the grid folder)
#   bash d1_grid.sh --help
#
# Options are set with environment variables, for example:
#   D1_NTFY_TOPIC=my-secret-topic D1_JOBS=6 bash d1_grid.sh
#
#   D1_ROOT         install folder of A6            (default: $HOME/aoc-sim)
#   D1_JOBS         runs in parallel, and compile jobs (default: from CPU and RAM)
#   D1_RECEIVERS    other equipped cars whose observations are exported, beside the actors of the events (default: 8)
#   D1_SIM_TIME     simulated time of a run of the grid (default: 330s)
#   D1_NTFY_TOPIC   relay topic for the chat notice (default: empty = no relay)
#   D1_NTFY_SERVER  relay server                    (default: https://ntfy.sh)
#
# Written for the A6 and A8 installations on Ubuntu 22.04 (x86_64); its first run is the grid of D1.
# =============================================================================

D1_SCRIPT_VERSION="1.0"

# ----------------------------------------------------------------------------
# 0. Start from a clean environment (conda and old OMNeT++ paths break the tools).
# ----------------------------------------------------------------------------
if [ -z "${D1_CLEAN_ENV:-}" ]; then
    keep=()
    for v in HOME USER LOGNAME TERM DISPLAY WAYLAND_DISPLAY XAUTHORITY \
             XDG_RUNTIME_DIR XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS \
             http_proxy https_proxy no_proxy all_proxy \
             HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY \
             SSL_CERT_FILE SSL_CERT_DIR CURL_CA_BUNDLE \
             PIP_CERT PIP_INDEX_URL PIP_TRUSTED_HOST REQUESTS_CA_BUNDLE; do
        if [ -n "${!v:-}" ]; then keep+=("$v=${!v}"); fi
    done
    for name in $(compgen -A variable D1_); do      # the exported D1_* options (compgen: no /dev/fd needed)
        [ -n "${!name:-}" ] && keep+=("$name=${!name}")
    done
    exec env -i "${keep[@]}" D1_CLEAN_ENV=1 \
        LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        bash "$0" "$@"
fi

set -u
set -o pipefail
umask 022

D1_ROOT="${D1_ROOT:-$HOME/aoc-sim}"
D1_SIM_TIME="${D1_SIM_TIME:-330s}"
D1_RECEIVERS="${D1_RECEIVERS:-8}"
D1_NTFY_TOPIC="${D1_NTFY_TOPIC:-}"
D1_NTFY_SERVER="${D1_NTFY_SERVER:-https://ntfy.sh}"

ARTERY_DIR="$D1_ROOT/artery"
BUILD_DIR="$ARTERY_DIR/build"
ENV_FILE="$D1_ROOT/env.sh"
D1_DIR="$D1_ROOT/d1"
LOG_DIR="$D1_DIR/logs"
REPORT="$D1_DIR/d1_report.txt"
EXPORTER="$D1_DIR/d1_export.py"
RULECHECK="$D1_DIR/d1_rulecheck.py"
CHECKER="$D1_DIR/d1_grid_index.py"
VENV="$D1_ROOT/aoc-venv"
EXT_DIR="$ARTERY_DIR/scenarios/aoc"
GRID_DIR="$ARTERY_DIR/scenarios/aoc-grid"
CMAKE_FILE="$ARTERY_DIR/scenarios/CMakeLists.txt"
LIB="$BUILD_DIR/scenarios/aoc/libartery_aoc.so"
CONFIG="grid"
N_RUNS=432

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else G=""; R=""; Y=""; N=""; fi
say()  { printf '%s\n' "[D1 $(date +%H:%M:%S)] $*"; }
warn() { printf '%s\n' "${Y}[D1 $(date +%H:%M:%S)] WARNING: $*${N}"; }
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
    [ -n "$D1_NTFY_TOPIC" ] || return 0
    body="$(printf '%s' "$body" | scrub)"
    body="${body:0:3400}"          # the relay accepts 4096 bytes per message
    if command -v curl >/dev/null 2>&1; then
        printf '%s' "$body" | curl -fsS -m 20 -H "Title: $title" --data-binary @- \
            "$D1_NTFY_SERVER/$D1_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 20 -t 1 -O /dev/null --header="Title: $title" --post-data="$body" \
            "$D1_NTFY_SERVER/$D1_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    fi
    return 0
}

desktop_notify() {
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -u "$1" -t 0 "$2" "$3" >/dev/null 2>&1 || true
    printf '\a'
}

usage() { sed -n '3,43p' "$0" | sed 's/^# \{0,1\}//'; }

default_jobs() {
    local cores mem_kb by_mem
    cores="$(nproc 2>/dev/null || echo 2)"
    mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 4000000)"
    by_mem=$((mem_kb / 1900000))
    [ "$by_mem" -lt 1 ] && by_mem=1
    [ "$cores" -gt "$by_mem" ] && cores="$by_mem"
    echo "$cores"
}
D1_JOBS="${D1_JOBS:-$(default_jobs)}"

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
    put_file "$EXT_DIR/CMakeLists.txt" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/CMakeLists.txt@@
D1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.ned" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.ned@@
D1_FILE_EOF
    put_file "$EXT_DIR/AocWorld.ned" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocWorld.ned@@
D1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.h" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.h@@
D1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.cc" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocLogger.cc@@
D1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.ned" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocCpService.ned@@
D1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.h" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocCpService.h@@
D1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.cc" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/AocCpService.cc@@
D1_FILE_EOF
    put_file "$EXT_DIR/cpservice-protected.patch" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/aoc/cpservice-protected.patch@@
D1_FILE_EOF
}

# the scenario: the OMNeT++ configuration, the road of C5, the route files of the three densities, the services and the sensors
write_scenario() {
    mkdir -p "$GRID_DIR" || return 1
    put_file "$GRID_DIR/omnetpp.ini" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/grid/omnetpp.ini@@
D1_FILE_EOF
    put_file "$GRID_DIR/vru.nod.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/vru/vru.nod.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/vru.edg.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/vru/vru.edg.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/vru.con.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/vru/vru.con.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/vru.poly.geo.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/vru/vru.poly.geo.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/grid_low.rou.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/grid/grid_low.rou.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/grid_medium.rou.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/grid/grid_medium.rou.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/grid_high.rou.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/grid/grid_high.rou.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/grid_low.sumo.cfg" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/grid/grid_low.sumo.cfg@@
D1_FILE_EOF
    put_file "$GRID_DIR/grid_medium.sumo.cfg" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/grid/grid_medium.sumo.cfg@@
D1_FILE_EOF
    put_file "$GRID_DIR/grid_high.sumo.cfg" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/grid/grid_high.sumo.cfg@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-10.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-grid-10.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-25.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-grid-25.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-50.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-grid-50.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-100.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-grid-100.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-control-10.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-control-10.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-control-25.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-control-25.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-control-50.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-control-50.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/services-control-100.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/services-control-100.xml@@
D1_FILE_EOF
    put_file "$GRID_DIR/sensors-vru.xml" <<'D1_FILE_EOF' || return 1
@@FILE:artery-ext/config/sensors-vru.xml@@
D1_FILE_EOF
}

write_exporter() {
    put_file "$EXPORTER" <<'D1_FILE_EOF' || return 1
@@FILE:src/aoc/io/export.py@@
D1_FILE_EOF
    put_file "$RULECHECK" <<'D1_FILE_EOF' || return 1
@@FILE:src/aoc/analysis/rulecheck.py@@
D1_FILE_EOF
    put_file "$CHECKER" <<'D1_FILE_EOF' || return 1
@@FILE:src/aoc/analysis/grid.py@@
D1_FILE_EOF
}

# one block at the end of scenarios/CMakeLists.txt tells CMake to build the AoC sources (the same block as A8)
patch_cmake() {
    if grep -q 'add_subdirectory(aoc)' "$CMAKE_FILE"; then return 0; fi
    [ -f "$CMAKE_FILE.a8-backup" ] || cp -p "$CMAKE_FILE" "$CMAKE_FILE.a8-backup" || return 1
    printf '\n# AoC logger (task A8 of the AoC plan)\nif(WITH_ENVMOD)\n    add_subdirectory(aoc)\nendif()\n' >> "$CMAKE_FILE" || return 1
    CHANGED=1
}

# the AoC generation rule (task E1) overrides three methods of CpService: this patch makes them protected and virtual in
# the Artery sources (src/artery/envmod/service/CpService.h). Applied once; a header that already has it is left alone.
patch_cpservice() {
    local patch="$EXT_DIR/cpservice-protected.patch" header="$ARTERY_DIR/src/artery/envmod/service/CpService.h"
    [ -f "$header" ] || return 1
    if grep -q 'virtual bool checkPerceivedObjectTrigger' "$header"; then return 0; fi
    [ -f "$header.aoc-backup" ] || cp -p "$header" "$header.aoc-backup" || return 1
    if [ -d "$ARTERY_DIR/.git" ] && git -C "$ARTERY_DIR" apply "$patch" >/dev/null 2>&1; then :;
    elif ( cd "$ARTERY_DIR" && patch -p1 --forward --silent < "$patch" ) >/dev/null 2>&1; then :;
    else return 1; fi
    grep -q 'virtual bool checkPerceivedObjectTrigger' "$header" || return 1
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
        echo "===== D1 REPORT BEGIN ====="
        echo "result:   $1"
        echo "date:     $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "script:   d1_grid.sh $D1_SCRIPT_VERSION"
        versions
        echo "scenario: scenarios/aoc-grid, config $CONFIG, network AocWorld, sim time $SIM_TIME, receivers exported: the actors of the events + $D1_RECEIVERS"
        echo "results:  $2"
        echo "steps:"
        printf '%s' "$STEP_LINES"
        [ -n "$3" ] && printf '%s\n' "$3"
        [ -n "$4" ] && printf '%s\n' "$4"
        echo "===== D1 REPORT END ====="
    })"
    if [ -d "$D1_ROOT" ] && mkdir -p "$D1_DIR" 2>/dev/null; then
        printf '%s\n' "$REPORT_TEXT" > "$REPORT" 2>/dev/null || true
    fi
}

finish_fail() {
    # $1 short reason, $2 hint, $3 result folder or "", $4 log excerpt or ""
    local extra="reason:   $1"$'\n'"what to do: $2"
    [ -n "${4:-}" ] && extra="$extra"$'\n'"$4"
    write_report "FAIL" "${3:-none}" "" "$extra" 2>/dev/null
    echo
    printf '%s\n' "$REPORT_TEXT"
    printf '%s\n' "${R}[D1 $(date +%H:%M:%S)] D1 FAIL: $1${N}"
    printf '%s\n' "[D1 $(date +%H:%M:%S)] What to do: $2"
    relay "D1 FAIL" "$REPORT_TEXT"
    [ -n "$D1_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
    desktop_notify critical "D1 FAIL" "$1"
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
RUN_LIST=""
parse_runs() {
    # "3,17-20,41" -> "3 17 18 19 20 41"
    local out="" part a b
    IFS=',' read -ra parts <<< "$1"
    for part in "${parts[@]}"; do
        if [[ "$part" =~ ^[0-9]+$ ]]; then out="$out $part"
        elif [[ "$part" =~ ^([0-9]+)-([0-9]+)$ ]]; then a="${BASH_REMATCH[1]}"; b="${BASH_REMATCH[2]}"; out="$out $(seq -s ' ' "$a" "$b")"
        else echo "D1: bad run list element: $part" >&2; return 2; fi
    done
    for n in $out; do [ "$n" -lt "$N_RUNS" ] || { echo "D1: run $n does not exist, the runs are 0 to $((N_RUNS - 1))" >&2; return 2; }; done
    echo "$out"
}
cell_runs() {
    # the 12 run numbers of a cell: run = ((d * 4 + p) * 3 + m) * 12 + seed (the order of the iteration lines of omnetpp.ini)
    local d p m
    case "$1" in low) d=0 ;; medium) d=1 ;; high) d=2 ;; *) echo "D1: density must be low, medium or high" >&2; return 2 ;; esac
    case "$2" in 10) p=0 ;; 25) p=1 ;; 50) p=2 ;; 100) p=3 ;; *) echo "D1: penetration must be 10, 25, 50 or 100" >&2; return 2 ;; esac
    case "$3" in etsi) m=0 ;; 2hz) m=1 ;; 10hz) m=2 ;; *) echo "D1: message config must be etsi, 2hz or 10hz" >&2; return 2 ;; esac
    local base=$(( ((d * 4 + p) * 3 + m) * 12 ))
    seq -s ' ' "$base" $((base + 11))
}
case "${1:-}" in
    "") RUN_LIST="$(seq -s ' ' 0 $((N_RUNS - 1)))" ;;
    --quick) CONFIG="grid_quick"; RUN_LIST="0"; D1_SIM_TIME="60s" ;;
    --control-quick) CONFIG="control_quick"; RUN_LIST="0 1 2"; D1_SIM_TIME="60s" ;;
    --runs) [ -n "${2:-}" ] || { echo "D1: --runs needs a list, for example 3,17-20,41"; exit 2; }; RUN_LIST="$(parse_runs "$2")" || exit 2 ;;
    --cell) [ -n "${4:-}" ] || { echo "D1: --cell needs DENSITY PEN MSG, for example low 100 etsi"; exit 2; }; RUN_LIST="$(cell_runs "$2" "$3" "$4")" || exit 2 ;;
    --check) MODE="check"; CHECK_DIR="${2:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
esac
SIM_TIME="$D1_SIM_TIME"
N_ASKED="$(echo "$RUN_LIST" | wc -w)"
[[ "$D1_RECEIVERS" =~ ^[0-9]+$ ]] || { echo "D1: D1_RECEIVERS must be a whole number (other equipped cars whose observations are exported), not '$D1_RECEIVERS'"; exit 2; }
[[ "$D1_SIM_TIME" =~ ^[0-9]+(\.[0-9]+)?s$ ]] || { echo "D1: D1_SIM_TIME must be a time in seconds like 330s, not '$D1_SIM_TIME'"; exit 2; }

# a run is done when its export ended with PASS (the exporter writes a summary on FAIL too)
is_done() { grep -q '"result": "PASS"' "$1/parquet/export_summary.json" 2>/dev/null; }

# export one run (if not done) and pack its CSV logs; prints nothing, returns the exit code of the exporter
export_one() {
    local dir="$1"
    is_done "$dir" && return 0
    [ -f "$dir/cpm_tx.csv" ] || [ -f "$dir/cpm_tx.csv.gz" ] || return 3
    if [ -f "$dir/cpm_tx.csv.gz" ] && [ ! -f "$dir/cpm_tx.csv" ]; then gunzip -k "$dir"/*.csv.gz 2>/dev/null || gunzip "$dir"/*.csv.gz; fi
    # one export at a time: the exporter of a dense run at 10 Hz holds several GB in memory; the simulations go on meanwhile
    if flock "$(dirname "$dir")/.export.lock" "$VENV/bin/python" "$EXPORTER" "$dir" --receivers "auto:$D1_RECEIVERS" > "$dir/export.log" 2>&1; then
        gzip -f "$dir"/*.csv 2>/dev/null
        return 0
    fi
    return 1
}

# index the results folder with the checker; prints the check and returns its code
index_results() {
    local res="$1"
    "$VENV/bin/python" "$CHECKER" "$res" --out "$res/d1_index.csv" --missing "$res/d1_missing.txt"
}

if [ "$MODE" = "check" ]; then
    [ -n "$CHECK_DIR" ] || CHECK_DIR="$D1_DIR/results/grid"
    [ -d "$CHECK_DIR" ] || { echo "D1: give the results folder of the grid."; exit 2; }
    [ -x "$VENV/bin/python" ] || { echo "D1: $VENV is missing. Run 'bash a8_logger.sh' once first."; exit 2; }
    mkdir -p "$D1_DIR" && write_exporter || { echo "D1: cannot write $EXPORTER"; exit 2; }
    n_exp=0
    for dir in "$CHECK_DIR"/aoc-*; do
        [ -d "$dir" ] || continue
        is_done "$dir" && continue
        n="${dir##*-}"
        # only a run whose simulation ended (a line "n 0 ..." in exit-codes.txt); a run still writing its logs is left alone
        grep -q "^$n 0 " "$CHECK_DIR/exit-codes.txt" 2>/dev/null || continue
        export_one "$dir" && n_exp=$((n_exp + 1))
    done
    [ "$n_exp" -gt 0 ] && echo "exported $n_exp run(s) that were not exported yet"
    index_results "$CHECK_DIR"
    exit $?
fi

say "D1 starts. Install folder: $D1_ROOT. Configuration: $CONFIG, $N_ASKED run(s) asked for, $SIM_TIME each."

# ---- checks before any change
if [ ! -f "$ENV_FILE" ]; then
    finish_fail "The file $ENV_FILE does not exist." "Task A6 is not installed in $D1_ROOT. Run a6_install.sh first, or set D1_ROOT to the right folder." "" ""
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
for tool in cmake python3 sumo netconvert polyconvert flock gzip; do
    command -v "$tool" >/dev/null 2>&1 || finish_fail "The program '$tool' was not found after loading $ENV_FILE." "Run: bash a6_install.sh --verify" "" ""
done
if ! "$VENV/bin/python" -c 'import pandas, pyarrow, numpy' >/dev/null 2>&1; then
    finish_fail "The Python environment $VENV is missing or incomplete." "Run: bash a8_logger.sh   once (it creates the environment)." "" ""
fi
mkdir -p "$LOG_DIR" || finish_fail "Cannot create $LOG_DIR." "Check the rights of $D1_ROOT." "" ""
relay "D1 START" "D1 started $(date '+%H:%M:%S'). Steps: build the AoC sources, write the scenario, $N_ASKED run(s) of $SIM_TIME (the ones not done yet), export and index. A second message follows with the result."

# ---- step 1: sources and build
T0=$(date +%s)
CHANGED=0
write_sources || finish_fail "Cannot write the AoC sources to $EXT_DIR." "Check the rights of the Artery folder." "" ""
patch_cmake || finish_fail "Cannot change $CMAKE_FILE." "Check the rights of the Artery folder." "" ""
patch_cpservice || finish_fail "Cannot patch $ARTERY_DIR/src/artery/envmod/service/CpService.h for the AoC generation rule." "Check that the Artery sources are the pinned commit (a6_install.sh) and that git or patch is installed; the file cpservice-protected.patch in $EXT_DIR holds the change." "" ""
SRC_CHANGED="$CHANGED"
step_done "sources" "OK" "$( [ "$SRC_CHANGED" = 1 ] && echo "installed in scenarios/aoc" || echo "already up to date" )"
if [ "$SRC_CHANGED" = 1 ] || [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null \
   || ! nm -D "$LIB" 2>/dev/null | grep -q 'AocCpService.*ageLimit'; then
    say "Building the AoC sources with $D1_JOBS jobs. Logs: $LOG_DIR/cmake.log and build.log"
    if ! ( cd "$ARTERY_DIR" && cmake -S . -B build ) > "$LOG_DIR/cmake.log" 2>&1; then
        step_done "build" "FAILED" "cmake configure"
        finish_fail "CMake could not configure Artery with the AoC sources." "Read $LOG_DIR/cmake.log. Paste the report in the chat." "" "log excerpt:"$'\n'"$(excerpt "$LOG_DIR/cmake.log")"
    fi
    if ! ( cd "$ARTERY_DIR" && cmake --build build --target aoc -j "$D1_JOBS" ) > "$LOG_DIR/build.log" 2>&1; then
        step_done "build" "FAILED" "compile"
        finish_fail "The AoC sources did not compile." "Read $LOG_DIR/build.log. Paste the report in the chat." "" "log excerpt:"$'\n'"$(excerpt "$LOG_DIR/build.log")"
    fi
    if [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null; then
        step_done "build" "FAILED" "library not registered"
        finish_fail "The build ended but the AoC library is not registered in run-artery.ini." "Paste the report in the chat." "" "log excerpt:"$'\n'"$(tail -n 8 "$LOG_DIR/cmake.log" | cut -c1-300)"
    fi
    step_done "build" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
else
    step_done "build" "OK" "already built"
fi

# ---- step 2: the scenario, its road and its buildings
T0=$(date +%s)
CHANGED=0
write_scenario || finish_fail "Cannot write the scenario to $GRID_DIR." "Check the rights of the Artery folder." "" ""
if [ "$CHANGED" = 1 ] || [ ! -f "$GRID_DIR/vru.net.xml" ] || [ ! -f "$GRID_DIR/vru.poly.xml" ]; then
    if ! ( cd "$GRID_DIR" && netconvert --node-files vru.nod.xml --edge-files vru.edg.xml --connection-files vru.con.xml \
             --proj.utm --crossings.guess --no-turnarounds --output-file vru.net.xml ) > "$LOG_DIR/netconvert.log" 2>&1; then
        step_done "scenario" "FAILED" "netconvert"
        finish_fail "netconvert could not build the road." "Read $LOG_DIR/netconvert.log. Paste the report in the chat." "" "log excerpt:"$'\n'"$(excerpt "$LOG_DIR/netconvert.log")"
    fi
    if ! ( cd "$GRID_DIR" && polyconvert --net-file vru.net.xml --xml-files vru.poly.geo.xml --output-file vru.poly.xml ) > "$LOG_DIR/polyconvert.log" 2>&1; then
        step_done "scenario" "FAILED" "polyconvert"
        finish_fail "polyconvert could not place the buildings." "Read $LOG_DIR/polyconvert.log. Paste the report in the chat." "" "log excerpt:"$'\n'"$(excerpt "$LOG_DIR/polyconvert.log")"
    fi
fi
if ! grep -q 'projParameter="+proj=utm' "$GRID_DIR/vru.net.xml"; then
    step_done "scenario" "FAILED" "no UTM projection in the road file"
    finish_fail "The road file has no UTM projection: Artery could not convert positions to latitude and longitude." "Paste the report in the chat." "" "location line:"$'\n'"$(grep -m1 '<location' "$GRID_DIR/vru.net.xml" | cut -c1-300)"
fi
N_CROSS="$(grep -c 'function="crossing"' "$GRID_DIR/vru.net.xml")"
N_POLY="$(grep -c '<poly ' "$GRID_DIR/vru.poly.xml")"
if [ "$N_CROSS" -lt 5 ] || [ "$N_POLY" -lt 6 ]; then
    step_done "scenario" "FAILED" "$N_CROSS crossings, $N_POLY buildings"
    finish_fail "The road has $N_CROSS crossings and $N_POLY buildings; 5 and 6 are expected." "Read $LOG_DIR/netconvert.log and $LOG_DIR/polyconvert.log. Paste the report in the chat." "" ""
fi
step_done "scenario" "OK" "road with $N_CROSS crossings and $N_POLY buildings in scenarios/aoc-grid"
write_exporter || finish_fail "Cannot write $EXPORTER." "Check the rights of $D1_DIR." "" ""

# ---- step 3: the runs, several at a time, each exported as soon as it ends
if [ "$CONFIG" = "grid" ]; then
    RES="$D1_DIR/results/grid"                       # one fixed folder: the grid is run in several sittings
else
    RES="$D1_DIR/results/$(date +%Y%m%d-%H%M%S)-$CONFIG"
fi
mkdir -p "$RES" || finish_fail "Cannot create $RES." "Check the rights of $D1_DIR." "" ""
exec 9>"$RES/.run.lock"
flock -n 9 || finish_fail "Another d1_grid.sh is running on $RES." "Wait for it, or stop it, then start again. Its progress is in $RES/progress.txt." "$RES" ""
TODO=""
N_SKIPPED=0
for n in $RUN_LIST; do
    if is_done "$RES/aoc-$CONFIG-$n"; then N_SKIPPED=$((N_SKIPPED + 1)); else TODO="$TODO $n"; fi
done
N_TODO="$(echo "$TODO" | wc -w)"
say "Running $N_TODO run(s) of $SIM_TIME ($N_SKIPPED already done), $D1_JOBS at a time. Logs: $RES/run-N.log, progress: $RES/progress.txt"
T0=$(date +%s)
n_done_asked() { local k=0 m; for m in $RUN_LIST; do is_done "$RES/aoc-$CONFIG-$m" && k=$((k + 1)); done; echo "$k"; }
one_run() {
    local n="$1" t0 rc erc
    t0=$(date +%s)
    rm -rf "$RES/aoc-$CONFIG-$n"
    (
        cd "$ARTERY_DIR" || exit 97
        timeout 14400 python3 tools/run_artery.py -l build/run-artery.ini -s scenarios/aoc-grid -- \
            -u Cmdenv -c "$CONFIG" -r "$n" "--sim-time-limit=$SIM_TIME" \
            "--result-dir=$RES" --debug-on-errors=false --cmdenv-express-mode=true
    ) > "$RES/run-$n.log" 2>&1
    rc=$?
    erc="-"
    if [ "$rc" -eq 0 ]; then export_one "$RES/aoc-$CONFIG-$n"; erc=$?; fi
    echo "$n $rc $erc" >> "$RES/exit-codes.txt"
    printf '%s run %s: simulation %s, export %s, %s\n' "$(date +%H:%M:%S)" "$n" "$rc" "$erc" "$(fmt_dur $(( $(date +%s) - t0 )))" >> "$RES/progress.txt"
    if [ "$rc" -eq 0 ] && [ "$erc" -eq 0 ]; then
        say "run $n done in $(fmt_dur $(( $(date +%s) - t0 ))); done: $(n_done_asked) of $N_ASKED asked for"
    else
        warn "run $n failed (simulation $rc, export $erc); see $RES/run-$n.log"
    fi
}
touch "$RES/exit-codes.txt" "$RES/progress.txt"
running=0
for n in $TODO; do
    one_run "$n" &
    running=$((running + 1))
    sleep 1                      # two runs that start together could pick the same port for SUMO
    if [ "$running" -ge "$D1_JOBS" ]; then wait -n 2>/dev/null || wait; running=$((running - 1)); fi
done
wait
# the exit codes of this sitting: the last line of each run asked for
FAILED_RUNS=""
for n in $TODO; do
    line="$(grep "^$n " "$RES/exit-codes.txt" | tail -n 1)"
    [ -n "$line" ] || { FAILED_RUNS="$FAILED_RUNS $n"; continue; }
    set -- $line
    if [ "$2" != 0 ] || [ "$3" != 0 ]; then FAILED_RUNS="$FAILED_RUNS $n"; fi
done
N_FAILED="$(echo "$FAILED_RUNS" | wc -w)"
if [ "$N_FAILED" -gt 0 ]; then
    step_done "simulation" "FAILED" "$N_FAILED of $N_TODO run(s):$FAILED_RUNS"
else
    step_done "simulation" "OK" "$N_TODO run(s) in $(fmt_dur $(( $(date +%s) - T0 ))), $N_SKIPPED skipped (done before)"
fi

# ---- step 4: index the results (the grid) or sum up the run (quick)
T0=$(date +%s)
if [ "$CONFIG" = "grid" ]; then
    say "Indexing the results."
    SUMMARY="$(index_results "$RES" 2>&1)"
    IRC=$?
    if ! printf '%s\n' "$SUMMARY" | grep -q '^grid: '; then
        step_done "index" "FAILED" "the index did not run"
        finish_fail "The index of the results did not run." "Paste the report in the chat." "$RES" "$SUMMARY"
    fi
    step_done "index" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
elif [ "$CONFIG" = "control_quick" ]; then
    # the check of the rule (task E1): the aoc run against the two ETSI runs, after a warm-up of 10 s
    # every car of the quick run is a station, so the receiver metric counts every object (--objects all)
    SUMMARY="$("$VENV/bin/python" "$RULECHECK" "$RES/aoc-$CONFIG-2" --against "$RES/aoc-$CONFIG-0" --warmup 10 --objects all --out "$RES/rulecheck_vs_etsi" 2>&1
               echo; "$VENV/bin/python" "$RULECHECK" "$RES/aoc-$CONFIG-2" --against "$RES/aoc-$CONFIG-1" --warmup 10 --objects all --out "$RES/rulecheck_vs_etsi_abs" 2>&1 | tail -n 4)"
    IRC=0
    step_done "summary" "OK" "rule check"
else
    SUMMARY="$("$VENV/bin/python" - "$RES/aoc-$CONFIG-0/parquet/export_summary.json" <<'PYEOF' 2>&1
import json, sys
info = json.load(open(sys.argv[1]))
rows = info.get("rows", {})
print("quick run: exporter %s, %s s simulated, %d steps, variables %s" % (info.get("result"), info.get("t_last_s"), info.get("steps") or 0, info.get("variables")))
print("receivers exported: %s" % ", ".join(info.get("receivers") or ["all"]))
print("rows: observations %s, local %s, cpm_tx %s, cpm_rx %s; objects %s; parquet %.1f MB" % (
    rows.get("observations"), rows.get("local"), rows.get("cpm_tx"), rows.get("cpm_rx"), info.get("objects_true"), sum(info.get("size_bytes", {}).values()) / 1e6))
c = info.get("checks") or {}
print("local rows kept: %s of %s (the receivers' share); CPM rows matched to a sent CPM: %s of %s" % (
    c.get("local_rows_of_receivers"), c.get("local_rows_all"), c.get("cpm_rx_matched_to_a_sent_cpm"), c.get("cpm_rx_rows")))
PYEOF
)"
    IRC=0
    step_done "summary" "OK" "quick run"
fi
ln -sfn "$RES" "$D1_DIR/latest"

EXTRA="where the files are:"$'\n'"  results:       $RES  (also $D1_DIR/latest)"$'\n'"  one run:       $RES/aoc-$CONFIG-N/parquet (the CSV logs packed as .csv.gz)"
[ "$CONFIG" = "grid" ] && EXTRA="$EXTRA"$'\n'"  index:         $RES/d1_index.csv, missing runs in $RES/d1_missing.txt, progress in $RES/progress.txt"
if [ "$N_FAILED" -gt 0 ]; then
    FIRST="$(echo "$FAILED_RUNS" | awk '{print $1}')"
    HINT="Read $RES/run-$FIRST.log and $RES/aoc-$CONFIG-$FIRST/export.log. "
    if grep -q "NED type\|not found\|no such" "$RES/run-$FIRST.log"; then HINT="${HINT}A module or a file was not found (the AoC library not loaded, or a file of the scenario missing). Paste the report in the chat."
    elif grep -q "Connection refused\|TraCI" "$RES/run-$FIRST.log"; then HINT="${HINT}Artery could not talk to SUMO. Check that 'sumo --version' works after 'source $ENV_FILE'."
    else HINT="${HINT}Start the script again: it runs the failed runs only. If they fail again, paste the report in the chat."; fi
    finish_fail "$N_FAILED run(s) ended with an error or did not export." "$HINT" "$RES" "$SUMMARY"$'\n'"log excerpt of run $FIRST:"$'\n'"$(grep -v '^[[:space:]]*$' "$RES/run-$FIRST.log" | tail -n 10 | cut -c1-300)"
fi
VERDICT="PASS"
if [ "$CONFIG" = "grid" ] && [ "$IRC" -ne 0 ]; then VERDICT="PASS (this sitting; the grid is not complete yet)"; fi
write_report "$VERDICT" "$RES" "$SUMMARY" "$EXTRA"
echo
printf '%s\n' "$REPORT_TEXT"
printf '%s\n' "${G}[D1 $(date +%H:%M:%S)] D1 PASS. Report: $REPORT${N}"
relay "D1 PASS" "$REPORT_TEXT"
[ -n "$D1_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
desktop_notify normal "D1 PASS" "The runs asked for are done and exported. Report: $REPORT"
exit 0
