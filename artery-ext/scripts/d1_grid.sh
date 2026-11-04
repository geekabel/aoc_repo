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
# AoC extension of Artery: a logger for collective perception experiments (A8)
# and a CP service with a controlled loss of received CPMs (C1).
# Added to the build by one line in scenarios/CMakeLists.txt: add_subdirectory(aoc)
add_artery_feature(aoc AocLogger.cc AocCpService.cc)
target_link_libraries(aoc PUBLIC envmod)
D1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.ned" <<'D1_FILE_EOF' || return 1
//
// AocLogger: writes what the AoC replay needs, as CSV files.
//
//  truth.csv    true state of every object at every step of the environment model
//  local.csv    which object each on-board sensor of each vehicle detected, at every step
//  cpm_tx.csv   one row per CPM sent
//  cpm_obj.csv  one row per perceived object of each CPM sent
//  cpm_rx.csv   one row per CPM received
//  cpm_drop.csv one row per CPM dropped on purpose by AocCpService (task C1)
//  cbr.csv      channel load measured by each vehicle
//  nodes.csv    vehicles with a middleware: module, SUMO id, station id
//  run.csv      configuration name, run number, seed set
//
// The logger only listens to signals. It does not change the simulation.
//
simple AocLogger
{
    parameters:
        @display("i=block/table2;p=220,20");
        bool enabled = default(true);        // false: the module does nothing
        string globalEnvironmentModule = default("environmentModel");
        string outputDir = default("");     // empty: <result-dir>/aoc-<configname>-<runnumber>
        bool logTruth = default(true);
        bool logLocal = default(true);
        bool logChannelLoad = default(true);
        string label = default("");         // free text, not read by the code: a place to hang an iteration variable
                                            // of the configuration so that it is recorded in run.csv (task D1: the message config)
}
D1_FILE_EOF
    put_file "$EXT_DIR/AocWorld.ned" <<'D1_FILE_EOF' || return 1
//
// The environment model network of Artery, plus the AoC logger.
// Use it with:  network = AocWorld
//
import artery.envmod.World;

network AocWorld extends World
{
    submodules:
        aocLogger: AocLogger;
}
D1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.h" <<'D1_FILE_EOF' || return 1
/*
 * AoC logger for Artery (task A8 of the AoC plan).
 * Licensed under GPLv2 like Artery.
 */
#ifndef AOC_LOGGER_H_
#define AOC_LOGGER_H_

#include <omnetpp/clistener.h>
#include <omnetpp/csimplemodule.h>
#include <omnetpp/simtime.h>

#include <cstdint>
#include <fstream>
#include <string>
#include <unordered_map>

namespace artery
{
class CpObject;
class GlobalEnvironmentModel;
class Middleware;
}  // namespace artery

class AocLogger : public omnetpp::cSimpleModule, public omnetpp::cListener
{
public:
    AocLogger() = default;
    ~AocLogger() override;

    void initialize() override;
    void handleMessage(omnetpp::cMessage*) override;
    void finish() override;

    // CpmSent, CpmReceived, CpmDropped, EnvironmentModel.refresh
    void receiveSignal(omnetpp::cComponent*, omnetpp::simsignal_t, omnetpp::cObject*, omnetpp::cObject*) override;
    // ChannelLoad
    void receiveSignal(omnetpp::cComponent*, omnetpp::simsignal_t, double, omnetpp::cObject*) override;

private:
    struct Node {
        omnetpp::cModule* host = nullptr;
        artery::Middleware* middleware = nullptr;
        std::string name;       // node[3]
        std::string traciId;    // SUMO id
    };

    const Node& node(omnetpp::cComponent* source);
    void logCpmSent(omnetpp::cComponent* source, const artery::CpObject&);
    void logCpmReceived(omnetpp::cComponent* source, const artery::CpObject&);
    void logCpmDropped(omnetpp::cComponent* source, const artery::CpObject&);
    void dumpTruth();
    void dumpLocal();
    void closeAll();
    void unsubscribeAll();

    artery::GlobalEnvironmentModel* mGem = nullptr;
    bool mLogTruth = true;
    bool mLogLocal = true;
    bool mLogChannelLoad = true;
    bool mSubscribed = false;
    omnetpp::SimTime mLastLocalDump = -1;
    std::unordered_map<int, Node> mNodes;   // key: module id of the host
    std::unordered_map<int, omnetpp::SimTime> mLastCbr;   // key: module id of the host
    std::string mDir;
    std::ofstream mTruth, mLocal, mTx, mObj, mRx, mDrop, mCbr, mNodesFile;
    unsigned long mRowsTruth = 0, mRowsLocal = 0, mRowsTx = 0, mRowsObj = 0, mRowsRx = 0, mRowsDrop = 0, mRowsCbr = 0;
};

#endif /* AOC_LOGGER_H_ */
D1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.cc" <<'D1_FILE_EOF' || return 1
/*
 * AoC logger for Artery (task A8 of the AoC plan).
 * Licensed under GPLv2 like Artery.
 *
 * The logger is a passive listener at the top of the network:
 *  - "CpmSent" and "CpmReceived" are emitted by artery.envmod.service.CpService
 *  - "CpmDropped" is emitted by AocCpService when it loses a received CPM on purpose (task C1)
 *  - "EnvironmentModel.refresh" is emitted by the global environment model at each SUMO step.
 *    Listeners of a module are called before the listeners of its parent, so the local
 *    environment models of the vehicles are already updated when the logger is called.
 *  - "ChannelLoad" is emitted by the radio of each vehicle.
 * Positions are in the frame of Artery and OMNeT++: x to the east, y to the south, in meters.
 * Headings are in degrees from north, clockwise.
 */
#include "AocLogger.h"

#include "artery/application/Middleware.h"
#include "artery/application/VehicleDataProvider.h"
#include "artery/envmod/EnvironmentModelObject.h"
#include "artery/envmod/GlobalEnvironmentModel.h"
#include "artery/envmod/LocalEnvironmentModel.h"
#include "artery/envmod/TraCIEnvironmentModelObject.h"
#include "artery/envmod/sensor/Sensor.h"
#include "artery/envmod/service/CpObject.h"
#include "artery/utility/Identity.h"

#include <omnetpp.h>
#include <vanetza/asn1/cpm.hpp>
#include <vanetza/units/angle.hpp>
#include <vanetza/units/velocity.hpp>

#include <sys/stat.h>
#include <sys/types.h>

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <sstream>

using namespace omnetpp;

Define_Module(AocLogger)

namespace
{

const simsignal_t sigCpmSent = cComponent::registerSignal("CpmSent");
const simsignal_t sigCpmReceived = cComponent::registerSignal("CpmReceived");
const simsignal_t sigCpmDropped = cComponent::registerSignal("CpmDropped");
const simsignal_t sigRefresh = cComponent::registerSignal("EnvironmentModel.refresh");
const simsignal_t sigChannelLoad = cComponent::registerSignal("ChannelLoad");

const char* const kLoggerVersion = "1.1";

// simulation time with microsecond digits, the same text in every file
std::string T(const SimTime& t)
{
    char buf[40];
    std::snprintf(buf, sizeof(buf), "%.6f", t.dbl());
    return buf;
}

std::string F(double v, int digits = 3)
{
    char buf[40];
    std::snprintf(buf, sizeof(buf), "%.*f", digits, v);
    return buf;
}

void makeDirs(const std::string& path)
{
    std::string partial;
    for (std::size_t i = 0; i <= path.size(); ++i) {
        if (i == path.size() || path[i] == '/') {
            if (!partial.empty() && ::mkdir(partial.c_str(), 0755) != 0 && errno != EEXIST) {
                throw cRuntimeError("AocLogger: cannot create folder '%s': %s", partial.c_str(), std::strerror(errno));
            }
        }
        if (i < path.size()) {
            partial.push_back(path[i]);
        }
    }
}

void openFile(std::ofstream& f, const std::string& dir, const char* name, const char* header)
{
    const std::string path = dir + "/" + name;
    f.open(path, std::ios::out | std::ios::trunc);
    if (!f.is_open()) {
        throw cRuntimeError("AocLogger: cannot write '%s'", path.c_str());
    }
    f << header << '\n';
}

double degrees(vanetza::units::Angle a)
{
    return a.value() * 180.0 / 3.14159265358979323846;   // the value of an Angle is in radians
}

double mps(vanetza::units::Velocity v)
{
    return v / vanetza::units::si::meter_per_second;
}

// class of a perceived object as written in the CPM: "vehicle:5", "vru:1", "group", "other", or empty
std::string classLabel(const Vanetza_ITS2_PerceivedObject_t& po)
{
    if (!po.classification || po.classification->list.count < 1 || !po.classification->list.array[0]) {
        return "";
    }
    const Vanetza_ITS2_ObjectClass_t& oc = po.classification->list.array[0]->objectClass;
    std::ostringstream os;
    switch (oc.present) {
        case Vanetza_ITS2_ObjectClass_PR_vehicleSubClass:
            os << "vehicle:" << oc.choice.vehicleSubClass;
            break;
        case Vanetza_ITS2_ObjectClass_PR_vruSubClass:
            os << "vru:" << static_cast<int>(oc.choice.vruSubClass.present);
            break;
        case Vanetza_ITS2_ObjectClass_PR_groupSubClass:
            os << "group";
            break;
        case Vanetza_ITS2_ObjectClass_PR_otherSubClass:
            os << "other";
            break;
        default:
            break;
    }
    return os.str();
}

}  // namespace


AocLogger::~AocLogger()
{
    unsubscribeAll();
    closeAll();
}

void AocLogger::initialize()
{
    if (!par("enabled").boolValue()) {
        return;   // nothing is opened and nothing is subscribed
    }
    mLogTruth = par("logTruth");
    mLogLocal = par("logLocal");
    mLogChannelLoad = par("logChannelLoad");

    cModule* gem = getParentModule()->getSubmodule(par("globalEnvironmentModule").stringValue());
    mGem = dynamic_cast<artery::GlobalEnvironmentModel*>(gem);
    if (!mGem) {
        throw cRuntimeError("AocLogger: no global environment model named '%s' in the network", par("globalEnvironmentModule").stringValue());
    }

    mDir = par("outputDir").stdstringValue();
    if (mDir.empty()) {
        mDir = getEnvir()->getConfigEx()->substituteVariables("${resultdir}/aoc-${configname}-${runnumber}");
    }
    makeDirs(mDir);

    openFile(mTruth, mDir, "truth.csv", "t,obj_id,x,y,speed,heading_deg,length,width,station_type");
    openFile(mLocal, mDir, "local.csv", "t,node,node_id,sensor,category,obj_id,track_id,t_first,t_last");
    openFile(mTx, mDir, "cpm_tx.csv",
        "t_tx,tx_node,tx_id,station_id,reference_time_ms,t_ref,ego_x,ego_y,ego_speed,ego_heading_deg,n_objects,n_containers,has_sensor_info,size_bytes");
    openFile(mObj, mDir, "cpm_obj.csv",
        "station_id,reference_time_ms,obj_index,object_id,meas_delta_ms,x_cm,y_cm,object_age_ms,quality,has_velocity,speed_cms,direction_raw,class_label,sensor_ids");
    openFile(mRx, mDir, "cpm_rx.csv", "t_rx,rx_node,rx_id,station_id,reference_time_ms,rx_x,rx_y");
    openFile(mDrop, mDir, "cpm_drop.csv", "t_drop,rx_node,rx_id,station_id,reference_time_ms");
    openFile(mCbr, mDir, "cbr.csv", "t,node,node_id,cbr");
    openFile(mNodesFile, mDir, "nodes.csv", "t_first,node,node_id,station_id,module");

    std::ofstream run;
    openFile(run, mDir, "run.csv", "key,value");
    cConfigurationEx* cfg = getEnvir()->getConfigEx();
    run << "logger_version," << kLoggerVersion << '\n';
    run << "config," << cfg->getActiveConfigName() << '\n';
    run << "run_number," << cfg->getActiveRunNumber() << '\n';
    run << "run_id," << cfg->substituteVariables("${runid}") << '\n';
    run << "repetition," << cfg->substituteVariables("${repetition}") << '\n';
    run << "seed_set," << cfg->substituteVariables("${seedset}") << '\n';
    run << "iteration_vars,\"" << cfg->substituteVariables("${iterationvars}") << "\"\n";
    run << "network," << getSystemModule()->getNedTypeName() << '\n';
    run.close();

    cModule* sys = getSystemModule();
    sys->subscribe(sigCpmSent, this);
    sys->subscribe(sigCpmReceived, this);
    sys->subscribe(sigCpmDropped, this);
    sys->subscribe(sigRefresh, this);
    if (mLogChannelLoad) {
        sys->subscribe(sigChannelLoad, this);
    }
    mSubscribed = true;
}

