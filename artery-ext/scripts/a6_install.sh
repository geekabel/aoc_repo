#!/usr/bin/env bash
# =============================================================================
# a6_install.sh  -  Task A6 of the AoC plan
# Installs OMNeT++, SUMO and Artery (with Vanetza, INET, Veins) for one user,
# then runs three short test simulations and writes a PASS/FAIL report.
#
# Usage:
#   bash a6_install.sh                 install, or resume after a failure
#   bash a6_install.sh --verify        only re-run the checks and the report
#   bash a6_install.sh --status        show which steps are done
#   bash a6_install.sh --redo STEP     run one step again (and the steps that depend on it)
#   bash a6_install.sh --print-packages  list the system packages (for an admin)
#   bash a6_install.sh --help
#
# Options are set with environment variables, for example:
#   A6_NTFY_TOPIC=my-secret-topic A6_JOBS=4 bash a6_install.sh
#
#   A6_ROOT         install folder               (default: $HOME/aoc-sim)
#   A6_JOBS         parallel compile jobs        (default: from CPU and RAM)
#   A6_GUI          yes|no, build Qtenv GUI      (default: yes)
#   A6_DEBUG_LIBS   yes|no, OMNeT++ debug libs   (default: yes)
#   A6_SKIP_APT     1 = do not call sudo/apt     (default: 0)
#   A6_NTFY_TOPIC   relay topic for chat notice  (default: empty = no relay)
#   A6_NTFY_SERVER  relay server                 (default: https://ntfy.sh)
#   A6_ARTERY_REF   Artery commit to build       (default: pinned, see below)
#
# Tested end to end on clean Ubuntu 22.04 and Ubuntu 24.04 systems (x86_64).
# =============================================================================

A6_SCRIPT_VERSION="1.0"

# ----------------------------------------------------------------------------
# 0. Start from a clean environment.
#    Conda, old OMNeT++ paths, custom compilers and locale settings are the
#    most common causes of broken builds. The script restarts itself with a
#    minimal environment and keeps only what it needs. Programs in
#    /usr/local/bin (a self-built cmake or python) are not used either.
# ----------------------------------------------------------------------------
if [ -z "${A6_CLEAN_ENV:-}" ]; then
    keep=()
    for v in HOME USER LOGNAME TERM DISPLAY WAYLAND_DISPLAY XAUTHORITY \
             XDG_RUNTIME_DIR XDG_SESSION_TYPE DBUS_SESSION_BUS_ADDRESS \
             http_proxy https_proxy ftp_proxy no_proxy all_proxy \
             HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY ALL_PROXY \
             SSL_CERT_FILE SSL_CERT_DIR CURL_CA_BUNDLE GIT_SSL_CAINFO \
             PIP_CERT PIP_INDEX_URL PIP_TRUSTED_HOST REQUESTS_CA_BUNDLE; do
        if [ -n "${!v:-}" ]; then keep+=("$v=${!v}"); fi
    done
    while IFS='=' read -r name value; do
        case "$name" in A6_*) keep+=("$name=$value") ;; esac
    done < <(env)
    dirty=""
    [ -n "${CONDA_PREFIX:-}" ] && dirty="$dirty conda"
    [ -n "${VIRTUAL_ENV:-}" ] && dirty="$dirty virtualenv"
    [ -n "${__omnetpp_root_dir:-}" ] && dirty="$dirty omnetpp"
    exec env -i "${keep[@]}" A6_CLEAN_ENV=1 A6_DIRTY_ENV="$dirty" \
        LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        bash "$0" "$@"
fi

set -u
set -o pipefail
umask 022

# ----------------------------------------------------------------------------
# 1. Versions. This set is the one used by the Artery project itself:
#    OMNeT++ 5.7.1 is the version of Artery's own Vagrant setup,
#    SUMO 1.21.0 is the version of Artery's continuous integration.
#    Do not use OMNeT++ 6.x: the Artery documentation says it breaks 5.x models.
# ----------------------------------------------------------------------------
OMNETPP_VERSION="5.7.1"
OMNETPP_MD5="98aab18c7ca876a539240a3be95bf47e"
OMNETPP_SHA256="e93262b136fc778649405da640a201787df68f1d3f72504f2a44fded4c7bb6b9"
OMNETPP_URL="https://github.com/omnetpp/omnetpp/releases/download/omnetpp-${OMNETPP_VERSION}/omnetpp-${OMNETPP_VERSION}-src-linux.tgz"
SUMO_VERSION="1.21.0"
ARTERY_URL="https://github.com/riebl/artery.git"
ARTERY_REF="${A6_ARTERY_REF:-e0ddd8c0414b079f28e8efa932f52f66ae5a3c73}"   # master of 2026-07-30

A6_ROOT="${A6_ROOT:-$HOME/aoc-sim}"
A6_GUI="${A6_GUI:-yes}"
A6_DEBUG_LIBS="${A6_DEBUG_LIBS:-yes}"
A6_SKIP_APT="${A6_SKIP_APT:-0}"
A6_NTFY_TOPIC="${A6_NTFY_TOPIC:-}"
A6_NTFY_SERVER="${A6_NTFY_SERVER:-https://ntfy.sh}"

