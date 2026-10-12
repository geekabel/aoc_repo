#!/usr/bin/env bash
# =============================================================================
# a7_cp_demo.sh  -  Task A7 of the AoC plan
# Runs the Artery example with the real collective perception service
# (artery.envmod.service.CpService, ETSI CPM Release 2), then finds the CPM
# transmission and reception logs, exports them to CSV and writes a
# PASS/FAIL report.
#
# It needs the installation made by a6_install.sh (task A6). It changes
# nothing in that installation, except one new file:
#   <root>/artery/scenarios/artery/services-cp.xml
#
# Usage:
#   bash a7_cp_demo.sh               run without window, export the logs, write the report
#   bash a7_cp_demo.sh --gui         open the same simulation in the Qtenv window (to watch it)
#   bash a7_cp_demo.sh --analyze DIR export again from an existing result folder
#   bash a7_cp_demo.sh --help
#
# Options are set with environment variables, for example:
#   A7_NTFY_TOPIC=my-secret-topic A7_SIM_TIME=120s bash a7_cp_demo.sh
#
#   A7_ROOT         install folder of A6            (default: $HOME/aoc-sim)
#   A7_SIM_TIME     simulated time                  (default: 60s)
#   A7_NTFY_TOPIC   relay topic for the chat notice (default: empty = no relay)
#   A7_NTFY_SERVER  relay server                    (default: https://ntfy.sh)
#
# Tested on a clean Ubuntu 22.04 with the A6 installation (x86_64).
# =============================================================================

A7_SCRIPT_VERSION="1.0"

# ----------------------------------------------------------------------------
# 0. Start from a clean environment (same reason as in A6: conda and old
#    OMNeT++ paths break the tools). The display variables are kept for --gui.
# ----------------------------------------------------------------------------
if [ -z "${A7_CLEAN_ENV:-}" ]; then
    keep=()
    for v in HOME USER LOGNAME TERM DISPLAY WAYLAND_DISPLAY XAUTHORITY \
             XDG_RUNTIME_DIR XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS QT_QPA_PLATFORM \
             http_proxy https_proxy no_proxy all_proxy \
             HTTP_PROXY HTTPS_PROXY NO_PROXY ALL_PROXY \
             SSL_CERT_FILE SSL_CERT_DIR CURL_CA_BUNDLE; do
        if [ -n "${!v:-}" ]; then keep+=("$v=${!v}"); fi
    done
    while IFS='=' read -r name value; do
        case "$name" in A7_*) keep+=("$name=$value") ;; esac
    done < <(env)
    exec env -i "${keep[@]}" A7_CLEAN_ENV=1 \
        LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        bash "$0" "$@"
fi

set -u
set -o pipefail
umask 022

A7_ROOT="${A7_ROOT:-$HOME/aoc-sim}"
A7_SIM_TIME="${A7_SIM_TIME:-60s}"
A7_NTFY_TOPIC="${A7_NTFY_TOPIC:-}"
A7_NTFY_SERVER="${A7_NTFY_SERVER:-https://ntfy.sh}"

ARTERY_DIR="$A7_ROOT/artery"
ENV_FILE="$A7_ROOT/env.sh"
A7_DIR="$A7_ROOT/a7"
REPORT="$A7_DIR/a7_report.txt"
TOOL="$A7_DIR/a7_cpm_logs.py"
SERVICES="$ARTERY_DIR/scenarios/artery/services-cp.xml"
SCENARIO="scenarios/artery"
CONFIG="envmod"

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else G=""; R=""; Y=""; N=""; fi
say()  { printf '%s\n' "[A7 $(date +%H:%M:%S)] $*"; }
warn() { printf '%s\n' "${Y}[A7 $(date +%H:%M:%S)] WARNING: $*${N}"; }

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
    [ -n "$A7_NTFY_TOPIC" ] || return 0
    body="$(printf '%s' "$body" | scrub)"
    body="${body:0:3400}"          # the relay accepts 4096 bytes per message
    if command -v curl >/dev/null 2>&1; then
        printf '%s' "$body" | curl -fsS -m 20 -H "Title: $title" --data-binary @- \
            "$A7_NTFY_SERVER/$A7_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 20 -t 1 -O /dev/null --header="Title: $title" --post-data="$body" \
            "$A7_NTFY_SERVER/$A7_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    fi
    return 0
}