void AocLogger::handleMessage(cMessage* msg)
{
    delete msg;   // the logger receives no message
}

void AocLogger::unsubscribeAll()
{
    if (!mSubscribed) {
        return;
    }
    mSubscribed = false;
    cModule* sys = getSystemModule();
    if (sys) {
        sys->unsubscribe(sigCpmSent, this);
        sys->unsubscribe(sigCpmReceived, this);
        sys->unsubscribe(sigCpmDropped, this);
        sys->unsubscribe(sigRefresh, this);
        if (sys->isSubscribed(sigChannelLoad, this)) {
            sys->unsubscribe(sigChannelLoad, this);
        }
    }
}

void AocLogger::closeAll()
{
    for (std::ofstream* f : {&mTruth, &mLocal, &mTx, &mObj, &mRx, &mDrop, &mCbr, &mNodesFile}) {
        if (f->is_open()) {
            f->close();
        }
    }
}

void AocLogger::finish()
{
    if (!mSubscribed && !mTruth.is_open()) {
        return;   // logger switched off
    }
    unsubscribeAll();
    closeAll();
    recordScalar("aocTruthRows", mRowsTruth);
    recordScalar("aocLocalRows", mRowsLocal);
    recordScalar("aocCpmTxRows", mRowsTx);
    recordScalar("aocCpmObjRows", mRowsObj);
    recordScalar("aocCpmRxRows", mRowsRx);
    recordScalar("aocCpmDropRows", mRowsDrop);
    EV_INFO << "AocLogger wrote " << mDir << endl;
}

const AocLogger::Node& AocLogger::node(cComponent* source)
{
    // the host is the module just below the network, for example World.node[3]
    cModule* host = dynamic_cast<cModule*>(source);
    if (!host) {
        host = source->getParentModule();
    }
    cModule* sys = getSystemModule();
    while (host && host->getParentModule() && host->getParentModule() != sys) {
        host = host->getParentModule();
    }
    if (!host) {
        throw cRuntimeError("AocLogger: signal source without host module");
    }

    auto found = mNodes.find(host->getId());
    if (found != mNodes.end()) {
        return found->second;
    }

    Node n;
    n.host = host;
    n.name = host->getFullName();
    n.middleware = dynamic_cast<artery::Middleware*>(host->getSubmodule("middleware"));
    uint32_t station = 0;
    if (n.middleware) {
        const artery::Identity& identity = n.middleware->getIdentity();
        n.traciId = identity.traci;
        station = identity.application;
    }
    mNodesFile << T(simTime()) << ',' << n.name << ',' << n.traciId << ',' << station << ',' << host->getFullPath() << '\n';
    return mNodes.emplace(host->getId(), std::move(n)).first->second;
}

void AocLogger::receiveSignal(cComponent* source, simsignal_t signal, cObject* obj, cObject*)
{
    if (signal == sigRefresh) {
        if (mLogTruth) {
            dumpTruth();
        }
        if (mLogLocal) {
            dumpLocal();
        }
    } else if (signal == sigCpmSent) {
        if (auto cpm = dynamic_cast<artery::CpObject*>(obj)) {
            logCpmSent(source, *cpm);
        }
    } else if (signal == sigCpmReceived) {
        if (auto cpm = dynamic_cast<artery::CpObject*>(obj)) {
            logCpmReceived(source, *cpm);
        }
    } else if (signal == sigCpmDropped) {
        if (auto cpm = dynamic_cast<artery::CpObject*>(obj)) {
            logCpmDropped(source, *cpm);
        }
    }
}

void AocLogger::receiveSignal(cComponent* source, simsignal_t signal, double value, cObject*)
{
    if (signal == sigChannelLoad) {
        // the radio driver repeats the signal of the radio: keep one value per vehicle and time
        const Node& n = node(source);
        const SimTime now = simTime();
        auto last = mLastCbr.find(n.host->getId());
        if (last != mLastCbr.end() && last->second == now) {
            return;
        }
        mLastCbr[n.host->getId()] = now;
        mCbr << T(now) << ',' << n.name << ',' << n.traciId << ',' << F(value, 5) << '\n';
        ++mRowsCbr;
    }
}

void AocLogger::logCpmSent(cComponent* source, const artery::CpObject& object)
{
    const Node& n = node(source);
    const artery::Cpm& cpm = object.asn1();

    const unsigned long station = cpm->header.stationId;
    uint64_t reference = 0;
    asn_INTEGER2uint64(&cpm->payload.managementContainer.referenceTime, &reference);

    std::string tRef, egoX, egoY, egoSpeed, egoHeading;
    if (n.middleware) {
        if (auto vdp = n.middleware->getFacilities().get_const_ptr<artery::VehicleDataProvider>()) {
            tRef = T(vdp->updated());
            egoX = F(vdp->position().x.value());
            egoY = F(vdp->position().y.value());
            egoSpeed = F(mps(vdp->speed()));
            egoHeading = F(degrees(vdp->heading()), 2);
        }
    }

    int nObjects = 0;
    bool hasSensorInfo = false;
    const auto& containers = cpm->payload.cpmContainers.list;
    for (int i = 0; i < containers.count; ++i) {
        const Vanetza_ITS2_WrappedCpmContainer_t* wcc = containers.array[i];
        if (!wcc) {
            continue;
        }
        if (wcc->containerData.present == Vanetza_ITS2_WrappedCpmContainer__containerData_PR_SensorInformationContainer) {
            hasSensorInfo = true;
        }
        if (wcc->containerData.present != Vanetza_ITS2_WrappedCpmContainer__containerData_PR_PerceivedObjectContainer) {
            continue;
        }
        const auto& objects = wcc->containerData.choice.PerceivedObjectContainer.perceivedObjects.list;
        for (int j = 0; j < objects.count; ++j) {
            const Vanetza_ITS2_PerceivedObject_t* po = objects.array[j];
            if (!po) {
                continue;
            }
            mObj << station << ',' << reference << ',' << nObjects << ',';
            if (po->objectId) {
                mObj << *po->objectId;
            }
            mObj << ',' << po->measurementDeltaTime << ',' << po->position.xCoordinate.value << ',' << po->position.yCoordinate.value << ',';
            if (po->objectAge) {
                mObj << *po->objectAge;
            }
            mObj << ',';
            if (po->objectPerceptionQuality) {
                mObj << *po->objectPerceptionQuality;
            }
            mObj << ',';
            if (po->velocity && po->velocity->present == Vanetza_ITS2_Velocity3dWithConfidence_PR_polarVelocity) {
                const auto& polar = po->velocity->choice.polarVelocity;
                mObj << "1," << polar.velocityMagnitude.speedValue << ',' << polar.velocityDirection.value;
            } else {
                mObj << "0,,";
            }
            mObj << ',' << classLabel(*po) << ',';
            if (po->sensorIdList) {
                for (int k = 0; k < po->sensorIdList->list.count; ++k) {
                    if (k > 0) {
                        mObj << ' ';
                    }
                    mObj << *po->sensorIdList->list.array[k];
                }
            }
            mObj << '\n';
            ++nObjects;
            ++mRowsObj;
        }
    }

    mTx << T(simTime()) << ',' << n.name << ',' << n.traciId << ',' << station << ',' << reference << ',' << tRef << ',' << egoX << ',' << egoY << ','
        << egoSpeed << ',' << egoHeading << ',' << nObjects << ',' << containers.count << ',' << (hasSensorInfo ? 1 : 0) << ',' << cpm.size() << '\n';
    ++mRowsTx;
}

void AocLogger::logCpmReceived(cComponent* source, const artery::CpObject& object)
{
    const Node& n = node(source);
    const artery::Cpm& cpm = object.asn1();
    uint64_t reference = 0;
    asn_INTEGER2uint64(&cpm->payload.managementContainer.referenceTime, &reference);

    std::string rxX, rxY;
    if (n.middleware) {
        if (auto vdp = n.middleware->getFacilities().get_const_ptr<artery::VehicleDataProvider>()) {
            rxX = F(vdp->position().x.value());
            rxY = F(vdp->position().y.value());
        }
    }
    mRx << T(simTime()) << ',' << n.name << ',' << n.traciId << ',' << cpm->header.stationId << ',' << reference << ',' << rxX << ',' << rxY << '\n';
    ++mRowsRx;
}

void AocLogger::logCpmDropped(cComponent* source, const artery::CpObject& object)
{
    const Node& n = node(source);
    const artery::Cpm& cpm = object.asn1();
    uint64_t reference = 0;
    asn_INTEGER2uint64(&cpm->payload.managementContainer.referenceTime, &reference);
    mDrop << T(simTime()) << ',' << n.name << ',' << n.traciId << ',' << cpm->header.stationId << ',' << reference << '\n';
    ++mRowsDrop;
}

void AocLogger::dumpTruth()
{
    // every object of the global environment model; the search area covers the whole map
    static const std::vector<artery::Position> everywhere = {
        artery::Position(-1.0e7, -1.0e7), artery::Position(-1.0e7, 1.0e7), artery::Position(1.0e7, 1.0e7), artery::Position(1.0e7, -1.0e7)};
    const std::string now = T(simTime());
    for (const auto& obj : mGem->preselectObjects("", everywhere)) {
        const artery::Position& c = obj->getCentrePoint();
        std::string speed, heading, type;
        if (auto traciObj = std::dynamic_pointer_cast<artery::TraCIEnvironmentModelObject>(obj)) {
            const artery::VehicleDataProvider& vdp = traciObj->getVehicleData();
            speed = F(mps(vdp.speed()));
            heading = F(degrees(vdp.heading()), 2);
            type = std::to_string(static_cast<int>(vdp.getStationType()));
        }
        mTruth << now << ',' << obj->getExternalId() << ',' << F(c.x.value()) << ',' << F(c.y.value()) << ',' << speed << ',' << heading << ','
               << F(obj->getLength().value(), 2) << ',' << F(obj->getWidth().value(), 2) << ',' << type << '\n';
        ++mRowsTruth;
    }
}

void AocLogger::dumpLocal()
{
    // one row per (vehicle, sensor, object) detected since the previous step
    const SimTime now = simTime();
    const std::string nowText = T(now);
    for (cModule::SubmoduleIterator it(getSystemModule()); !it.end(); ++it) {
        cModule* host = *it;
        auto lem = dynamic_cast<artery::LocalEnvironmentModel*>(host->getSubmodule("environmentModel"));
        if (!lem) {
            continue;
        }
        const Node& n = node(host);
        for (const auto& tracked : lem->allObjects()) {
            auto obj = tracked.first.lock();
            if (!obj) {
                continue;
            }
            const artery::LocalEnvironmentModel::Tracking& tracking = tracked.second;
            for (const auto& entry : tracking.sensors()) {
                const artery::Sensor* sensor = entry.first;
                const artery::LocalEnvironmentModel::TrackingTime& time = entry.second;
                if (!sensor || time.last() <= mLastLocalDump) {
                    continue;
                }
                mLocal << nowText << ',' << n.name << ',' << n.traciId << ',' << sensor->getSensorName() << ',' << sensor->getSensorCategory() << ','
                       << obj->getExternalId() << ',' << tracking.id() << ',' << T(time.first()) << ',' << T(time.last()) << '\n';
                ++mRowsLocal;
            }
        }
    }
    mLastLocalDump = now;
}
D1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.ned" <<'D1_FILE_EOF' || return 1
//
// AocCpService: the collective perception service of Artery, plus a controlled loss of received CPMs.
//
// Each received CPM is dropped with probability dropProbability, before the service reads it.
// The drop uses the random number generator of the module, so a seed set gives the same drops.
// A dropped CPM is announced with the signal CpmDropped, which the AoC logger writes to cpm_drop.csv.
// With dropProbability = 0 the service behaves exactly like CpService.
//
// Used by the single-link validation scenario (task C1): a fixed message period (minInterval = maxInterval,
// withDccRestriction = false, objectInclusionConfig = 0) and a known loss, so that the age law of the
// theory note can be checked against the logs.
//
import artery.envmod.service.CpService;

simple AocCpService extends CpService
{
    parameters:
        @class(artery::AocCpService);
        @signal[CpmDropped](type=artery::CpObject);
        double dropProbability = default(0.0);     // 0 to 1 (excluded): each received CPM is lost with this probability
}
D1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.h" <<'D1_FILE_EOF' || return 1
/*
 * AocCpService: CpService with a controlled loss of received CPMs (task C1 of the AoC plan).
 * Licensed under GPLv2 like Artery.
 */