OMNETPP_DIR="$A6_ROOT/omnetpp-$OMNETPP_VERSION"
SUMO_VENV="$A6_ROOT/sumo-venv"
ARTERY_DIR="$A6_ROOT/artery"
BUILD_DIR="$ARTERY_DIR/build"
META_DIR="$A6_ROOT/a6"
STATE_DIR="$META_DIR/state"
LOG_DIR="$META_DIR/logs"
REPORT="$A6_ROOT/a6_report.txt"
LOG_ARCHIVE="$A6_ROOT/a6_logs.tar.gz"
ENV_FILE="$A6_ROOT/env.sh"

STEP_IDS=(packages omnetpp_get omnetpp_build omnetpp_debug omnetpp_check sumo artery_get artery_build tests)
STEP_TITLES=(
    "System packages (apt)"
    "OMNeT++ $OMNETPP_VERSION: download and unpack"
    "OMNeT++ $OMNETPP_VERSION: configure and build (release)"
    "OMNeT++ $OMNETPP_VERSION: build debug libraries"
    "OMNeT++ $OMNETPP_VERSION: self test (aloha sample)"
    "SUMO $SUMO_VERSION: install"
    "Artery: download with Vanetza, INET, Veins"
    "Artery: configure and build"
    "Test simulations (inet, veins, envmod)"
)

# ----------------------------------------------------------------------------
# 2. Small helpers
# ----------------------------------------------------------------------------
if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else B=""; G=""; R=""; Y=""; N=""; fi

say()  { printf '%s\n' "[A6 $(date +%H:%M:%S)] $*"; }
warn() { printf '%s\n' "${Y}[A6 $(date +%H:%M:%S)] WARNING: $*${N}"; }
die()  { printf '%s\n' "${R}[A6 $(date +%H:%M:%S)] ERROR: $*${N}" >&2; exit 2; }

fmt_dur() { local s="$1"; printf '%dm%02ds' $((s / 60)) $((s % 60)); }

# Hide the home folder, the user name and the host name in what leaves the PC.
scrub() {
    local u="${USER:-$(id -un)}" h
    h="$(hostname 2>/dev/null || echo host)"
    sed -e "s|$HOME|~|g" -e "s|\b$u\b|USER|g" -e "s|\b$h\b|HOST|g"
}

# Send one message to the relay. Never fails the install.
relay() {
    local title="$1" body="$2"
    [ -n "$A6_NTFY_TOPIC" ] || return 0
    local before="$RELAY_ERRORS"
    body="$(printf '%s' "$body" | scrub)"
    body="${body:0:3400}"          # the relay accepts 4096 bytes per message
    if command -v curl >/dev/null 2>&1 && [ "${A6_RELAY_TOOL:-curl}" = "curl" ]; then
        printf '%s' "$body" | curl -fsS -m 20 -H "Title: $title" --data-binary @- \
            "$A6_NTFY_SERVER/$A6_NTFY_TOPIC" >/dev/null 2>&1 || RELAY_ERRORS=$((RELAY_ERRORS + 1))
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 20 -t 1 -O /dev/null --header="Title: $title" --post-data="$body" \
            "$A6_NTFY_SERVER/$A6_NTFY_TOPIC" >/dev/null 2>&1 || RELAY_ERRORS=$((RELAY_ERRORS + 1))
    else
        RELAY_ERRORS=$((RELAY_ERRORS + 1))
    fi
    if [ "$RELAY_ERRORS" -eq "$before" ]; then RELAY_LAST_OK=1; else RELAY_LAST_OK=0; fi
    return 0
}
RELAY_ERRORS=0
RELAY_LAST_OK=0
RELAY_FINAL_OK=0

desktop_notify() {
    command -v notify-send >/dev/null 2>&1 || return 0
    notify-send -u "$1" -t 0 "$2" "$3" >/dev/null 2>&1 || true
    printf '\a'
}

default_jobs() {
    local cores mem_kb by_mem
    cores="$(nproc 2>/dev/null || echo 2)"
    mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 4000000)"
    by_mem=$((mem_kb / 1900000))          # about 2 GB of RAM per compile job
    [ "$by_mem" -lt 1 ] && by_mem=1
    [ "$cores" -gt "$by_mem" ] && cores="$by_mem"
    echo "$cores"
}
A6_JOBS="${A6_JOBS:-$(default_jobs)}"

os_field() { ( . /etc/os-release 2>/dev/null; eval "printf '%s' \"\${$1:-}\"" ); }

load_env() {
    # Tool paths for the build steps. 'setenv' uses unset variables, so relax -u.
    set +u
    if [ -f "$OMNETPP_DIR/setenv" ]; then
        # shellcheck disable=SC1091
        cd "$OMNETPP_DIR" && . ./setenv -q >/dev/null 2>&1
        cd - >/dev/null || true
    fi
    set -u
    local sumo_home
    sumo_home="$(ls -d "$SUMO_VENV"/lib/python3*/site-packages/sumo 2>/dev/null | head -n 1 || true)"
    if [ -n "$sumo_home" ]; then
        export SUMO_HOME="$sumo_home"
        export PATH="$SUMO_HOME/bin:$PATH"
    fi
}

# ----------------------------------------------------------------------------
# 3. System packages
# ----------------------------------------------------------------------------
PKG_REQUIRED=(build-essential gcc g++ make bison flex perl git cmake pkg-config
    curl wget ca-certificates tar gzip xz-utils
    python3 python3-dev python3-venv python3-pip
    libxml2-dev zlib1g-dev libssl-dev
    libboost-dev libboost-date-time-dev libboost-system-dev
    libcrypto++-dev)