desktop_notify() {
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -u "$1" -t 0 "$2" "$3" >/dev/null 2>&1 || true
    printf '\a'
}

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

# ----------------------------------------------------------------------------
# 1. The files this script writes
# ----------------------------------------------------------------------------
write_services() {
    # CA service (as in the example) plus the real CP service on BTP port 2009.
    local tmp="$A7_DIR/services-cp.xml.new"
    cat > "$tmp" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
	</service>
</services>
XML
    if [ -f "$SERVICES" ] && cmp -s "$tmp" "$SERVICES"; then rm -f "$tmp"
    else mv -f "$tmp" "$SERVICES" || return 1; fi
}

write_tool() {
    cat > "$TOOL" <<'PYEOF'
@@A7_CPM_LOGS_PY@@
PYEOF
}

# ----------------------------------------------------------------------------
# 2. Checks before the run. Each failure gives the reason and what to do.
# ----------------------------------------------------------------------------
FAIL_REASON=""
FAIL_HINT=""
preflight() {
    if [ ! -f "$ENV_FILE" ]; then
        FAIL_REASON="The file $ENV_FILE does not exist."
        FAIL_HINT="Task A6 is not installed in $A7_ROOT. Run a6_install.sh first, or set A7_ROOT to the right folder."
        return 1
    fi
    if [ ! -f "$ARTERY_DIR/build/run-artery.ini" ] || [ ! -f "$ARTERY_DIR/tools/run_artery.py" ]; then
        FAIL_REASON="Artery is not built in $ARTERY_DIR (run-artery.ini or tools/run_artery.py is missing)."
        FAIL_HINT="Run: bash a6_install.sh --verify   and fix what it reports."
        return 1
    fi
    if [ ! -f "$ARTERY_DIR/src/artery/envmod/service/CpService.ned" ] || \
       [ ! -f "$ARTERY_DIR/build/src/artery/envmod/libartery_envmod.so" ]; then
        FAIL_REASON="This Artery build has no collective perception service (CpService or libartery_envmod.so is missing)."
        FAIL_HINT="Run: bash a6_install.sh --redo artery_build"
        return 1
    fi
    if ! getent hosts localhost >/dev/null 2>&1; then
        FAIL_REASON="The name 'localhost' does not resolve on this machine."
        FAIL_HINT="Add the line '127.0.0.1 localhost' to /etc/hosts. Artery talks to SUMO through localhost."
        return 1
    fi
    set +u
    # shellcheck disable=SC1090
    . "$ENV_FILE" >/dev/null 2>&1
    set -u
    if ! command -v sumo >/dev/null 2>&1 || ! sumo --version >/dev/null 2>&1; then
        FAIL_REASON="SUMO does not start after loading $ENV_FILE."
        FAIL_HINT="Run: bash a6_install.sh --redo sumo"
        return 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        FAIL_REASON="python3 was not found."
        FAIL_HINT="Run: sudo apt install python3"
        return 1
    fi
    mkdir -p "$A7_DIR" || { FAIL_REASON="Cannot create $A7_DIR."; FAIL_HINT="Check the rights of $A7_ROOT."; return 1; }
    write_services || { FAIL_REASON="Cannot write $SERVICES."; FAIL_HINT="Check the rights of the Artery folder."; return 1; }
    write_tool || { FAIL_REASON="Cannot write $TOOL."; FAIL_HINT="Check the rights of $A7_DIR."; return 1; }
    return 0
}

# Options that switch the CP service on and record its logs.
#  - the services file replaces the mock service of the example by CpService
#  - result-recording-modes=all is needed: the CPM vectors are optional ("?") in CpService.ned
#  - only the useful vectors are recorded: the CPM and CAM logs of the middleware, the position
#    of each vehicle (posX, posY) and the channel load. Recording everything gives a file
#    about 100 times larger.
#  - the text vector file is forced, so that the export always finds a .vec file
opp_options() {
    printf '%s\n' \
        "-c" "$CONFIG" \
        "--sim-time-limit=$A7_SIM_TIME" \
        '--*.node[*].middleware.services=xmldoc("services-cp.xml")' \
        '--**.result-recording-modes=all' \
        '--**.middleware.**.vector-recording=true' \
        '--**.pos*.vector-recording=true' \
        '--**.ChannelLoad*.vector-recording=true' \
        '--**.vector-recording=false' \
        '--**.scalar-recording=true' \
        '--outputvectormanager-class=omnetpp::envir::cIndexedFileOutputVectorManager' \
        "--result-dir=$1"
}