#ifndef AOC_CP_SERVICE_H_
#define AOC_CP_SERVICE_H_

#include "artery/envmod/service/CpService.h"

namespace artery
{

class AocCpService : public CpService
{
public:
    void initialize() override;
    void finish() override;
    void indicate(const vanetza::btp::DataIndication&, std::unique_ptr<vanetza::UpPacket>) override;

private:
    double mDropProbability = 0.0;
    unsigned long mDropped = 0;
    unsigned long mPassed = 0;
};

}  // namespace artery

#endif /* AOC_CP_SERVICE_H_ */
D1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.cc" <<'D1_FILE_EOF' || return 1
/*
 * AocCpService: CpService with a controlled loss of received CPMs (task C1 of the AoC plan).
 * Licensed under GPLv2 like Artery.
 *
 * The loss is drawn per received CPM, independently, with the random number generator of the
 * module: the same seed set gives the same drops. A dropped CPM never reaches the service, so the
 * receiver behaves exactly as if the radio had lost it. The signal CpmDropped tells the logger.
 */
#include "AocCpService.h"

#include "artery/application/Asn1PacketVisitor.h"
#include "artery/envmod/service/CpObject.h"

#include <omnetpp/cexception.h>

using namespace omnetpp;

namespace artery
{

Define_Module(AocCpService)

static const simsignal_t scSignalCpmDropped = cComponent::registerSignal("CpmDropped");

void AocCpService::initialize()
{
    CpService::initialize();
    mDropProbability = par("dropProbability");
    if (!(mDropProbability >= 0.0 && mDropProbability < 1.0)) {
        throw cRuntimeError("AocCpService: dropProbability must be between 0 (included) and 1 (excluded), not %g", mDropProbability);
    }
}

void AocCpService::finish()
{
    recordScalar("aocCpmDropped", mDropped);
    recordScalar("aocCpmPassed", mPassed);
    CpService::finish();
}

void AocCpService::indicate(const vanetza::btp::DataIndication& ind, std::unique_ptr<vanetza::UpPacket> packet)
{
    Enter_Method("indicate");
    if (mDropProbability > 0.0 && uniform(0.0, 1.0) < mDropProbability) {
        ++mDropped;
        Asn1PacketVisitor<Cpm> visitor;
        const Cpm* cpm = boost::apply_visitor(visitor, *packet);
        if (cpm && cpm->validate()) {
            CpObject obj = visitor.shared_wrapper;
            emit(scSignalCpmDropped, &obj);
        }
        return;
    }
    ++mPassed;
    CpService::indicate(ind, std::move(packet));
}

}  // namespace artery
D1_FILE_EOF
}