PKG_GUI=(qtbase5-dev qt5-qmake qtbase5-dev-tools libqt5opengl5-dev
    libgl1 libglu1-mesa libxrender1 libxrandr2 libxcursor1 libxi6 libxft2 libxext6)
# Names differ between Ubuntu releases: the first name that exists is used.
PKG_ALTERNATIVES=("libgeographiclib-dev libgeographic-dev")
# Useful but not needed: installed only if the release has them.
PKG_OPTIONAL=(qtchooser libnotify-bin gdb python3-distutils)

apt_has() {   # no 'grep -q' here: with pipefail an early exit of grep would look like "not found"
    local out
    out="$(apt-cache policy "$1" 2>/dev/null || true)"
    case "$out" in
        *"Candidate: (none)"*|"") return 1 ;;
        *"Candidate:"*) return 0 ;;
        *) return 1 ;;
    esac
}

package_list() {
    local list=("${PKG_REQUIRED[@]}") alt name p
    [ "$A6_GUI" = "yes" ] && list+=("${PKG_GUI[@]}")
    for alt in "${PKG_ALTERNATIVES[@]}"; do
        for name in $alt; do
            if apt_has "$name"; then list+=("$name"); break; fi
        done
    done
    for p in "${PKG_OPTIONAL[@]}"; do
        if apt_has "$p"; then list+=("$p"); fi
    done
    printf '%s\n' "${list[@]}"
}

step_packages() {
    local pkgs missing=() p
    if [ "$A6_SKIP_APT" = "1" ]; then
        echo "A6_SKIP_APT=1: no apt call, only a check of the installed packages."
        mapfile -t pkgs < <(package_list)
        for p in "${pkgs[@]}"; do
            dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
        done
        if [ "${#missing[@]}" -gt 0 ]; then
            echo "A6-ERROR: missing system packages: ${missing[*]}"
            echo "Ask an administrator to run: sudo apt-get install ${missing[*]}"
            return 1
        fi
        return 0
    fi
    sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o DPkg::Lock::Timeout=600 update
    mapfile -t pkgs < <(package_list)
    echo "Packages: ${pkgs[*]}"
    sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o DPkg::Lock::Timeout=600 \
        install -y --no-install-recommends "${pkgs[@]}"
    cmake --version
    g++ --version
    python3 --version
}

# ----------------------------------------------------------------------------
# 4. OMNeT++
# ----------------------------------------------------------------------------
download() {   # url, target file
    local url="$1" out="$2" i
    for i in 1 2 3 4; do
        if curl -fL --retry 3 --retry-delay 5 -C - -o "$out" "$url"; then return 0; fi
        echo "Download attempt $i failed, waiting 15 s."
        sleep 15
    done
    return 1
}

step_omnetpp_get() {
    local tgz="$A6_ROOT/downloads/omnetpp-${OMNETPP_VERSION}-src-linux.tgz" sum
    mkdir -p "$A6_ROOT/downloads"
    sum="$(md5sum "$tgz" 2>/dev/null | cut -d' ' -f1 || true)"
    if [ "$sum" != "$OMNETPP_MD5" ]; then
        download "$OMNETPP_URL" "$tgz" || { rm -f "$tgz"; download "$OMNETPP_URL" "$tgz"; }
        sum="$(md5sum "$tgz" | cut -d' ' -f1)"
    fi
    if [ "$sum" != "$OMNETPP_MD5" ]; then
        echo "A6-ERROR: checksum mismatch for $tgz (got $sum). The file is deleted."
        rm -f "$tgz"
        return 1
    fi
    echo "$OMNETPP_SHA256  $tgz" | sha256sum -c -
    rm -rf "$OMNETPP_DIR"
    mkdir -p "$OMNETPP_DIR"
    tar xzf "$tgz" -C "$OMNETPP_DIR" --strip-components=1
    test -f "$OMNETPP_DIR/configure"
}

set_conf() {   # key, value : edit configure.user
    if grep -q "^$1=" configure.user; then
        sed -i "s|^$1=.*|$1=$2|" configure.user
    else
        echo "$1=$2" >> configure.user
    fi
}

step_omnetpp_build() {
    cd "$OMNETPP_DIR"
    [ -f configure.user ] || cp configure.user.dist configure.user
    set_conf PREFER_CLANG no        # one compiler (GCC) for OMNeT++ and Artery
    set_conf PREFER_LLD no
    set_conf WITH_TKENV no
    set_conf WITH_QTENV "$A6_GUI"
    set_conf WITH_OSG no            # 3D view is not needed
    set_conf WITH_OSGEARTH no       # known to break the INET build (Artery issue 101)
    set_conf WITH_PARSIM no         # no MPI
    set +u
    # shellcheck disable=SC1091
    . ./setenv -q
    set -u
    ./configure
    touch "$STATE_DIR/omnetpp_rebuilt"      # Artery must then be rebuilt from scratch
    make -j"$A6_JOBS" base MODE=release
    ls -l bin/opp_run* lib/
}

step_omnetpp_debug() {
    if [ "$A6_DEBUG_LIBS" != "yes" ]; then
        echo "A6_DEBUG_LIBS=$A6_DEBUG_LIBS: debug libraries are not built."
        return 0
    fi
    cd "$OMNETPP_DIR"
    set +u
    # shellcheck disable=SC1091
    . ./setenv -q
    set -u
    make -j"$A6_JOBS" base MODE=debug
}