versions() {
    local o s
    o="$(command -v opp_run 2>/dev/null)"; [ -n "$o" ] && o="$(basename "$(dirname "$(dirname "$o")")")"
    s="$(sumo --version 2>/dev/null | head -n 1)"
    printf 'omnetpp:  %s\n' "${o:-not found}"
    printf 'sumo:     %s\n' "${s:-not found}"
    printf 'artery:   %s\n' "$(git -C "$ARTERY_DIR" log -1 --format='%h %cs' 2>/dev/null || echo unknown)"
    printf 'python:   %s\n' "$(python3 --version 2>&1)"
}

REPORT_TEXT=""
write_report() {
    # $1 verdict, $2 result folder, $3 duration text, $4 summary text, $5 extra text
    REPORT_TEXT="$({
        echo "===== A7 REPORT BEGIN ====="
        echo "result:   $1"
        echo "date:     $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "script:   a7_cp_demo.sh $A7_SCRIPT_VERSION"
        versions
        echo "scenario: $SCENARIO, config $CONFIG, CP service = artery.envmod.service.CpService"
        echo "sim time: $A7_SIM_TIME, wall time: $3"
        echo "results:  $2"
        [ -n "$4" ] && printf '%s\n' "$4"
        [ -n "$5" ] && printf '%s\n' "$5"
        echo "===== A7 REPORT END ====="
    })"
    # the report is saved only if the install folder exists (no folder is created at a wrong place)
    if [ -d "$A7_ROOT" ] && mkdir -p "$A7_DIR" 2>/dev/null; then
        printf '%s\n' "$REPORT_TEXT" > "$REPORT" 2>/dev/null || true
    fi
}

finish_fail() {
    # $1 short reason, $2 hint, $3 result folder or "", $4 log tail or ""
    local extra="reason:   $1"$'\n'"what to do: $2"
    [ -n "${4:-}" ] && extra="$extra"$'\n'"last lines of the simulation log:"$'\n'"$4"
    write_report "FAIL" "${3:-none}" "${DURATION_TEXT:-0m00s}" "" "$extra" 2>/dev/null
    echo
    printf '%s\n' "$REPORT_TEXT"
    printf '%s\n' "${R}[A7 $(date +%H:%M:%S)] A7 FAIL: $1${N}"
    printf '%s\n' "[A7 $(date +%H:%M:%S)] What to do: $2"
    relay "A7 FAIL" "$REPORT_TEXT"
    [ -n "$A7_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
    desktop_notify critical "A7 FAIL" "$1"
    exit 1
}

fmt_dur() { local s="$1"; printf '%dm%02ds' $((s / 60)) $((s % 60)); }

# ----------------------------------------------------------------------------
# 3. Modes
# ----------------------------------------------------------------------------
MODE="run"
ANALYZE_DIR=""
case "${1:-}" in
    "") ;;
    --gui) MODE="gui" ;;
    --analyze) MODE="analyze"; ANALYZE_DIR="${2:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
esac

if [ "$MODE" = "analyze" ]; then
    [ -n "$ANALYZE_DIR" ] || ANALYZE_DIR="$A7_DIR/latest"
    [ -e "$ANALYZE_DIR" ] || { echo "A7: $ANALYZE_DIR does not exist."; exit 2; }
    mkdir -p "$A7_DIR" && write_tool || { echo "A7: cannot write $TOOL"; exit 2; }
    python3 "$TOOL" "$ANALYZE_DIR"
    exit $?
fi

say "A7 starts. Install folder: $A7_ROOT"
if ! preflight; then
    finish_fail "$FAIL_REASON" "$FAIL_HINT" "" ""
fi

if [ "$MODE" = "gui" ]; then
    if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
        echo "A7: no display found. Open a terminal on the desktop of the workstation (not a plain SSH session)."
        exit 2
    fi
    RES="$A7_DIR/results-gui"
    mkdir -p "$RES"
    mapfile -t OPTS < <(opp_options "$RES")
    say "Opening Qtenv. Press Run (F5) or Fast (F6). Close the window to stop."
    say "In the object tree, open World > node[0] > middleware > CP to see the CP service of one vehicle."
    cd "$ARTERY_DIR" || exit 2
    exec python3 tools/run_artery.py -l build/run-artery.ini -s "$SCENARIO" -- -u Qtenv "${OPTS[@]}"
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
RES="$A7_DIR/results/$STAMP"
mkdir -p "$RES" || finish_fail "Cannot create $RES." "Check the rights of $A7_DIR." "" ""
SIMLOG="$RES/sim.log"
mapfile -t OPTS < <(opp_options "$RES")