# the scenario: the OMNeT++ configuration, the road of C5, the route files of the three densities, the services and the sensors
write_scenario() {
    mkdir -p "$GRID_DIR" || return 1
    put_file "$GRID_DIR/omnetpp.ini" <<'D1_FILE_EOF' || return 1
# Core grid of the plan (task D1, experiment E3): the occluded-VRU intersection with background traffic.
#
# The road, the buildings and the alley are those of the occluded-VRU scenario (C5). On top of it: background
# traffic at three densities on the north, east and west approaches (the south approach carries the event
# vehicles only, so the ego's approach is the same in every run), the scripted event of C5 three times per run
# (at 90, 180 and 270 s, at the start of a light cycle like C5),
# and three factors of the plan:
#   density   low, medium, high (250, 450, 700 vehicles per hour per approach: about 15, 25 and 40 cars on the map)
#   pen       penetration 10, 25, 50, 100 percent: the share of cars that run the CP service (every car runs CA; the
#             egos and the helpers of the events always run CP); services-grid-<pen>.xml
#   msg       the message configuration of the CP service: etsi (the rules of ETSI, with DCC), 2hz and 10hz
#             (a fixed period, every perceived object in every CPM, no DCC restriction)
# 12 repetitions per cell: seeds 0 to 9 are the evaluation runs of the plan (360), seeds 10 and 11 the
# calibration runs for the motion value q (72), kept apart. SUMO draws the traffic from the same seed.
# 330 s per run (three light cycles of 90 s after the warm-up of 60 s, one event per cycle); the analysis leaves out the first 60 s.
#
# Configurations:
#   grid        3 x 4 x 3 x 12 = 432 runs of 330 s
#   grid_quick  one run of 60 s (low, 100, etsi), to check the chain
#
# Run with the AoC library loaded (d1_grid.sh does it, one process per run number):
#   python3 tools/run_artery.py -l build/run-artery.ini -s scenarios/aoc-grid -- -u Cmdenv -c grid -r 0

[General]
network = AocWorld
sim-time-limit = 330s
debug-on-errors = false
print-undisposed = false
cmdenv-express-mode = true
cmdenv-autoflush = true
**.scalar-recording = true
**.vector-recording = false
**.result-recording-modes = all
outputvectormanager-class = "omnetpp::envir::cIndexedFileOutputVectorManager"
seed-set = ${repetition}

# SUMO: cars and pedestrians; the traffic of SUMO is drawn from the repetition too (the launcher passes --seed)
*.traci.core.version = -1
*.traci.launcher.typename = "PosixLauncher"
*.traci.launcher.seed = ${repetition}
*.traci.ignorePersons = false
*.traci.mapper.vehicleType = "artery.envmod.Car"
*.traci.mapper.personType = "artery.envmod.PlainPerson"
num-rngs = 2
*.traci.mapper.rng-0 = 1

# radio (the inet configuration of the Artery example)
*.node[*].wlan[*].typename = "VanetNic"
*.node[*].wlan[*].radio.channelNumber = 180
*.node[*].wlan[*].radio.carrierFrequency = 5.9 GHz
*.node[*].wlan[*].radio.transmitter.power = 200 mW

# middleware
*.node[*].middleware.updateInterval = 0.1s
*.node[*].middleware.datetime = "2013-06-01 12:35:00"
*.node[*].middleware.CP.addSensorInformation = 1s

# environment model: the buildings are the obstacles, the cars and the pedestrians block the lines of sight too
**.identityRegistryModule = "idRegistry"
*.environmentModel.drawObstacles = false
*.environmentModel.drawVehicles = false
*.environmentModel.obstacleTypes = "building"
*.environmentModel.traciModule = "traci"
*.environmentModel.nodeMobilityModule = ".mobility"

# the three sensors of the plan (sensors-vru.xml), as in C5
*.node[*].environmentModel.sensors = xmldoc("sensors-vru.xml")
*.node[*].environmentModel.*.drawSensorCone = false
*.node[*].environmentModel.*.drawLinesOfSight = false
*.node[*].environmentModel.*.drawDetectedObjects = false
*.node[*].environmentModel.*.drawBlockingObstacles = false
*.node[*].environmentModel.Camera.fovRange = 80m
*.node[*].environmentModel.Camera.fovAngle = 120
*.node[*].environmentModel.Camera.numSegments = 12
*.node[*].environmentModel.Radar.fovRange = 150m
*.node[*].environmentModel.Radar.fovAngle = 120
*.node[*].environmentModel.Radar.numSegments = 12
*.node[*].environmentModel.Lidar.fovRange = 100m
*.node[*].environmentModel.Lidar.fovAngle = 360
*.node[*].environmentModel.Lidar.numSegments = 36

# the logger
*.aocLogger.enabled = true
*.aocLogger.logChannelLoad = true

[Config grid]
description = "core grid: density x penetration x message config, 12 seeds"
# the order of the three lines sets the order of the runs: density outermost, then penetration, then message
# config, the repetition innermost (run = ((d * 4 + p) * 3 + m) * 12 + repetition)
*.traci.launcher.sumocfg = "grid_${density=low,medium,high}.sumo.cfg"
*.node[*].middleware.services = xmldoc("services-grid-${pen=10,25,50,100}.xml")
*.aocLogger.label = "${msg=etsi,2hz,10hz}"
*.node[*].middleware.CP.objectInclusionConfig = ${incl=1,0,0 ! msg}
*.node[*].middleware.CP.withDccRestriction = ${dcc=true,false,false ! msg}
*.node[*].middleware.CP.minInterval = ${minI=0.1s,0.5s,0.1s ! msg}
*.node[*].middleware.CP.maxInterval = ${maxI=1s,0.5s,0.1s ! msg}
repeat = 12

[Config grid_quick]
description = "core grid: one short run (low, 100, etsi), to check the chain"
sim-time-limit = 60s
*.traci.launcher.sumocfg = "grid_${density=low}.sumo.cfg"
*.node[*].middleware.services = xmldoc("services-grid-${pen=100}.xml")
*.aocLogger.label = "${msg=etsi}"
*.node[*].middleware.CP.objectInclusionConfig = 1
*.node[*].middleware.CP.withDccRestriction = true
*.node[*].middleware.CP.minInterval = 0.1s
*.node[*].middleware.CP.maxInterval = 1s
D1_FILE_EOF
    put_file "$GRID_DIR/vru.nod.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->
<!-- The centre C is the intersection; S1 is the zebra crossing on the south leg, 45 m before it;
     A is the far end of the alley that comes out at S1 from the east. -->
<nodes>
    <node id="C" x="6.900000" y="47.630000" type="traffic_light"/>
    <node id="N0" x="6.900000" y="47.632695" type="priority"/>
    <node id="S0" x="6.900000" y="47.627305" type="priority"/>
    <node id="E0" x="6.903999" y="47.630000" type="priority"/>
    <node id="W0" x="6.896001" y="47.630000" type="priority"/>
    <node id="S1" x="6.900000" y="47.629596" type="priority"/>
    <node id="A" x="6.900666" y="47.629596" type="priority"/>
</nodes>
D1_FILE_EOF
    put_file "$GRID_DIR/vru.edg.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->
<!-- Two lanes per direction at 50 km/h and a sidewalk on the right of each edge (lane 0);
     the alley is for pedestrians only. -->
<edges>
    <edge id="N0C" from="N0" to="C" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="CN0" from="C" to="N0" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="E0C" from="E0" to="C" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="CE0" from="C" to="E0" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="W0C" from="W0" to="C" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="CW0" from="C" to="W0" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="S0S1" from="S0" to="S1" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="S1S0" from="S1" to="S0" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="S1C" from="S1" to="C" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="CS1" from="C" to="S1" priority="2" numLanes="2" speed="13.89" sidewalkWidth="2.0"/>
    <edge id="alley" from="A" to="S1" priority="1" numLanes="1" width="3.0" speed="1.5" allow="pedestrian" spreadType="center"/>
</edges>
D1_FILE_EOF
    put_file "$GRID_DIR/vru.con.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->
<!-- The zebra crossing at S1: pedestrians have priority, cars must yield. The crossings at C are
     guessed by netconvert (option crossings.guess) and controlled by the traffic light. -->
<connections>
    <crossing node="S1" edges="S0S1 S1S0" priority="true" width="4.0"/>
</connections>
D1_FILE_EOF
    put_file "$GRID_DIR/vru.poly.geo.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->
<!-- A square building at each corner of the intersection (30 m aside, 11 m from the centre line), and two
     buildings along the alley on the east side of the south leg, right behind the sidewalk, which hide the
     alley from the road. Longitude and latitude (geo="true"): polyconvert projects them with the road file. -->
<additional>
    <poly id="building_NE" type="building" color="0.7,0.7,0.7" fill="true" layer="-1" geo="true" shape="6.900147,47.630099 6.900547,47.630099 6.900547,47.630368 6.900147,47.630368 6.900147,47.630099"/>
    <poly id="building_NW" type="building" color="0.7,0.7,0.7" fill="true" layer="-1" geo="true" shape="6.899853,47.630099 6.899453,47.630099 6.899453,47.630368 6.899853,47.630368 6.899853,47.630099"/>
    <poly id="building_SW" type="building" color="0.7,0.7,0.7" fill="true" layer="-1" geo="true" shape="6.899853,47.629901 6.899453,47.629901 6.899453,47.629632 6.899853,47.629632 6.899853,47.629901"/>
    <poly id="building_SE" type="building" color="0.7,0.7,0.7" fill="true" layer="-1" geo="true" shape="6.900147,47.629901 6.900547,47.629901 6.900547,47.629632 6.900147,47.629632 6.900147,47.629901"/>
    <poly id="building_alley_N" type="building" color="0.7,0.7,0.7" fill="true" layer="-1" geo="true" shape="6.900115,47.629614 6.900674,47.629614 6.900674,47.629892 6.900115,47.629892 6.900115,47.629614"/>
    <poly id="building_alley_S" type="building" color="0.7,0.7,0.7" fill="true" layer="-1" geo="true" shape="6.900115,47.629578 6.900674,47.629578 6.900674,47.629308 6.900115,47.629308 6.900115,47.629578"/>
</additional>
D1_FILE_EOF
    put_file "$GRID_DIR/grid_low.rou.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->
<!-- Core grid of the plan (task D1), density low: 250 vehicles per hour on each of the north, east and west approaches,
     random arrivals, 60% straight, 20% right, 20% left; the south approach carries the event vehicles only;
     the event of C5 three times (started at 90, 180, 270 s, the pedestrian out of the alley 16.6 s later). -->
<routes>
    <vType id="car" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="2.6" decel="4.5" minGap="2.5" tau="1.0" lcKeepRight="0" lcSpeedGain="0" lcStrategic="1"/>
    <vType id="flow" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.1"
           sigma="0.5" accel="2.6" decel="4.5" minGap="2.5" tau="1.0"/>
    <vType id="truck" vClass="truck" length="12.0" width="2.5" height="4.0" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="1.0" decel="4.0" minGap="2.5" tau="1.0" guiShape="truck"/>
    <vType id="ped" vClass="pedestrian" length="0.5" width="0.5" height="1.7" maxSpeed="1.4" speedFactor="1.0" speedDev="0.0"/>

    <route id="northbound" edges="S0S1 S1C CN0"/>
    <route id="southbound" edges="N0C CS1 S1S0"/>

    <!-- the background traffic: one route distribution per approach with a flow -->
    <routeDistribution id="from_N">
        <route id="N_S" edges="N0C CS1 S1S0" probability="0.6"/>
        <route id="N_W" edges="N0C CW0" probability="0.2"/>
        <route id="N_E" edges="N0C CE0" probability="0.2"/>
    </routeDistribution>
    <routeDistribution id="from_E">
        <route id="E_W" edges="E0C CW0" probability="0.6"/>
        <route id="E_N" edges="E0C CN0" probability="0.2"/>
        <route id="E_S" edges="E0C CS1 S1S0" probability="0.2"/>
    </routeDistribution>
    <routeDistribution id="from_W">
        <route id="W_E" edges="W0C CE0" probability="0.6"/>
        <route id="W_S" edges="W0C CS1 S1S0" probability="0.2"/>
        <route id="W_N" edges="W0C CN0" probability="0.2"/>
    </routeDistribution>

    <!-- the events of C5 and the background flows, sorted by departure -->
    <flow id="bg_N" type="flow" route="from_N" begin="0" end="330" period="exp(0.06944)" departLane="best" departSpeed="max"/>
    <flow id="bg_E" type="flow" route="from_E" begin="0" end="330" period="exp(0.06944)" departLane="best" departSpeed="max"/>
    <flow id="bg_W" type="flow" route="from_W" begin="0" end="330" period="exp(0.06944)" departLane="best" departSpeed="max"/>
    <vehicle id="truck_1" type="truck" route="northbound" depart="61.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_1" type="ped" depart="90.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_1" type="car" route="southbound" depart="90.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_1" type="car" route="northbound" depart="90.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_1" type="car" route="northbound" depart="93.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_1" type="car" route="southbound" depart="95.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="truck_2" type="truck" route="northbound" depart="151.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_2" type="ped" depart="180.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_2" type="car" route="southbound" depart="180.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_2" type="car" route="northbound" depart="180.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_2" type="car" route="northbound" depart="183.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_2" type="car" route="southbound" depart="185.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="truck_3" type="truck" route="northbound" depart="241.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_3" type="ped" depart="270.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_3" type="car" route="southbound" depart="270.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_3" type="car" route="northbound" depart="270.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_3" type="car" route="northbound" depart="273.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_3" type="car" route="southbound" depart="275.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
</routes>
D1_FILE_EOF
    put_file "$GRID_DIR/grid_medium.rou.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->
<!-- Core grid of the plan (task D1), density medium: 450 vehicles per hour on each of the north, east and west approaches,
     random arrivals, 60% straight, 20% right, 20% left; the south approach carries the event vehicles only;
     the event of C5 three times (started at 90, 180, 270 s, the pedestrian out of the alley 16.6 s later). -->
<routes>
    <vType id="car" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="2.6" decel="4.5" minGap="2.5" tau="1.0" lcKeepRight="0" lcSpeedGain="0" lcStrategic="1"/>
    <vType id="flow" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.1"
           sigma="0.5" accel="2.6" decel="4.5" minGap="2.5" tau="1.0"/>
    <vType id="truck" vClass="truck" length="12.0" width="2.5" height="4.0" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="1.0" decel="4.0" minGap="2.5" tau="1.0" guiShape="truck"/>
    <vType id="ped" vClass="pedestrian" length="0.5" width="0.5" height="1.7" maxSpeed="1.4" speedFactor="1.0" speedDev="0.0"/>

    <route id="northbound" edges="S0S1 S1C CN0"/>
    <route id="southbound" edges="N0C CS1 S1S0"/>

    <!-- the background traffic: one route distribution per approach with a flow -->
    <routeDistribution id="from_N">
        <route id="N_S" edges="N0C CS1 S1S0" probability="0.6"/>
        <route id="N_W" edges="N0C CW0" probability="0.2"/>
        <route id="N_E" edges="N0C CE0" probability="0.2"/>
    </routeDistribution>
    <routeDistribution id="from_E">
        <route id="E_W" edges="E0C CW0" probability="0.6"/>
        <route id="E_N" edges="E0C CN0" probability="0.2"/>
        <route id="E_S" edges="E0C CS1 S1S0" probability="0.2"/>
    </routeDistribution>
    <routeDistribution id="from_W">
        <route id="W_E" edges="W0C CE0" probability="0.6"/>
        <route id="W_S" edges="W0C CS1 S1S0" probability="0.2"/>
        <route id="W_N" edges="W0C CN0" probability="0.2"/>
    </routeDistribution>

    <!-- the events of C5 and the background flows, sorted by departure -->
    <flow id="bg_N" type="flow" route="from_N" begin="0" end="330" period="exp(0.12500)" departLane="best" departSpeed="max"/>
    <flow id="bg_E" type="flow" route="from_E" begin="0" end="330" period="exp(0.12500)" departLane="best" departSpeed="max"/>
    <flow id="bg_W" type="flow" route="from_W" begin="0" end="330" period="exp(0.12500)" departLane="best" departSpeed="max"/>
    <vehicle id="truck_1" type="truck" route="northbound" depart="61.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_1" type="ped" depart="90.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_1" type="car" route="southbound" depart="90.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_1" type="car" route="northbound" depart="90.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_1" type="car" route="northbound" depart="93.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_1" type="car" route="southbound" depart="95.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="truck_2" type="truck" route="northbound" depart="151.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_2" type="ped" depart="180.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_2" type="car" route="southbound" depart="180.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_2" type="car" route="northbound" depart="180.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_2" type="car" route="northbound" depart="183.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_2" type="car" route="southbound" depart="185.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="truck_3" type="truck" route="northbound" depart="241.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_3" type="ped" depart="270.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_3" type="car" route="southbound" depart="270.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_3" type="car" route="northbound" depart="270.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_3" type="car" route="northbound" depart="273.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_3" type="car" route="southbound" depart="275.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
</routes>
D1_FILE_EOF
    put_file "$GRID_DIR/grid_high.rou.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->
<!-- Core grid of the plan (task D1), density high: 700 vehicles per hour on each of the north, east and west approaches,
     random arrivals, 60% straight, 20% right, 20% left; the south approach carries the event vehicles only;
     the event of C5 three times (started at 90, 180, 270 s, the pedestrian out of the alley 16.6 s later). -->
<routes>
    <vType id="car" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="2.6" decel="4.5" minGap="2.5" tau="1.0" lcKeepRight="0" lcSpeedGain="0" lcStrategic="1"/>
    <vType id="flow" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.1"
           sigma="0.5" accel="2.6" decel="4.5" minGap="2.5" tau="1.0"/>
    <vType id="truck" vClass="truck" length="12.0" width="2.5" height="4.0" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="1.0" decel="4.0" minGap="2.5" tau="1.0" guiShape="truck"/>
    <vType id="ped" vClass="pedestrian" length="0.5" width="0.5" height="1.7" maxSpeed="1.4" speedFactor="1.0" speedDev="0.0"/>

    <route id="northbound" edges="S0S1 S1C CN0"/>
    <route id="southbound" edges="N0C CS1 S1S0"/>

    <!-- the background traffic: one route distribution per approach with a flow -->
    <routeDistribution id="from_N">
        <route id="N_S" edges="N0C CS1 S1S0" probability="0.6"/>
        <route id="N_W" edges="N0C CW0" probability="0.2"/>
        <route id="N_E" edges="N0C CE0" probability="0.2"/>
    </routeDistribution>
    <routeDistribution id="from_E">
        <route id="E_W" edges="E0C CW0" probability="0.6"/>
        <route id="E_N" edges="E0C CN0" probability="0.2"/>
        <route id="E_S" edges="E0C CS1 S1S0" probability="0.2"/>
    </routeDistribution>
    <routeDistribution id="from_W">
        <route id="W_E" edges="W0C CE0" probability="0.6"/>
        <route id="W_S" edges="W0C CS1 S1S0" probability="0.2"/>
        <route id="W_N" edges="W0C CN0" probability="0.2"/>
    </routeDistribution>

    <!-- the events of C5 and the background flows, sorted by departure -->
    <flow id="bg_N" type="flow" route="from_N" begin="0" end="330" period="exp(0.19444)" departLane="best" departSpeed="max"/>
    <flow id="bg_E" type="flow" route="from_E" begin="0" end="330" period="exp(0.19444)" departLane="best" departSpeed="max"/>
    <flow id="bg_W" type="flow" route="from_W" begin="0" end="330" period="exp(0.19444)" departLane="best" departSpeed="max"/>
    <vehicle id="truck_1" type="truck" route="northbound" depart="61.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_1" type="ped" depart="90.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_1" type="car" route="southbound" depart="90.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_1" type="car" route="northbound" depart="90.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_1" type="car" route="northbound" depart="93.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_1" type="car" route="southbound" depart="95.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="truck_2" type="truck" route="northbound" depart="151.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_2" type="ped" depart="180.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_2" type="car" route="southbound" depart="180.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_2" type="car" route="northbound" depart="180.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_2" type="car" route="northbound" depart="183.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_2" type="car" route="southbound" depart="185.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="truck_3" type="truck" route="northbound" depart="241.6" departLane="1" departPos="0.0" departSpeed="13.89"><stop lane="S0S1_1" endPos="246.8" duration="50"/></vehicle>
    <person id="ped_3" type="ped" depart="270.0" departPos="20.0"><walk from="alley" to="S1S0" arrivalPos="80.0"/></person>
    <vehicle id="helper_a_3" type="car" route="southbound" depart="270.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <vehicle id="helper_b_3" type="car" route="northbound" depart="270.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <vehicle id="ego_3" type="car" route="northbound" depart="273.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c_3" type="car" route="southbound" depart="275.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
</routes>
D1_FILE_EOF
    put_file "$GRID_DIR/grid_low.sumo.cfg" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->
<configuration>
    <input>
        <net-file value="vru.net.xml"/>
        <route-files value="grid_low.rou.xml"/>
        <additional-files value="vru.poly.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <step-length value="0.1"/>
    </time>
    <processing>
        <collision.action value="warn"/>
        <time-to-teleport value="-1"/>
        <pedestrian.model value="striping"/>
    </processing>
    <report>
        <verbose value="false"/>
        <no-step-log value="true"/>
    </report>
</configuration>
D1_FILE_EOF
    put_file "$GRID_DIR/grid_medium.sumo.cfg" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->
<configuration>
    <input>
        <net-file value="vru.net.xml"/>
        <route-files value="grid_medium.rou.xml"/>
        <additional-files value="vru.poly.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <step-length value="0.1"/>
    </time>
    <processing>
        <collision.action value="warn"/>
        <time-to-teleport value="-1"/>
        <pedestrian.model value="striping"/>
    </processing>
    <report>
        <verbose value="false"/>
        <no-step-log value="true"/>
    </report>
</configuration>
D1_FILE_EOF
    put_file "$GRID_DIR/grid_high.sumo.cfg" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_grid_scenario.py. Do not edit by hand. -->
<configuration>
    <input>
        <net-file value="vru.net.xml"/>
        <route-files value="grid_high.rou.xml"/>
        <additional-files value="vru.poly.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <step-length value="0.1"/>
    </time>
    <processing>
        <collision.action value="warn"/>
        <time-to-teleport value="-1"/>
        <pedestrian.model value="striping"/>
    </processing>
    <report>
        <verbose value="false"/>
        <no-step-log value="true"/>
    </report>
</configuration>
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-10.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Core grid (task D1), penetration 10%: every car runs CA (Day-1 equipment, as in C5); a car runs CP with the probability
     0.10, drawn once with the random numbers of the car; the egos and the helpers of the events always run CP. The share of
     cars that send CPMs is the penetration. The name filter matches the whole SUMO id; the truck and the background cars
     take part in the draw. -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
		<filters operator="or">
			<name pattern="ego_[0-9]+|helper_[abc]_[0-9]+" />
			<penetration rate="0.10" />
		</filters>
	</service>
</services>
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-25.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Core grid (task D1), penetration 25%: every car runs CA (Day-1 equipment, as in C5); a car runs CP with the probability
     0.25, drawn once with the random numbers of the car; the egos and the helpers of the events always run CP. The share of
     cars that send CPMs is the penetration. The name filter matches the whole SUMO id; the truck and the background cars
     take part in the draw. -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
		<filters operator="or">
			<name pattern="ego_[0-9]+|helper_[abc]_[0-9]+" />
			<penetration rate="0.25" />
		</filters>
	</service>
</services>
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-50.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Core grid (task D1), penetration 50%: every car runs CA (Day-1 equipment, as in C5); a car runs CP with the probability
     0.50, drawn once with the random numbers of the car; the egos and the helpers of the events always run CP. The share of
     cars that send CPMs is the penetration. The name filter matches the whole SUMO id; the truck and the background cars
     take part in the draw. -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
		<filters operator="or">
			<name pattern="ego_[0-9]+|helper_[abc]_[0-9]+" />
			<penetration rate="0.50" />
		</filters>
	</service>
</services>
D1_FILE_EOF
    put_file "$GRID_DIR/services-grid-100.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Core grid (task D1), penetration 100%: every car runs CA and CP, the truck of the events included (no filter). -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
	</service>
</services>
D1_FILE_EOF
    put_file "$GRID_DIR/sensors-vru.xml" <<'D1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!--
The three sensors of the plan for the occluded-VRU runs (task C5, scenario E1): a camera, a radar and a lidar.
All three are the cone sensor of Artery with a line-of-sight check (the see-through sensor and the CAM
sensor of Artery are not used): a range, an opening angle, a mounting point. Category "Radar" in the logs,
so src = radar in the observations table; the name tells them apart.
The ranges and angles are set in omnetpp.ini:
    Camera   120 degrees, 80 m, at the front
    Radar    120 degrees (plus or minus 60), 150 m, at the front
    Lidar    360 degrees, 100 m
Detection is deterministic in Artery: an object is seen when one of its corners is in the cone with a free
line of sight. A detection probability per weather is not in the simulator; it belongs to the replay.
-->
<sensors>
	<sensor name="Camera" type="artery.envmod.sensor.FrontRadar" />
	<sensor name="Radar" type="artery.envmod.sensor.FrontRadar" />
	<sensor name="Lidar" type="artery.envmod.sensor.RadarSensor" />
</sensors>
D1_FILE_EOF
}