step_omnetpp_check() {
    load_env
    local out
    command -v opp_run
    out="$(opp_run -v 2>&1 || true)"
    printf '%s\n' "$out" | sed -n '1,6p'
    cd "$OMNETPP_DIR/samples/aloha"
    opp_makemake -f --deep
    make -j"$A6_JOBS" MODE=release
    ./aloha -u Cmdenv -c PureAloha1 -r 0 --sim-time-limit=200s --cmdenv-express-mode=true > aloha_a6.log 2>&1
    tail -n 15 aloha_a6.log
    grep -q "End\." aloha_a6.log
    if [ "$A6_GUI" = "yes" ]; then
        ls -l "$OMNETPP_DIR"/lib/liboppqtenv*
    fi
}

# ----------------------------------------------------------------------------
# 5. SUMO (official binary package from PyPI, exact version, no compilation)
# ----------------------------------------------------------------------------
step_sumo() {
    if [ ! -x "$SUMO_VENV/bin/python" ]; then
        rm -rf "$SUMO_VENV"
        python3 -m venv "$SUMO_VENV"
    fi
    "$SUMO_VENV/bin/python" -m pip install --upgrade pip
    "$SUMO_VENV/bin/python" -m pip install "eclipse-sumo==$SUMO_VERSION" "traci==$SUMO_VERSION" "sumolib==$SUMO_VERSION"
    load_env
    echo "SUMO_HOME=$SUMO_HOME"
    local out
    command -v sumo
    out="$(sumo --version 2>&1 || true)"
    printf '%s\n' "$out" | sed -n '1,3p'
    case "$out" in
        *"Version $SUMO_VERSION"*) ;;
        *) echo "A6-ERROR: sumo does not report version $SUMO_VERSION."; return 1 ;;
    esac
    if [ "$A6_GUI" = "yes" ]; then
        out="$(ldd "$SUMO_HOME/bin/sumo-gui" 2>&1 || true)"
        if printf '%s\n' "$out" | grep "not found"; then
            echo "A6-ERROR: sumo-gui misses the shared libraries listed above."
            return 1
        fi
    fi
}

# ----------------------------------------------------------------------------
# 6. Artery
# ----------------------------------------------------------------------------
git_retry() {
    local i
    for i in 1 2 3 4; do
        if git "$@"; then return 0; fi
        echo "git $1 attempt $i failed, waiting 20 s."
        sleep 20
    done
    return 1
}

step_artery_get() {
    if [ -d "$ARTERY_DIR" ] && [ ! -d "$ARTERY_DIR/.git" ]; then
        mv "$ARTERY_DIR" "$ARTERY_DIR.broken.$(date +%s)"
    fi
    if [ ! -d "$ARTERY_DIR/.git" ]; then
        git_retry clone "$ARTERY_URL" "$ARTERY_DIR"
    fi
    cd "$ARTERY_DIR"
    if ! git cat-file -e "$ARTERY_REF^{commit}" 2>/dev/null; then
        git_retry fetch origin
    fi
    git -c advice.detachedHead=false checkout "$ARTERY_REF"
    git submodule sync --recursive
    git_retry submodule update --init --recursive --jobs 4
    local f
    for f in extern/vanetza/CMakeLists.txt extern/inet/src/inet/package.ned \
             extern/veins/src/veins/package.ned extern/pybind11/CMakeLists.txt \
             src/artery/envmod/service/CpService.cc; do
        if [ ! -f "$f" ]; then
            echo "A6-ERROR: file $f is missing, the submodules are not complete."
            return 1
        fi
    done
    git log -1 --format='Artery commit %H (%ad)' --date=short
    git submodule status
}

step_artery_build() {
    load_env
    command -v opp_run >/dev/null || { echo "A6-ERROR: opp_run is not in PATH."; return 1; }
    command -v sumo >/dev/null || { echo "A6-ERROR: sumo is not in PATH."; return 1; }
    if [ -f "$STATE_DIR/omnetpp_rebuilt" ]; then
        rm -rf "$BUILD_DIR"
    fi
    cmake -S "$ARTERY_DIR" -B "$BUILD_DIR" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$BUILD_DIR" -j"$A6_JOBS"
    ls -l "$BUILD_DIR"/libartery_core.so "$BUILD_DIR"/src/artery/envmod/libartery_envmod.so \
        "$BUILD_DIR"/extern/libINET.so "$BUILD_DIR"/extern/libveins.so
    test -f "$BUILD_DIR/run-artery.ini"
    rm -f "$STATE_DIR/omnetpp_rebuilt"
}

# ----------------------------------------------------------------------------
# 7. Tests: three 20 s simulations in Cmdenv (no window needed)
# ----------------------------------------------------------------------------
step_tests() {
    load_env
    getent hosts localhost
    command -v opp_run >/dev/null || { echo "A6-ERROR: opp_run is not in PATH."; return 1; }
    command -v sumo >/dev/null || { echo "A6-ERROR: sumo is not in PATH."; return 1; }
    cd "$BUILD_DIR"
    ctest -R '^example-(inet|veins|envmod)$' --output-on-failure --timeout 900
    # the libraries must use the release kernel of OMNeT++ only
    local libs
    libs="$(ldd "$BUILD_DIR/libartery_core.so" 2>&1 || true)"
    if printf '%s\n' "$libs" | grep -E 'libopp[a-z]+_dbg'; then
        echo "A6-ERROR: Artery is linked with debug libraries of OMNeT++ in a release build."
        return 1
    fi
}