relay "A7 START" "A7 started $(date '+%H:%M:%S'). Simulated time $A7_SIM_TIME. A second message follows with the result."
say "Running the simulation without window ($A7_SIM_TIME of simulated time). Log: $SIMLOG"
T0=$(date +%s)
(
    cd "$ARTERY_DIR" || exit 97
    # debug-on-errors=false: a model error ends the run with a message instead of waiting for a debugger
    timeout 7200 python3 tools/run_artery.py -l build/run-artery.ini -s "$SCENARIO" -- \
        -u Cmdenv "${OPTS[@]}" --debug-on-errors=false --cmdenv-express-mode=true
) > "$SIMLOG" 2>&1
RC=$?
DURATION_TEXT="$(fmt_dur $(( $(date +%s) - T0 )))"

if [ "$RC" -ne 0 ]; then
    TAIL="$(grep -v '^[[:space:]]*$' "$SIMLOG" | tail -n 12)"
    HINT="Read $SIMLOG. "
    if grep -q "Connection refused" "$SIMLOG"; then
        HINT="${HINT}Artery could not reach SUMO. Check that 'sumo --version' works after 'source $ENV_FILE'."
    elif grep -qi "cannot open shared object file\|error while loading shared libraries" "$SIMLOG"; then
        HINT="${HINT}A library is missing. Run: bash a6_install.sh --verify"
    elif grep -q "services-cp.xml" "$SIMLOG"; then
        HINT="${HINT}The services file was not found or not accepted. Check $SERVICES."
    elif grep -q "Could not read option sim-time-limit" "$SIMLOG"; then
        HINT="${HINT}A7_SIM_TIME='$A7_SIM_TIME' is not a valid time. Use a number with a unit, for example 60s."
    elif [ "$RC" -eq 124 ]; then
        HINT="${HINT}The run was stopped after 2 hours. Use a shorter A7_SIM_TIME."
    else
        HINT="${HINT}Paste the report in the chat."
    fi
    finish_fail "The simulation ended with exit code $RC after $DURATION_TEXT." "$HINT" "$RES" "$TAIL"
fi
say "Simulation done in $DURATION_TEXT. Exporting the CPM logs."

SUMMARY="$(python3 "$TOOL" "$RES" --out "$RES" 2>&1)"
ARC=$?
if [ "$ARC" -ne 0 ]; then
    finish_fail "The simulation ran, but the CPM logs are missing or not consistent (export exit code $ARC)." \
        "Paste the report in the chat." "$RES" "$SUMMARY"
fi
ln -sfn "$RES" "$A7_DIR/latest"

SUMO_VEH="$(grep -h -A3 '^Vehicles:' "$RES"/sumo-*.log 2>/dev/null | sed -n 's/^ *Inserted: *//p' | head -n 1)"
EXTRA="sumo vehicles inserted: ${SUMO_VEH:-unknown}"$'\n'"where the logs are:"$'\n'"  raw:    $RES/$(basename "$(ls "$RES"/*.vec | head -n 1)")"$'\n'"          vectors transmission and reception of each module World.node[i].middleware.CP"$'\n'"  export: $RES/cpm_tx.csv, cpm_rx.csv, cpm_links.csv"
write_report "PASS" "$RES" "$DURATION_TEXT" "$SUMMARY" "$EXTRA"
echo
printf '%s\n' "$REPORT_TEXT"
printf '%s\n' "${G}[A7 $(date +%H:%M:%S)] A7 PASS. Report: $REPORT${N}"
say "Logs: $A7_DIR/latest  (cpm_tx.csv, cpm_rx.csv, cpm_links.csv)"
say "To watch the same simulation in a window:  bash $0 --gui"
relay "A7 PASS" "$REPORT_TEXT"
[ -n "$A7_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
desktop_notify normal "A7 PASS" "CPM logs exported. Report: $REPORT"
exit 0