write_exporter() {
    put_file "$EXPORTER" <<'D1_FILE_EOF' || return 1
#!/usr/bin/env python3
"""
A8: export the logs of the AoC logger (CSV) to parquet, with the schema of the AoC plan.

Input: the folder written by the AocLogger module of Artery for one run
       (<result-dir>/aoc-<configname>-<runnumber>), with the files
       truth.csv, local.csv, cpm_tx.csv, cpm_obj.csv, cpm_rx.csv, cbr.csv, nodes.csv, run.csv

Output, in <input>/parquet:
  truth.parquet         true state of every object at every step (0.1 s)
  cpm_tx.parquet        one row per CPM sent
  cpm_obj.parquet       one row per object of each CPM sent, with its true identity
  cpm_rx.parquet        one row per CPM received, matched to the CPM sent
  cpm_drop.parquet      one row per CPM dropped on purpose (AocCpService, task C1), when cpm_drop.csv exists
  local.parquet         one row per (vehicle, sensor, object) detection
  observations.parquet  the table of the AoC plan: one row per object that a vehicle gets,
                        from its own sensors (one row per step) or from a CPM (one row per reception)
  export_summary.json   counts and checks

Frame: x to the east, y to the south, in meters (the frame of Artery and OMNeT++).
Times are simulation times in seconds.

Usage:  python a8_export.py RUN_DIR [--out OUT_DIR] [--weather NAME] [--pdr-window 1.0] [--receivers all|auto:N|id,id,...]
Needs:  pandas and pyarrow.
"""
import argparse
import json
import math
import os
import random
import re
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

EXPORT_VERSION = "1.3"
ACTOR_PATTERN = re.compile(r"(ego|helper_[abc]|truck)(_\d+)?")   # the actors of the scripted events (C5 and the grid of D1)

# station types of ETSI (vanetza::geonet::StationType)
STATION_TYPES = {0: "unknown", 1: "pedestrian", 2: "cyclist", 3: "moped", 4: "motorcycle", 5: "passenger_car", 6: "bus",
                 7: "light_truck", 8: "heavy_truck", 9: "trailer", 10: "special_vehicle", 11: "tram", 15: "rsu"}
# class written in the CPM by CpService: vehicle sub class = traffic participant type, VRU profile number
VRU_PROFILES = {1: "pedestrian", 2: "cyclist", 3: "motorcycle", 4: "animal"}
# sensor category of Artery -> source name in the observations table
SOURCES = {"Radar": "radar", "FoV": "fov", "see-through": "see_through", "CA": "cam_msg"}
# channel busy ratio -> state, limits of the reactive scheme of ETSI TS 102 687 V1.2.1 (Table A.1, informative)
DCC_LIMITS = [0.30, 0.40, 0.50, 0.60]
DCC_STATES = ["relaxed", "active_1", "active_2", "active_3", "restrictive"]

# columns of the observations table, in the order of the plan, then the extra columns
OBS_COLUMNS = [
    ("run_id", "string", "identifier of the simulation run (OMNeT++ run id)"),
    ("seed", "int32", "seed set of the run"),
    ("t_sim", "float64", "time of the row in s: the step for a sensor row, the reception time for a CPM row"),
    ("rx_station", "string", "vehicle that gets the object (SUMO id)"),
    ("src", "string", "source: v2x for a CPM, else the sensor category (radar, fov, see_through, cam_msg)"),
    ("tx_station", "string", "sender of the CPM (SUMO id); empty for a sensor row"),
    ("obj_id", "string", "identifier given by the source: CPM object id, or track id of the local environment model"),
    ("class", "string", "class of the object as given by the source"),
    ("x", "float64", "position east in m"),
    ("y", "float64", "position south in m"),
    ("vx", "float64", "speed east in m/s"),
    ("vy", "float64", "speed south in m/s"),
    ("P11", "float64", "position covariance, xx in m2; empty until the noise model is defined"),
    ("P12", "float64", "position covariance, xy in m2; empty until the noise model is defined"),
    ("P22", "float64", "position covariance, yy in m2; empty until the noise model is defined"),
    ("Pv11", "float64", "speed covariance, xx; empty until the noise model is defined"),
    ("Pv22", "float64", "speed covariance, yy; empty until the noise model is defined"),
    ("exist_score", "float64", "existence score of the source; empty until the evidence model is defined"),
    ("sender_conf", "float64", "confidence declared by the sender, 0 to 1: CPM object perception quality / 15"),
    ("t_meas", "float64", "time of the measurement in s, as declared by the source"),
    ("t_rx", "float64", "time at which the receiver gets the row in s"),
    ("age", "float64", "t_rx minus t_meas in s"),
    ("cbr", "float64", "last channel busy ratio measured by the receiver, 0 to 1"),
    ("dcc_state", "string", "state deduced from cbr with the limits of ETSI TS 102 687 Table A.1"),
    ("pdr_window", "float64", "share of the CPMs of this sender received by this receiver in the last window"),
    ("weather", "string", "weather of the run (option --weather)"),
    ("gt_match_id", "string", "true object behind the row (SUMO id)"),
    # extra columns
    ("sensor", "string", "name of the sensor for a sensor row"),
    ("gt_dist", "float64", "distance in m between the row and the true object at t_pos"),
    ("t_pos", "float64", "time of the true state that the position comes from in s"),
    ("tx_station_id", "int64", "ETSI station id of the sender of the CPM"),
    ("reference_time_ms", "int64", "reference time of the CPM, ms since 2004-01-01"),
    ("meas_src", "string", "where the sender got the measurement of a CPM object from: an on-board sensor category, or cam_msg"),
]


def die(msg):
    print("A8 EXPORT ERROR: " + msg)
    sys.exit(2)


def read_csv(run_dir, name, dtypes):
    path = os.path.join(run_dir, name)
    if not os.path.isfile(path):
        die("the file %s is missing. Was the run made with the network AocWorld?" % path)
    return pd.read_csv(path, dtype=dtypes, keep_default_na=False, na_values=[""])


def velocity(speed, heading_deg):
    """Speed and heading (degrees from north, clockwise) to components east and south."""
    h = np.deg2rad(heading_deg)
    return speed * np.sin(h), -speed * np.cos(h)


def last_at_or_before(sorted_times, t):
    """For each t, the largest value of sorted_times that is <= t (NaN if none)."""
    idx = np.searchsorted(sorted_times, t, side="right") - 1
    out = np.where(idx >= 0, sorted_times[np.clip(idx, 0, None)], np.nan)
    return out


def cpm_class(label):
    if not isinstance(label, str) or not label:
        return ""
    kind, _, num = label.partition(":")
    try:
        n = int(num)
    except ValueError:
        return kind
    if kind == "vehicle":
        return STATION_TYPES.get(n, "vehicle_%d" % n)
    if kind == "vru":
        return VRU_PROFILES.get(n, "vru_%d" % n)
    return kind


def choose_receivers(option, tx, local, nodes, seed):
    """The receivers whose observations are written: None for all, else a set of SUMO ids.

    'auto:N': the actors of the scripted events (every vehicle whose id is 'ego', 'helper_a', 'helper_b', 'helper_c' or
    'truck', with or without a suffix '_<k>'), plus N other vehicles drawn at random (with the seed of the run) among
    those that sent a CPM (the equipped ones); when fewer than N sent, among all vehicles with a middleware. A
    comma-separated list names the receivers directly.
    """
    option = (option or "all").strip()
    if option == "all":
        return None
    if option.startswith("auto:"):
        try:
            n = int(option[5:])
        except ValueError:
            die("--receivers auto:N needs a whole number, not %r" % option[5:])
        candidates = sorted(set(tx["tx_id"].dropna().astype(str))) if len(tx) else []
        actors = sorted({v for v in set(local["node_id"].dropna().astype(str)) | set(nodes["node_id"].dropna().astype(str)) | set(candidates)
                         if ACTOR_PATTERN.fullmatch(v)})
        pool = [v for v in candidates if v not in actors]
        if len(pool) < n:
            pool = sorted({v for v in nodes["node_id"].dropna().astype(str)} - set(actors))
        drawn = random.Random(int(seed)).sample(pool, min(n, len(pool))) if pool else []
        return set(actors) | set(drawn)
    return {v.strip() for v in option.split(",") if v.strip()}


def iteration_variables(text):
    """The iteration variables of OMNeT++ as a dictionary: '$T=0.5s, $p=0.3' gives {'T': '0.5s', 'p': '0.3'}."""
    out = {}
    for part in str(text or "").split(","):
        part = part.strip().lstrip("$")
        if "=" in part:
            name, value = part.split("=", 1)
            out[name.strip()] = value.strip()
    return out


def dcc_state(cbr):
    idx = np.searchsorted(DCC_LIMITS, cbr, side="right")
    out = np.array(DCC_STATES, dtype=object)[np.clip(idx, 0, len(DCC_STATES) - 1)]
    return np.where(np.isnan(cbr), None, out)


def write_parquet(df, path, descriptions=None):
    table = pa.Table.from_pandas(df, preserve_index=False)
    if descriptions:
        fields = []
        for f in table.schema:
            d = descriptions.get(f.name)
            fields.append(f.with_metadata({"description": d}) if d else f)
        table = table.cast(pa.schema(fields, metadata=table.schema.metadata))
    pq.write_table(table, path, compression="zstd")


def main():
    ap = argparse.ArgumentParser(description="Export the AoC logger files of one run to parquet.")
    ap.add_argument("run_dir", help="folder aoc-<configname>-<runnumber> of the run")
    ap.add_argument("--out", default=None, help="output folder (default: RUN_DIR/parquet)")
    ap.add_argument("--weather", default="", help="weather label stored in the observations table")
    ap.add_argument("--pdr-window", type=float, default=1.0, help="window of pdr_window in s (default 1.0)")
    ap.add_argument("--receivers", default="all",
                    help="whose observations to write: 'all' (default), 'auto:N' (the actors of the scripted events, ego*, helper_*, truck*, "
                         "plus N other senders of CPMs drawn with the seed of the run) or a comma-separated list of SUMO ids. The other "
                         "tables hold every vehicle. Task D1: the observations of every receiver of a dense run would be far too many rows.")
    args = ap.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        die("%s is not a folder" % run_dir)
    out = args.out or os.path.join(run_dir, "parquet")
    os.makedirs(out, exist_ok=True)

    # ---------------------------------------------------------------- read
    run = read_csv(run_dir, "run.csv", {"key": "string", "value": "string"})
    run = dict(zip(run["key"], run["value"].fillna("")))
    run_id = run.get("run_id", "")
    try:
        seed = int(run.get("seed_set", "0") or 0)
    except ValueError:
        seed = 0
    variables = iteration_variables(run.get("iteration_vars", ""))

    truth = read_csv(run_dir, "truth.csv", {"obj_id": "string", "station_type": "Int64"})
    nodes = read_csv(run_dir, "nodes.csv", {"node": "string", "node_id": "string", "station_id": "Int64", "module": "string"})
    tx = read_csv(run_dir, "cpm_tx.csv", {"tx_node": "string", "tx_id": "string", "station_id": "int64", "reference_time_ms": "int64"})
    obj = read_csv(run_dir, "cpm_obj.csv", {"station_id": "int64", "reference_time_ms": "int64", "object_id": "Int64",
                                            "class_label": "string", "sensor_ids": "string", "object_age_ms": "Int64",
                                            "quality": "Int64", "speed_cms": "Int64", "direction_raw": "Int64"})
    rx = read_csv(run_dir, "cpm_rx.csv", {"rx_node": "string", "rx_id": "string", "station_id": "int64", "reference_time_ms": "int64"})
    local = read_csv(run_dir, "local.csv", {"node": "string", "node_id": "string", "sensor": "string", "category": "string",
                                            "obj_id": "string", "track_id": "int64"})
    cbr = read_csv(run_dir, "cbr.csv", {"node": "string", "node_id": "string"})
    drop = None
    if os.path.isfile(os.path.join(run_dir, "cpm_drop.csv")):
        drop = read_csv(run_dir, "cpm_drop.csv", {"rx_node": "string", "rx_id": "string", "station_id": "int64", "reference_time_ms": "int64"})

    checks = {}
    if truth.empty:
        die("truth.csv has no row: the simulation had no object")

    # ---------------------------------------------------------------- truth
    truth["vx"], truth["vy"] = velocity(truth["speed"].to_numpy(float), truth["heading_deg"].to_numpy(float))
    truth["class"] = truth["station_type"].map(STATION_TYPES).astype("string")
    truth = truth.sort_values(["obj_id", "t"], kind="stable").reset_index(drop=True)
    steps = np.sort(truth["t"].unique())
    # check of the speed components against the displacement between two steps
    g = truth.groupby("obj_id", sort=False)
    dt = g["t"].shift(-1) - truth["t"]
    ok = (dt > 0) & (dt < 0.11)
    ex = ((g["x"].shift(-1) - truth["x"]) / dt - (truth["vx"] + g["vx"].shift(-1)) / 2)[ok]
    ey = ((g["y"].shift(-1) - truth["y"]) / dt - (truth["vy"] + g["vy"].shift(-1)) / 2)[ok]
    err = np.hypot(ex, ey)
    checks["truth_velocity_error_mps_median"] = round(float(err.median()), 4) if len(err) else None
    checks["truth_velocity_error_mps_p95"] = round(float(err.quantile(0.95)), 4) if len(err) else None

    truth_cols = ["t", "obj_id", "x", "y", "speed", "heading_deg", "vx", "vy", "length", "width", "station_type", "class"]
    truth = truth[truth_cols]

    # ---------------------------------------------------------------- CPMs sent
    tx = tx.sort_values("t_tx", kind="stable").reset_index(drop=True)
    tx["ref_lag"] = tx["t_tx"] - tx["t_ref"]
    key = ["station_id", "reference_time_ms"]
    dup = int(tx.duplicated(key).sum())
    checks["cpm_tx_duplicate_keys"] = dup
    if dup:
        tx = tx.drop_duplicates(key, keep="first")

    # ---------------------------------------------------------------- objects of the CPMs
    obj = obj.merge(tx[key + ["t_tx", "t_ref", "tx_id", "ego_x", "ego_y"]], on=key, how="left", validate="many_to_one")
    checks["cpm_obj_without_cpm"] = int(obj["t_tx"].isna().sum())
    obj = obj[obj["t_tx"].notna()].reset_index(drop=True)
    obj["x"] = obj["ego_x"] + obj["x_cm"] / 100.0
    obj["y"] = obj["ego_y"] + obj["y_cm"] / 100.0
    obj["t_meas"] = obj["t_ref"] - obj["meas_delta_ms"] / 1000.0
    # the position written in the CPM is the true position at the last step of the environment model
    obj["t_pos"] = last_at_or_before(steps, obj["t_tx"].to_numpy(float))
    obj["class"] = obj["class_label"].map(cpm_class).astype("string")

    # true object behind each CPM object: the nearest true object at t_pos, the sender excluded
    gt_id = np.full(len(obj), None, dtype=object)
    gt_dist = np.full(len(obj), np.nan)
    gt_heading = np.full(len(obj), np.nan)
    truth_by_t = {t: d for t, d in truth.groupby("t", sort=False)}
    for t_pos, idx in obj.groupby("t_pos", sort=False).indices.items():
        d = truth_by_t.get(t_pos)
        if d is None or d.empty:
            continue
        tid = d["obj_id"].to_numpy(object)
        txy = d[["x", "y"]].to_numpy(float)
        th = d["heading_deg"].to_numpy(float)
        o = obj.iloc[idx]
        oxy = o[["x", "y"]].to_numpy(float)
        dist = np.hypot(oxy[:, None, 0] - txy[None, :, 0], oxy[:, None, 1] - txy[None, :, 1])
        dist[o["tx_id"].to_numpy(object)[:, None] == tid[None, :]] = np.inf
        j = np.argmin(dist, axis=1)
        best = dist[np.arange(len(idx)), j]
        found = np.isfinite(best)
        gt_id[idx[found]] = tid[j[found]]
        gt_dist[idx[found]] = best[found]
        gt_heading[idx[found]] = th[j[found]]
    obj["gt_match_id"] = pd.array(gt_id, dtype="string")
    obj["gt_dist"] = gt_dist
    matched = obj["gt_dist"] <= 0.02
    checks["cpm_obj_rows"] = int(len(obj))
    checks["cpm_obj_matched_within_2cm"] = int(matched.sum())
    checks["cpm_obj_gt_dist_max_m"] = round(float(obj["gt_dist"].max()), 4) if len(obj) else None
    obj.loc[~matched, "gt_match_id"] = pd.NA

    # speed components: the speed value of the CPM, with the true heading of the matched object.
    # The direction written in the CPM by Artery (commit e0ddd8c) is in tenths of a radian instead of
    # tenths of a degree, so it is kept as direction_raw but not used.
    speed = obj["speed_cms"].astype("float64") / 100.0
    obj["vx"], obj["vy"] = velocity(speed.to_numpy(float), np.where(matched, gt_heading, np.nan))
    raw = obj["direction_raw"].astype("float64")
    both = matched & raw.notna()
    if both.any():
        as_rad = np.abs(((raw[both] / 10.0) - np.deg2rad(gt_heading[both.to_numpy()]) + math.pi) % (2 * math.pi) - math.pi)
        as_deg = np.abs(((raw[both] / 10.0) - gt_heading[both.to_numpy()] + 180.0) % 360.0 - 180.0)
        checks["cpm_direction_error_if_tenths_of_radian_median_rad"] = round(float(np.median(as_rad)), 3)
        checks["cpm_direction_error_if_tenths_of_degree_median_deg"] = round(float(np.median(as_deg)), 1)
    obj["sender_conf"] = obj["quality"].astype("float64") / 15.0

    # where the sender got each measurement from: the sensor of the sender whose last detection of the
    # matched object is at the declared measurement time (to the millisecond). An on-board sensor wins
    # over the CAM sensor when both detected the object at that time.
    det = local[["node_id", "obj_id", "category", "t_last"]].copy()
    det["ms"] = np.floor(det["t_last"].to_numpy(float) * 1000.0 + 1e-6).astype("int64")
    det["src"] = det["category"].map(SOURCES).fillna(det["category"])
    det["rank"] = np.where(det["category"] == "CA", 1, 0)
    det = det.sort_values("rank", kind="stable").drop_duplicates(["node_id", "obj_id", "ms"], keep="first")
    obj["_ms"] = np.round(obj["t_meas"].to_numpy(float) * 1000.0).astype("int64")
    origin = obj.merge(det[["node_id", "obj_id", "ms", "src"]], left_on=["tx_id", "gt_match_id", "_ms"],
                       right_on=["node_id", "obj_id", "ms"], how="left")["src"].to_numpy(object)
    obj["meas_src"] = pd.array(origin, dtype="string")
    obj = obj.drop(columns=["_ms"])
    counts = obj["meas_src"].fillna("unknown").value_counts()
    checks["cpm_obj_by_origin"] = {str(k): int(v) for k, v in counts.items()}
    obj["delta_positive"] = obj["meas_delta_ms"] > 0
    checks["cpm_obj_measured_before_reference_time_share"] = round(float(obj["delta_positive"].mean()), 4) if len(obj) else None

    # ---------------------------------------------------------------- CPMs received
    rx = rx.merge(tx[key + ["t_tx", "t_ref", "tx_id", "ego_x", "ego_y", "n_objects"]], on=key, how="left", validate="many_to_one")
    checks["cpm_rx_rows"] = int(len(rx))
    checks["cpm_rx_matched_to_a_sent_cpm"] = int(rx["t_tx"].notna().sum())
    rx["delay"] = rx["t_rx"] - rx["t_tx"]
    rx["age_ref"] = rx["t_rx"] - rx["t_ref"]
    rx["distance"] = np.hypot(rx["rx_x"] - rx["ego_x"], rx["rx_y"] - rx["ego_y"])
    rx = rx.sort_values(["t_rx", "rx_id"], kind="stable").reset_index(drop=True)

    # share of the CPMs of the sender received on this link in the last window
    w = args.pdr_window
    pdr = np.full(len(rx), np.nan)
    tx_times = {k: np.sort(v.to_numpy(float)) for k, v in tx.groupby("tx_id")["t_tx"]}
    for (tx_id, rx_id), idx in rx[rx["tx_id"].notna()].groupby(["tx_id", "rx_id"], sort=False).indices.items():
        rows = rx.index[rx["tx_id"].notna()][idx]
        t_rx = rx.loc[rows, "t_rx"].to_numpy(float)
        t_sent_rx = np.sort(rx.loc[rows, "t_tx"].to_numpy(float))        # sending times of the CPMs received on this link
        sent = tx_times.get(tx_id, np.array([]))
        n_sent = np.searchsorted(sent, t_rx, side="right") - np.searchsorted(sent, t_rx - w, side="right")
        n_got = np.searchsorted(t_sent_rx, t_rx, side="right") - np.searchsorted(t_sent_rx, t_rx - w, side="right")
        pdr[rows] = np.where(n_sent > 0, n_got / np.maximum(n_sent, 1), np.nan)
    rx["pdr_window"] = np.clip(pdr, 0.0, 1.0)

    # ---------------------------------------------------------------- CPMs dropped on purpose (task C1)
    if drop is not None:
        drop = drop.merge(tx[key + ["t_tx", "tx_id"]], on=key, how="left", validate="many_to_one")
        checks["cpm_drop_rows"] = int(len(drop))
        checks["cpm_drop_matched_to_a_sent_cpm"] = int(drop["t_tx"].notna().sum())
        drop = drop.sort_values(["t_drop", "rx_id"], kind="stable").reset_index(drop=True)

    # last channel busy ratio of the receiver
    cbr = cbr.drop_duplicates(["node_id", "t"], keep="last").sort_values("t", kind="stable")

    def with_cbr(df, t_col, id_col):
        if cbr.empty or df.empty:
            df["cbr"] = np.nan
            return df
        order = df[t_col].argsort(kind="stable")
        left = df.iloc[order].copy()
        left["_row"] = order
        merged = pd.merge_asof(left, cbr[["t", "node_id", "cbr"]].rename(columns={"t": "_t_cbr", "node_id": id_col}),
                               left_on=t_col, right_on="_t_cbr", by=id_col, direction="backward")
        merged = merged.sort_values("_row", kind="stable").drop(columns=["_row", "_t_cbr"])
        merged.index = df.index
        return merged

    rx = with_cbr(rx, "t_rx", "rx_id")

    # ---------------------------------------------------------------- local detections
    local = local.merge(truth[["t", "obj_id", "x", "y", "vx", "vy", "class"]], on=["t", "obj_id"], how="left")
    checks["local_rows"] = int(len(local))
    checks["local_rows_with_true_state"] = int(local["x"].notna().sum())
    local["src"] = local["category"].map(SOURCES).fillna(local["category"]).astype("string")
    local = with_cbr(local, "t", "node_id")

    # ---------------------------------------------------------------- the receivers of the observations table (task D1)
    # the local table itself keeps every vehicle; only the observations table is cut down to the receivers
    receivers = choose_receivers(args.receivers, tx, local, nodes, seed)
    if receivers is not None:
        chosen = pd.Series(sorted(receivers), dtype="string")
        local_obs = local[local["node_id"].isin(chosen)]
        rx_obs = rx[rx["rx_id"].isin(chosen)]
        checks["receivers"] = int(len(chosen))
        checks["local_rows_of_receivers"] = int(len(local_obs))
        checks["local_rows_all"] = int(len(local))
    else:
        local_obs, rx_obs = local, rx

    # ---------------------------------------------------------------- observations
    n_loc = len(local_obs)
    obs_local = pd.DataFrame({
        "t_sim": local_obs["t"], "rx_station": local_obs["node_id"], "src": local_obs["src"],
        "tx_station": pd.array([pd.NA] * n_loc, dtype="string"),
        "obj_id": local_obs["track_id"].astype("string"), "class": local_obs["class"], "x": local_obs["x"], "y": local_obs["y"],
        "vx": local_obs["vx"], "vy": local_obs["vy"], "sender_conf": np.nan, "t_meas": local_obs["t_last"], "t_rx": local_obs["t"],
        "cbr": local_obs["cbr"], "pdr_window": np.nan, "gt_match_id": local_obs["obj_id"], "sensor": local_obs["sensor"],
        "gt_dist": np.where(local_obs["x"].notna(), 0.0, np.nan), "t_pos": local_obs["t"],
        "tx_station_id": pd.array([pd.NA] * n_loc, dtype="Int64"), "reference_time_ms": pd.array([pd.NA] * n_loc, dtype="Int64"),
        "meas_src": local_obs["src"],
    })
    v2x = rx_obs[rx_obs["t_tx"].notna()].merge(
        obj[key + ["object_id", "class", "x", "y", "vx", "vy", "sender_conf", "t_meas", "t_pos", "gt_match_id", "gt_dist", "meas_src"]],
        on=key, how="inner")
    obs_v2x = pd.DataFrame({
        "t_sim": v2x["t_rx"], "rx_station": v2x["rx_id"], "src": pd.array(["v2x"] * len(v2x), dtype="string"), "tx_station": v2x["tx_id"],
        "obj_id": v2x["object_id"].astype("string"), "class": v2x["class"], "x": v2x["x"], "y": v2x["y"],
        "vx": v2x["vx"], "vy": v2x["vy"], "sender_conf": v2x["sender_conf"], "t_meas": v2x["t_meas"], "t_rx": v2x["t_rx"],
        "cbr": v2x["cbr"], "pdr_window": v2x["pdr_window"], "gt_match_id": v2x["gt_match_id"],
        "sensor": pd.array([pd.NA] * len(v2x), dtype="string"), "gt_dist": v2x["gt_dist"], "t_pos": v2x["t_pos"],
        "tx_station_id": v2x["station_id"].astype("Int64"), "reference_time_ms": v2x["reference_time_ms"].astype("Int64"),
        "meas_src": v2x["meas_src"],
    })
    obs = pd.concat([obs_local, obs_v2x], ignore_index=True)
    obs["run_id"] = pd.array([run_id] * len(obs), dtype="string")
    obs["seed"] = np.int32(seed)
    for c in ("P11", "P12", "P22", "Pv11", "Pv22", "exist_score"):
        obs[c] = np.nan
    obs["age"] = obs["t_rx"] - obs["t_meas"]
    obs["dcc_state"] = pd.array(dcc_state(obs["cbr"].to_numpy(float)), dtype="string")
    obs["weather"] = pd.array([args.weather or pd.NA] * len(obs), dtype="string")
    obs = obs.sort_values(["t_sim", "rx_station", "src"], kind="stable").reset_index(drop=True)
    obs = obs[[c for c, _, _ in OBS_COLUMNS]]
    for c, typ, _ in OBS_COLUMNS:
        if typ == "string":
            obs[c] = obs[c].astype("string")
        elif typ == "int64":
            obs[c] = obs[c].astype("Int64")
        elif typ == "int32":
            obs[c] = obs[c].astype("int32")
        else:
            obs[c] = obs[c].astype("float64")

    # ---------------------------------------------------------------- write
    obj_out = obj.drop(columns=["ego_x", "ego_y", "delta_positive"])
    write_parquet(truth, os.path.join(out, "truth.parquet"))
    write_parquet(tx, os.path.join(out, "cpm_tx.parquet"))
    write_parquet(obj_out, os.path.join(out, "cpm_obj.parquet"))
    write_parquet(rx, os.path.join(out, "cpm_rx.parquet"))
    if drop is not None:
        write_parquet(drop, os.path.join(out, "cpm_drop.parquet"))
    write_parquet(local, os.path.join(out, "local.parquet"))
    write_parquet(nodes, os.path.join(out, "nodes.parquet"))
    write_parquet(obs, os.path.join(out, "observations.parquet"), {c: d for c, _, d in OBS_COLUMNS})

    # ---------------------------------------------------------------- summary and verdict
    v2x_rows = int((obs["src"] == "v2x").sum())
    summary = {
        "export_version": EXPORT_VERSION, "logger_version": run.get("logger_version", ""), "run_dir": run_dir, "out_dir": out,
        "run_id": run_id, "config": run.get("config", ""), "seed": seed, "weather": args.weather,
        "iteration_vars": run.get("iteration_vars", ""), "variables": variables,
        "receivers": None if receivers is None else sorted(receivers), "receivers_option": args.receivers,
        "t_first_s": float(steps[0]), "t_last_s": float(steps[-1]), "steps": int(len(steps)),
        "objects_true": int(truth["obj_id"].nunique()), "vehicles_with_middleware": int(nodes["node_id"].nunique()),
        "rows": {"truth": int(len(truth)), "cpm_tx": int(len(tx)), "cpm_obj": int(len(obj)), "cpm_rx": int(len(rx)),
                 "cpm_drop": int(len(drop)) if drop is not None else None,
                 "local": int(len(local)), "observations": int(len(obs)), "observations_v2x": v2x_rows,
                 "observations_local": int(len(obs)) - v2x_rows},
        "objects_per_cpm_mean": round(float(tx["n_objects"].mean()), 2) if len(tx) else None,
        "age_v2x_s": {"median": round(float(obs.loc[obs["src"] == "v2x", "age"].median()), 4) if v2x_rows else None,
                      "p95": round(float(obs.loc[obs["src"] == "v2x", "age"].quantile(0.95)), 4) if v2x_rows else None,
                      "max": round(float(obs.loc[obs["src"] == "v2x", "age"].max()), 4) if v2x_rows else None},
        "sources": {str(k): int(v) for k, v in obs["src"].value_counts().items()},
        "classes_true": {str(k): int(v) for k, v in truth.drop_duplicates("obj_id")["class"].value_counts().items()},
        "checks": checks,
        "size_bytes": {f: os.path.getsize(os.path.join(out, f)) for f in sorted(os.listdir(out)) if f.endswith(".parquet")},
    }
    problems = []
    if len(tx) == 0:
        problems.append("no CPM was sent")
    if len(rx) == 0:
        problems.append("no CPM was received")
    if checks["cpm_rx_matched_to_a_sent_cpm"] < checks["cpm_rx_rows"]:
        problems.append("some received CPMs have no sent CPM")
    if drop is not None and checks["cpm_drop_matched_to_a_sent_cpm"] < checks["cpm_drop_rows"]:
        problems.append("some dropped CPMs have no sent CPM")
    if checks["cpm_obj_rows"] and checks["cpm_obj_matched_within_2cm"] < 0.999 * checks["cpm_obj_rows"]:
        problems.append("some CPM objects have no true object within 2 cm")
    step_rows = local["category"] != "CA"
    if int(local.loc[step_rows, "x"].isna().sum()) > 0:
        problems.append("some sensor detections have no true state")
    if checks["truth_velocity_error_mps_median"] is not None and checks["truth_velocity_error_mps_median"] > 0.5:
        problems.append("the speed components do not agree with the displacement")
    summary["problems"] = problems
    summary["result"] = "PASS" if not problems else "FAIL"
    with open(os.path.join(out, "export_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    r = summary["rows"]
    c = checks
    print("A8 EXPORT SUMMARY")
    print("  run:                 %s (config %s, seed set %d)" % (run_id, summary["config"], seed))
    print("  time covered:        %.1f s to %.1f s, %d steps" % (steps[0], steps[-1], len(steps)))
    print("  true objects:        %d (%s)" % (summary["objects_true"], ", ".join("%d %s" % (v, k) for k, v in summary["classes_true"].items())))
    dropped = "" if r["cpm_drop"] is None else ", %d dropped on purpose" % r["cpm_drop"]
    print("  CPMs:                %d sent, %d received, %.1f objects per CPM on average%s"
          % (r["cpm_tx"], r["cpm_rx"], summary["objects_per_cpm_mean"] or 0, dropped))
    if variables:
        print("  variables:           %s" % ", ".join("%s = %s" % kv for kv in variables.items()))
    print("  observations:        %d rows = %d from CPMs + %d from on-board sensors" % (r["observations"], r["observations_v2x"], r["observations_local"]))
    print("  sources:             " + ", ".join("%s %d" % (k, v) for k, v in summary["sources"].items()))
    a = summary["age_v2x_s"]
    print("  age of CPM objects:  median %s s, 95%% below %s s, longest %s s" % (a["median"], a["p95"], a["max"]))
    print("  check, CPM objects matched to a true object within 2 cm: %d of %d (largest distance %s m)"
          % (c["cpm_obj_matched_within_2cm"], c["cpm_obj_rows"], c["cpm_obj_gt_dist_max_m"]))
    print("  check, received CPMs matched to a sent CPM: %d of %d" % (c["cpm_rx_matched_to_a_sent_cpm"], c["cpm_rx_rows"]))
    print("  check, sensor detections with a true state: %d of %d" % (c["local_rows_with_true_state"], c["local_rows"]))
    print("  check, speed components against displacement: median error %s m/s" % c["truth_velocity_error_mps_median"])
    if "cpm_direction_error_if_tenths_of_radian_median_rad" in c:
        print("  note, direction field of the CPM: median error %s rad if read as tenths of a radian, %s deg if read as tenths of a degree"
              % (c["cpm_direction_error_if_tenths_of_radian_median_rad"], c["cpm_direction_error_if_tenths_of_degree_median_deg"]))
    print("  share of CPM objects measured before the reference time: %s" % c["cpm_obj_measured_before_reference_time_share"])
    print("  CPM objects by origin of the measurement: " + ", ".join("%s %d" % (k, v) for k, v in c["cpm_obj_by_origin"].items()))
    total = sum(summary["size_bytes"].values())
    print("  files:               %s (%.1f MB of parquet)" % (out, total / 1e6))
    print("  result:              %s%s" % (summary["result"], "" if not problems else " (" + "; ".join(problems) + ")"))
    return 0 if not problems else 3


if __name__ == "__main__":
    sys.exit(main())
D1_FILE_EOF
    put_file "$CHECKER" <<'D1_FILE_EOF' || return 1
"""The core grid of the plan (task D1, experiment E3): the cells, the run numbers, and the index of the exported runs.

The grid (``artery-ext/grid/omnetpp.ini``, configuration ``grid``):

    density   low, medium, high           250, 450, 700 vehicles per hour on each of the north, east and west approaches
    pen       10, 25, 50, 100             the share of cars that run the CA and CP services, in percent
    msg       etsi, 2hz, 10hz             the message configuration of the CP service
    seed      0 to 11                     0 to 9 are the evaluation runs of the plan, 10 and 11 the calibration runs

3 x 4 x 3 = 36 cells, 12 runs each: 432 runs of 330 s, 360 for the evaluation and 72 for the calibration of the motion
value q. OMNeT++ numbers the runs with the density outermost and the repetition innermost (the order of the
iteration lines of the configuration): run = ((d * 4 + p) * 3 + m) * 12 + seed.

    aoc-grid ROOT [--out index.csv] [--missing missing.txt]

reads the exported runs under ``ROOT`` (the folders ``aoc-grid-<run>`` with a ``parquet/export_summary.json``),
writes one row per run (the cell, the seed, the rows, the receivers, the sizes, the verdict of the exporter), checks
that the cell read from the run's variables is the one its run number says, counts the runs of each cell and lists
the missing run numbers, ready for ``d1_grid.sh --runs``. The exit code is 0 when the 432 runs are there and
exported, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import pandas as pd

DENSITIES = ("low", "medium", "high")
PENETRATIONS = (10, 25, 50, 100)
MESSAGES = ("etsi", "2hz", "10hz")
REPETITIONS = 12
EVAL_SEEDS = tuple(range(10))
CAL_SEEDS = (10, 11)
N_RUNS = len(DENSITIES) * len(PENETRATIONS) * len(MESSAGES) * REPETITIONS
CONFIG = "grid"


def run_number(density: str, pen: int, msg: str, seed: int) -> int:
    """The OMNeT++ run number of a cell and a seed."""
    d, p, m = DENSITIES.index(density), PENETRATIONS.index(int(pen)), MESSAGES.index(msg)
    if not 0 <= int(seed) < REPETITIONS:
        raise ValueError("the seed must be between 0 and %d" % (REPETITIONS - 1))
    return ((d * len(PENETRATIONS) + p) * len(MESSAGES) + m) * REPETITIONS + int(seed)


def cell_of_run(number: int) -> dict:
    """The cell and the seed of a run number."""
    if not 0 <= int(number) < N_RUNS:
        raise ValueError("the run number must be between 0 and %d" % (N_RUNS - 1))
    seed = int(number) % REPETITIONS
    rest = int(number) // REPETITIONS
    m = rest % len(MESSAGES)
    rest //= len(MESSAGES)
    p = rest % len(PENETRATIONS)
    d = rest // len(PENETRATIONS)
    return dict(density=DENSITIES[d], pen=PENETRATIONS[p], msg=MESSAGES[m], seed=seed, kind="calibration" if seed in CAL_SEEDS else "evaluation")


def runs_table() -> pd.DataFrame:
    """Every run of the grid: run number, cell, seed, kind."""
    return pd.DataFrame([dict(run=n, **cell_of_run(n)) for n in range(N_RUNS)])


def seed_runs(seeds) -> list[int]:
    """The run numbers of the given seeds in every cell (for example the seeds 0 and 1 of every cell: 72 runs)."""
    wanted = {int(s) for s in seeds}
    return [n for n in range(N_RUNS) if n % REPETITIONS in wanted]


def cell_runs(density: str, pen: int, msg: str) -> list[int]:
    """The 12 run numbers of a cell."""
    return [run_number(density, pen, msg, seed) for seed in range(REPETITIONS)]


def cell_from_variables(variables: dict) -> dict | None:
    """The cell written by the exporter from the iteration variables of the run (None when one is missing)."""
    try:
        density, pen, msg = str(variables["density"]), int(variables["pen"]), str(variables["msg"])
    except (KeyError, ValueError, TypeError):
        return None
    if density not in DENSITIES or pen not in PENETRATIONS or msg not in MESSAGES:
        return None
    return dict(density=density, pen=pen, msg=msg)


def _number_of_folder(name: str) -> int | None:
    m = re.fullmatch(r"aoc-%s-(\d+)" % CONFIG, name)
    return int(m.group(1)) if m else None


def index_runs(root: str | os.PathLike) -> pd.DataFrame:
    """One row per run folder ``aoc-grid-<n>`` under ``root``: exported or not, with its cell and its numbers."""
    root = os.fspath(root)
    exit_codes, export_codes = {}, {}
    codes_path = os.path.join(root, "exit-codes.txt")
    if os.path.isfile(codes_path):                       # one line per run of the script: "run simulation_code export_code", the last one counts
        with open(codes_path, encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    exit_codes[int(parts[0])] = int(parts[1]) if parts[1].lstrip("-").isdigit() else None
                    export_codes[int(parts[0])] = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    rows = []
    seen = set()
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        number = _number_of_folder(name)
        if number is None:
            continue
        seen.add(number)
        folder = os.path.join(root, name)
        expected = cell_of_run(number) if number < N_RUNS else {}
        row = dict(run=number, folder=folder, exported=False, **{k: expected.get(k) for k in ("density", "pen", "msg", "seed", "kind")},
                   exit_code=exit_codes.get(number), export_code=export_codes.get(number), run_id="", result="", t_last_s=None, steps=None,
                   receivers=None, rows_observations=None, rows_cpm_rx=None, rows_local=None, objects_true=None, parquet_mb=None, cell_matches=None,
                   has_variables=None)
        summary = os.path.join(folder, "parquet", "export_summary.json")
        if os.path.isfile(summary):
            with open(summary, encoding="utf-8") as fh:
                info = json.load(fh)
            row.update(exported=True, run_id=info.get("run_id", ""), result=info.get("result", ""), t_last_s=info.get("t_last_s"),
                       steps=info.get("steps"), receivers=None if info.get("receivers") is None else len(info["receivers"]),
                       rows_observations=info.get("rows", {}).get("observations"), rows_cpm_rx=info.get("rows", {}).get("cpm_rx"),
                       rows_local=info.get("rows", {}).get("local"), objects_true=info.get("objects_true"),
                       parquet_mb=round(sum(info.get("size_bytes", {}).values()) / 1e6, 1))
            cell = cell_from_variables(info.get("variables") or {})
            seed = info.get("seed")
            row["has_variables"] = cell is not None
            if expected:                                 # a run without the three variables is not a run of the grid
                row["cell_matches"] = bool(cell is not None and cell == {k: expected[k] for k in ("density", "pen", "msg")} and seed == expected["seed"])
                if cell is not None and not row["cell_matches"]:
                    row.update(cell)
                    row["seed"] = seed
        rows.append(row)
    for number in range(N_RUNS):
        if number not in seen:
            rows.append(dict(run=number, folder="", exported=False, **cell_of_run(number), exit_code=exit_codes.get(number),
                             export_code=export_codes.get(number), run_id="", result="", t_last_s=None, steps=None, receivers=None,
                             rows_observations=None, rows_cpm_rx=None, rows_local=None, objects_true=None, parquet_mb=None, cell_matches=None,
                             has_variables=None))
    table = pd.DataFrame(rows).sort_values("run").reset_index(drop=True)
    for col in ("exit_code", "export_code", "steps", "receivers", "rows_observations", "rows_cpm_rx", "rows_local", "objects_true", "pen", "seed"):
        table[col] = table[col].astype("Int64")
    return table


def check_grid(table: pd.DataFrame) -> dict:
    """What is done and what is missing: the good runs (exported, PASS, right cell), the missing or failed run numbers,
    the count per cell."""
    in_grid = table["run"] < N_RUNS
    passed = table["exported"] & (table["result"] == "PASS")
    good = table[in_grid & passed & (table["cell_matches"] == True)]  # noqa: E712
    done = set(good["run"])
    missing = sorted(set(range(N_RUNS)) - done)
    no_variables = sorted(table.loc[in_grid & table["exported"] & (table["has_variables"] == False), "run"])  # noqa: E712
    wrong_cell = sorted(table.loc[in_grid & (table["cell_matches"] == False) & (table["has_variables"] == True), "run"])  # noqa: E712
    failed_export = sorted(table.loc[in_grid & table["exported"] & (table["result"] != "PASS"), "run"])
    # a run whose script codes are not 0 and that is not exported with PASS (a run exported again by --check is fine)
    bad_code = (table["exit_code"].fillna(0) != 0) | (table["export_code"].fillna(0) != 0)
    failed_run = sorted(table.loc[in_grid & bad_code & ~passed, "run"])
    stray = sorted(table.loc[~in_grid, "run"])
    per_cell = (good.groupby(["density", "pen", "msg"]).agg(evaluation=("seed", lambda s: int(sum(1 for v in s if v in EVAL_SEEDS))),
                                                          calibration=("seed", lambda s: int(sum(1 for v in s if v in CAL_SEEDS))))
                .reindex(pd.MultiIndex.from_product([DENSITIES, PENETRATIONS, MESSAGES], names=["density", "pen", "msg"]), fill_value=0)
                .reset_index())
    return dict(done=len(done), missing=missing, wrong_cell=wrong_cell, no_variables=no_variables, failed_export=failed_export,
                failed_run=failed_run, stray=stray, per_cell=per_cell, complete=not missing and not wrong_cell and not no_variables)


def format_check(c: dict) -> str:
    lines = ["runs done: %d of %d (%d evaluation, %d calibration)" % (
        c["done"], N_RUNS, int(c["per_cell"]["evaluation"].sum()), int(c["per_cell"]["calibration"].sum()))]
    per_cell = c["per_cell"]
    full = (per_cell["evaluation"] >= len(EVAL_SEEDS)) & (per_cell["calibration"] >= len(CAL_SEEDS))
    started = (per_cell["evaluation"] + per_cell["calibration"]) > 0
    lines.append("cells complete: %d of %d, started: %d" % (int(full.sum()), len(per_cell), int(started.sum())))
    short = per_cell[started & ~full]
    if len(short):
        lines.append("cells started and not complete (evaluation+calibration runs): " + "; ".join(
            "%s/%d/%s %d+%d" % (r.density, r.pen, r.msg, r.evaluation, r.calibration) for r in short.itertuples()))
    if c["failed_run"]:
        lines.append("runs that ended with an error: " + compact(c["failed_run"]))
    if c["failed_export"]:
        lines.append("runs whose export did not pass: " + compact(c["failed_export"]))
    if c["wrong_cell"]:
        lines.append("runs whose variables are not those of their run number (the order of the runs is not the one assumed): " + compact(c["wrong_cell"]))
    if c["no_variables"]:
        lines.append("runs exported without the variables of the grid (density, pen, msg; not runs of the configuration grid?): " + compact(c["no_variables"]))
    if c["stray"]:
        lines.append("run folders with a number outside the grid (ignored): " + compact(c["stray"]))
    if c["missing"]:
        lines.append("missing: %d run(s): %s" % (len(c["missing"]), compact(c["missing"])))
        lines.append("to run them: bash d1_grid.sh --runs " + compact(c["missing"]) + "   (or bash d1_grid.sh alone: it runs what is missing)")
    lines.append("grid: %s" % ("complete" if c["complete"] else "incomplete"))
    return "\n".join(lines)


def compact(numbers: list) -> str:
    """1,2,3,7 as 1-3,7."""
    out, start, prev = [], None, None
    for n in list(numbers) + [None]:
        if start is None:
            start = prev = n
        elif n is not None and n == prev + 1:
            prev = n
        else:
            out.append(str(start) if start == prev else "%d-%d" % (start, prev))
            start = prev = n
    return ",".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Index and check the exported runs of the core grid (task D1).")
    ap.add_argument("root", help="the results folder of the grid (holds the run folders aoc-grid-<n>)")
    ap.add_argument("--out", default=None, help="write the index (one row per run) to this CSV file")
    ap.add_argument("--missing", default=None, help="write the missing run numbers, one per line, to this file")
    ap.add_argument("--list", action="store_true", help="print the table of the 432 runs (run number, cell, seed) and stop")
    args = ap.parse_args(argv)
    if args.list:
        print(runs_table().to_string(index=False))
        return 0
    table = index_runs(args.root)
    c = check_grid(table)
    print(format_check(c))
    if args.out:
        table.to_csv(args.out, index=False)
        print("index: %s" % args.out)
    if args.missing:
        with open(args.missing, "w", encoding="utf-8") as fh:
            fh.write("\n".join(str(n) for n in c["missing"]) + ("\n" if c["missing"] else ""))
        print("missing runs: %s" % args.missing)
    return 0 if c["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
D1_FILE_EOF
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
SRC_CHANGED="$CHANGED"
step_done "sources" "OK" "$( [ "$SRC_CHANGED" = 1 ] && echo "installed in scenarios/aoc" || echo "already up to date" )"
if [ "$SRC_CHANGED" = 1 ] || [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null \
   || ! nm -D "$LIB" 2>/dev/null | grep -q AocCpService; then
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