write_env_file() {
    cat > "$ENV_FILE" <<EOF
# Environment of the AoC simulation tools. Use it with:  source $ENV_FILE
# Written by a6_install.sh $A6_SCRIPT_VERSION on $(date '+%Y-%m-%d %H:%M').
if [ -n "\${CONDA_PREFIX:-}" ]; then
    echo "env.sh: a conda environment is active. Run 'conda deactivate' before you build Artery." >&2
fi
. "$OMNETPP_DIR/setenv" -q
export SUMO_HOME="\$(ls -d "$SUMO_VENV"/lib/python3*/site-packages/sumo | head -n 1)"
export PATH="\$SUMO_HOME/bin:\$PATH"
export ARTERY_HOME="$ARTERY_DIR"
export ARTERY_BUILD="$BUILD_DIR"
EOF
}

# ----------------------------------------------------------------------------
# 8. Step runner with resume, logs and a heartbeat
# ----------------------------------------------------------------------------
STEP_RESULT=()      # "id|status|seconds"
FAILED_STEP=""
FAILED_LOG=""

step_log() { printf '%s/%02d_%s.log' "$LOG_DIR" "$1" "$2"; }

run_step() {
    local idx="$1" id="${STEP_IDS[$1]}" title="${STEP_TITLES[$1]}"
    local n=$((idx + 1)) total="${#STEP_IDS[@]}" log t0 pid rc last beat
    log="$(step_log "$n" "$id")"
    if [ -f "$STATE_DIR/$id.done" ]; then
        say "[$n/$total] $title: already done, skipped"
        STEP_RESULT+=("$id|done before|0")
        return 0
    fi
    say "${B}[$n/$total] $title${N}  (log: $log)"
    t0="$(date +%s)"
    if [ "$id" = "packages" ]; then
        # in the foreground, so that sudo can ask for the password if it has to
        ( set -Eeuo pipefail; "step_$id" ) >"$log" 2>&1
        rc=$?
        pid=""
    else
        ( set -Eeuo pipefail; "step_$id" ) >"$log" 2>&1 &
        pid=$!
    fi
    beat=0
    while [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; do
        sleep 5
        beat=$((beat + 5))
        if [ $((beat % 120)) -eq 0 ]; then
            last="$(tail -n 40 "$log" 2>/dev/null | grep -E '^\[ *[0-9]+%\]|^=====|^[A-Za-z0-9_./-]+\.(cc|cpp|c)$|Start +[0-9]+:|^(Get|Unpacking|Setting up)' | tail -n 1 | cut -c1-90)"
            [ -n "$last" ] || last="$(tail -n 1 "$log" 2>/dev/null | cut -c1-90)"
            say "    ... running for $(fmt_dur $(( $(date +%s) - t0 ))): $last"
        fi
    done
    if [ -n "$pid" ]; then wait "$pid"; rc=$?; fi
    local dur=$(( $(date +%s) - t0 ))
    if [ "$rc" -eq 0 ]; then
        touch "$STATE_DIR/$id.done"
        say "${G}    OK${N} ($(fmt_dur "$dur"))"
        STEP_RESULT+=("$id|OK|$dur")
        relay "A6 step $n/$total OK" "step $n/$total $id OK in $(fmt_dur "$dur")"
        return 0
    fi
    say "${R}    FAILED${N} with exit code $rc after $(fmt_dur "$dur")"
    STEP_RESULT+=("$id|FAILED (exit $rc)|$dur")
    FAILED_STEP="$id"
    FAILED_LOG="$log"
    return 1
}

# ----------------------------------------------------------------------------
# 9. Hints for known problems, searched in the failing log
# ----------------------------------------------------------------------------
hints() {
    local log="$1"
    [ -f "$log" ] || return 0
    hint() { if grep -Eqi -e "$1" "$log"; then echo "- $2"; fi; }
    hint 'Could not get lock|Unable to acquire the dpkg' \
         'Another package manager is running (often automatic updates). Wait 10 minutes, then run the script again.'
    hint 'Unable to locate package|has no installation candidate' \
         'A package was not found. Run: sudo add-apt-repository universe && sudo apt-get update. Then run the script again.'
    hint 'is not in the sudoers|sudo: a password is required|sudo: a terminal is required' \
         'No sudo right. Ask an administrator to install the packages shown by: bash a6_install.sh --print-packages. Then run with A6_SKIP_APT=1.'
    hint 'No space left on device' \
         'The disk is full. Free at least 8 GB, or set A6_ROOT to a bigger disk, then run the script again.'
    hint 'Could not resolve host|Connection timed out|Failed to connect|Connection refused|Temporary failure in name resolution|Network is unreachable' \
         'Network problem. Check the connection. Behind a proxy, export https_proxy and http_proxy before you run the script.'
    hint 'SSL certificate problem|certificate verify failed|CERTIFICATE_VERIFY_FAILED' \
         'TLS inspection on the network. Export SSL_CERT_FILE, GIT_SSL_CAINFO and PIP_CERT with the path of the CA file of your institution.'
    hint 'RPC failed|early EOF|unexpected disconnect|fetch-pack|remote end hung up' \
         'A git download was cut. Run the script again: it resumes the download.'
    hint 'Killed signal|virtual memory exhausted|Cannot allocate memory|cc1plus.*Killed|internal compiler error: Killed' \
         'The compiler ran out of memory. Run again with fewer jobs, for example: A6_JOBS=2 bash a6_install.sh'
    hint 'checksum mismatch' \
         'The OMNeT++ archive is damaged. Run the script again: it downloads the file again.'
    hint 'Cannot find Qt|qmake.*not found|moc.*not found|Qt5.*not found|configure: error:.*Qt' \
         'Qt problem while building the GUI. Check the Qt packages, or build without GUI: A6_GUI=no bash a6_install.sh --redo omnetpp_build'
    hint 'ensurepip is not available|No module named venv' \
         'Python venv is missing. Run: sudo apt-get install python3-venv. Then run the script again.'
    hint 'No matching distribution found for eclipse-sumo' \
         'pip found no SUMO package for this machine (old pip or a CPU that is not x86_64). Send the report.'
    hint 'opp_run is not in PATH|Could NOT find OmnetPP|OMNeT\+\+ message compiler is missing' \
         'CMake did not find OMNeT++. Run: bash a6_install.sh --redo omnetpp_build'
    hint 'Could NOT find Boost|Could NOT find CryptoPP|Could NOT find GeographicLib' \
         'A library is missing. Run: bash a6_install.sh --redo packages'
    hint 'submodules are not complete|No rule to make target.*extern' \
         'The Artery submodules are not complete. Run: bash a6_install.sh --redo artery_get'
    hint 'anaconda|miniconda|conda/' \
         'Conda files appear in the build. Open a new terminal, run "conda deactivate" until no (env) is shown, then run the script again.'
    hint 'sumo is not in PATH|sumo: not found|sumo: command not found|Could not connect to TraCI|TraCI server' \
         'Artery could not start SUMO. Run: bash a6_install.sh --redo sumo'
    hint 'recompile with -fPIC' \
         'A static library in /usr/local/lib is used by mistake (Artery issue 73). Remove the self-installed GeographicLib, Crypto++, Boost or Vanetza from /usr/local, then: bash a6_install.sh --redo artery_build'
    hint 'Attempt to load the oppsim shared library more than once' \
         'Release and debug libraries of OMNeT++ are mixed. Delete the folder artery/build, then: bash a6_install.sh --redo artery_build'
    hint 'Trace/breakpoint trap|Network .* not found' \
         'The simulation stopped on an error (the example sets debug-on-errors). Look for the line with <!> in the log above it.'
    hint 'Permission denied' \
         'A file is not writable. If you used sudo with this script before, run: sudo chown -R "$USER": "'"$A6_ROOT"'"'
    hint 'error.*osgearth|osgearth.*error' \
         'osgEarth must stay disabled (WITH_OSGEARTH=no in configure.user of OMNeT++).'
    unset -f hint
}

# ----------------------------------------------------------------------------
# 10. Report
# ----------------------------------------------------------------------------
tool_versions() {
    load_env
    echo "os:       $(os_field PRETTY_NAME), kernel $(uname -r), $(uname -m)"
    echo "hardware: $(nproc 2>/dev/null) cores, $(awk '/MemTotal/ {printf "%.1f GB RAM", $2/1048576}' /proc/meminfo), $(df -h --output=avail "$A6_ROOT" 2>/dev/null | tail -n 1 | tr -d ' ') free in A6_ROOT"
    echo "session:  ${XDG_SESSION_TYPE:-unknown}, DISPLAY=${DISPLAY:-none}$(grep -qi microsoft /proc/version 2>/dev/null && echo ', WSL')"
    echo "gcc:      $(g++ --version 2>/dev/null | head -n 1)"
    echo "cmake:    $(cmake --version 2>/dev/null | head -n 1)"
    echo "python:   $(python3 --version 2>&1)"
    echo "boost:    $(dpkg-query -W -f='${Version}' libboost-dev 2>/dev/null)"
    echo "omnetpp:  $(cat "$OMNETPP_DIR/Version" 2>/dev/null || echo 'not installed'), opp_run: $(command -v opp_run || echo 'not found')"
    echo "qtenv:    $(ls "$OMNETPP_DIR"/lib/liboppqtenv.so 2>/dev/null >/dev/null && echo yes || echo no)"
    echo "sumo:     $(sumo --version 2>/dev/null | head -n 1 || true) $(command -v sumo || echo 'not found')"
    echo "artery:   $(git -C "$ARTERY_DIR" log -1 --format='%h %ad' --date=short 2>/dev/null || echo 'not downloaded')"
    echo "cp:       $(ls "$BUILD_DIR"/src/artery/envmod/libartery_envmod.so 2>/dev/null >/dev/null && echo 'envmod library with CpService is built' || echo 'envmod library not built')"
}

write_report() {
    local status="$1" total="$2" r id st sec
    {
        echo "===== A6 REPORT BEGIN ====="
        echo "status:   $status"
        echo "date:     $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "script:   a6_install.sh $A6_SCRIPT_VERSION, jobs=$A6_JOBS, gui=$A6_GUI, mode=$MODE"
        echo "duration: $(fmt_dur "$total")"
        [ -n "${A6_DIRTY_ENV:-}" ] && echo "note:     the calling shell had:${A6_DIRTY_ENV} (ignored by the script)"
        tool_versions 2>/dev/null
        echo "steps:"
        for r in "${STEP_RESULT[@]}"; do
            IFS='|' read -r id st sec <<< "$r"
            printf '  %-15s %-18s %s\n' "$id" "$st" "$(fmt_dur "$sec")"
        done
        if [ "$status" = "PASS" ]; then
            echo "tests:"
            grep -E 'Test +#|tests passed' "$(step_log 9 tests)" 2>/dev/null | sed 's/^/  /'
            echo "next:     source $ENV_FILE"
        else
            echo "failed step: $FAILED_STEP"
            echo "hints:"
            hints "$FAILED_LOG" | sed 's/^/  /'
            echo "last lines of the log of the failed step:"
            tail -n 45 "$FAILED_LOG" 2>/dev/null | cut -c1-300 | sed 's/^/  | /'
        fi
        echo "===== A6 REPORT END ====="
    } > "$REPORT" 2>&1
    tar czf "$LOG_ARCHIVE" -C "$META_DIR" logs 2>/dev/null || true
}

send_final() {
    local status="$1"
    if [ "$status" = "PASS" ]; then
        relay "A6 PASS" "$(cat "$REPORT")"
        RELAY_FINAL_OK="$RELAY_LAST_OK"
        desktop_notify normal "A6 install: PASS" "OMNeT++, SUMO and Artery are installed. Report: $REPORT"
    else
        relay "A6 FAIL" "$(sed -n '1,/^last lines of the log/p' "$REPORT")"
        RELAY_FINAL_OK="$RELAY_LAST_OK"
        relay "A6 FAIL log 1" "$(tail -n 45 "$FAILED_LOG" 2>/dev/null | cut -c1-240)"
        relay "A6 FAIL log 2" "$(grep -n -i -E 'error|fatal|not found|cannot|undefined|failed' "$FAILED_LOG" 2>/dev/null | head -n 30 | cut -c1-240)"
        desktop_notify critical "A6 install: FAIL" "Step $FAILED_STEP failed. Report: $REPORT"
    fi
}

finish() {
    local status="$1" total=$(( $(date +%s) - T_START ))
    write_report "$status" "$total"
    send_final "$status"
    echo
    cat "$REPORT"
    echo
    if [ "$status" = "PASS" ]; then
        say "${G}${B}A6 PASS.${N} Report: $REPORT"
        say "In every new terminal, load the tools first:  source $ENV_FILE"
        say "Then try the GUI:  cmake --build $BUILD_DIR --target run_example"
    else
        say "${R}${B}A6 FAIL at step '$FAILED_STEP'.${N}"
        say "Report: $REPORT   All logs: $LOG_ARCHIVE"
        say "Paste the report in the chat (or attach both files). After a fix, run the script again: it resumes at the failed step."
    fi
    if [ -n "$A6_NTFY_TOPIC" ]; then
        if [ "$RELAY_FINAL_OK" = "1" ]; then say "Relay: the result was sent to topic '$A6_NTFY_TOPIC'."
        else warn "Relay: the result could not be sent (network). Paste the report in the chat instead."; fi
    fi
    [ "$status" = "PASS" ] && exit 0
    exit 1
}

# ----------------------------------------------------------------------------
# 11. Checks before anything is installed
# ----------------------------------------------------------------------------
preflight() {
    local id ver arch avail_kb mem_kb
    say "${B}Checks before the install${N}"
    [ "$(id -u)" -ne 0 ] || [ "${A6_ALLOW_ROOT:-0}" = "1" ] || \
        die "Do not run this script with sudo or as root. Run it as your normal user."
    case "$A6_ROOT" in
        *[[:space:]]*) die "A6_ROOT ($A6_ROOT) contains a space. OMNeT++ does not accept that. Choose another folder." ;;
        /*) ;;
        *) die "A6_ROOT must be an absolute path." ;;
    esac
    id="$(os_field ID)"; ver="$(os_field VERSION_ID)"; arch="$(uname -m)"
    case "$id:$ver" in
        ubuntu:22.04|ubuntu:24.04) say "System: Ubuntu $ver (tested configuration)" ;;
        debian:12) warn "System: Debian 12. Expected to work (Artery uses it for its own tests), but this script was tested on Ubuntu only." ;;
        *) [ "${A6_FORCE_OS:-0}" = "1" ] || die "System '$id $ver' is not supported by this script (Ubuntu 22.04 or 24.04). Set A6_FORCE_OS=1 to try anyway."
           warn "System '$id $ver' is not tested." ;;
    esac
    [ "$arch" = "x86_64" ] || [ "${A6_FORCE_OS:-0}" = "1" ] || \
        die "CPU architecture is $arch. The script expects x86_64. Set A6_FORCE_OS=1 to try anyway."
    grep -qi microsoft /proc/version 2>/dev/null && \
        warn "WSL detected. The command line part works. The GUI needs WSLg."
    [ -n "${A6_DIRTY_ENV:-}" ] && \
        warn "Your shell had these active:${A6_DIRTY_ENV}. The script ignores them. See the guide before you work in that shell."
    mkdir -p "$A6_ROOT" "$STATE_DIR" "$LOG_DIR" || die "Cannot create $A6_ROOT."
    [ -w "$A6_ROOT" ] || die "$A6_ROOT is not writable."
    avail_kb="$(df -Pk "$A6_ROOT" | awk 'NR==2 {print $4}')"
    if [ ! -f "$STATE_DIR/artery_build.done" ] && [ "$avail_kb" -lt 8000000 ]; then
        die "Only $((avail_kb / 1048576)) GB free in $A6_ROOT. At least 8 GB are needed (the install uses about 4 GB)."
    fi
    getent hosts localhost >/dev/null 2>&1 || \
        die "The name 'localhost' does not resolve. Artery talks to SUMO through localhost. Add the line '127.0.0.1 localhost' to /etc/hosts."
    local stray
    stray="$(ls /usr/local/lib/libGeographic* /usr/local/lib/libcryptopp* /usr/local/lib/libboost_* /usr/local/lib/libvanetza* 2>/dev/null | head -n 3 | tr '\n' ' ' || true)"
    [ -n "$stray" ] && warn "Self-installed libraries in /usr/local/lib ($stray...) can break the Artery build (Artery issue 73). If the build fails with '-fPIC' errors, remove them."
    mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo)"
    [ "$mem_kb" -lt 3500000 ] && warn "Less than 4 GB of RAM. The build will be slow and may run out of memory."
    say "Install folder: $A6_ROOT   compile jobs: $A6_JOBS   GUI: $A6_GUI"
    if [ "$A6_SKIP_APT" != "1" ] && [ ! -f "$STATE_DIR/packages.done" ]; then
        command -v sudo >/dev/null 2>&1 || die "sudo is not installed. Ask an administrator for the packages (--print-packages), then use A6_SKIP_APT=1."
        say "The package step needs sudo. Enter your password if asked."
        sudo -v || die "No sudo right. Ask an administrator for the packages (--print-packages), then use A6_SKIP_APT=1."
    fi
    if [ -n "$A6_NTFY_TOPIC" ]; then
        case "$A6_NTFY_TOPIC" in
            *[!A-Za-z0-9_-]*) die "A6_NTFY_TOPIC may only contain letters, digits, '-' and '_'." ;;
        esac
        say "Relay is ON: short status messages go to $A6_NTFY_SERVER/$A6_NTFY_TOPIC"
    fi
}

# ----------------------------------------------------------------------------
# 12. Main
# ----------------------------------------------------------------------------
usage() { sed -n '3,26p' "$0" | sed 's/^# \{0,1\}//'; }

MODE="install"
REDO=""
while [ $# -gt 0 ]; do
    case "$1" in
        --verify) MODE="verify" ;;
        --status) MODE="status" ;;
        --print-packages) MODE="packages" ;;
        --redo) shift; REDO="${1:-}"; [ -n "$REDO" ] || die "--redo needs a step name. Steps: ${STEP_IDS[*]}" ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option '$1'. Use --help." ;;
    esac
    shift
done

if [ "$MODE" = "packages" ]; then
    package_list | tr '\n' ' '; echo
    exit 0
fi

if [ "$MODE" = "status" ]; then
    for i in "${!STEP_IDS[@]}"; do
        if [ -f "$STATE_DIR/${STEP_IDS[$i]}.done" ]; then s="done"; else s="to do"; fi
        printf '%-15s %-6s %s\n' "${STEP_IDS[$i]}" "$s" "${STEP_TITLES[$i]}"
    done
    exit 0
fi

T_START="$(date +%s)"
trap 'echo; say "Stopped. Start the script again to continue where it stopped."; exit 130' INT TERM
preflight

if [ -n "$REDO" ]; then
    # a step is run again together with the steps that depend on it
    case "$REDO" in
        packages)      again="packages" ;;
        omnetpp_get)   again="omnetpp_get omnetpp_build omnetpp_debug omnetpp_check artery_build tests" ;;
        omnetpp_build) again="omnetpp_build omnetpp_debug omnetpp_check artery_build tests" ;;
        omnetpp_debug) again="omnetpp_debug" ;;
        omnetpp_check) again="omnetpp_check" ;;
        sumo)          again="sumo tests" ;;
        artery_get)    again="artery_get artery_build tests" ;;
        artery_build)  again="artery_build tests" ;;
        tests)         again="tests" ;;
        *) die "Unknown step '$REDO'. Steps: ${STEP_IDS[*]}" ;;
    esac
    for id in $again; do rm -f "$STATE_DIR/$id.done"; done
    say "Steps to run again: $again"
fi
[ "$MODE" = "verify" ] && rm -f "$STATE_DIR/tests.done"

relay "A6 START" "A6 $MODE started on $(os_field PRETTY_NAME), jobs=$A6_JOBS, gui=$A6_GUI"

for i in "${!STEP_IDS[@]}"; do
    if [ "$MODE" = "verify" ] && [ "${STEP_IDS[$i]}" != "tests" ] && [ ! -f "$STATE_DIR/${STEP_IDS[$i]}.done" ]; then
        FAILED_STEP="${STEP_IDS[$i]}"; FAILED_LOG="$(step_log $((i + 1)) "${STEP_IDS[$i]}")"
        STEP_RESULT+=("${STEP_IDS[$i]}|not installed|0")
        say "Step '${STEP_IDS[$i]}' is not done. Run the script without --verify first."
        finish "FAIL"
    fi
    run_step "$i"            # no '||' here: it would switch off 'set -e' in the step
    rc=$?
    [ "$rc" -eq 0 ] || finish "FAIL"
done

write_env_file
finish "PASS"
