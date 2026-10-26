#!/usr/bin/env bash
# =============================================================================
# c5_vru.sh  -  Task C5 of the AoC plan
# Runs the occluded-VRU scenario in Artery: a signalized intersection with
# buildings, a truck stopped before a zebra crossing, a pedestrian who comes out
# of an alley and crosses, an ego car that cannot see the pedestrian behind the
# truck, and one to three helper cars that see it and send it in their CPMs.
# The pilot of the plan: helpers in {1, 2, 3} x 2 seeds = 6 runs of 60 s.
# Exports every run to parquet and checks that the occlusion event is in the logs.
#
# What it does, step by step:
#   1. installs the AoC sources in <root>/artery/scenarios/aoc (logger + AocCpService) and builds them
#   2. writes the scenario in <root>/artery/scenarios/aoc-vru, builds its road with netconvert and
#      its buildings with polyconvert
#   3. checks the Python environment of A8 (<root>/aoc-venv)
#   4. runs the 6 runs of the pilot, several at a time
#   5. exports every run to parquet and checks each one (the timeline of the event)
#   6. writes a PASS/FAIL report
#
# It needs the installation of A6 and a run of a8_logger.sh (Python environment).
#
# Usage:
#   bash c5_vru.sh                    the pilot (6 runs of 60 s, a few minutes)
#   bash c5_vru.sh --quick            one run of 45 s with 2 helpers, to check the chain
#   bash c5_vru.sh --check DIR        export again and check the runs of one result folder
#   bash c5_vru.sh --help
#
# Options are set with environment variables, for example:
#   C5_NTFY_TOPIC=my-secret-topic C5_JOBS=4 bash c5_vru.sh
#
#   C5_ROOT         install folder of A6            (default: $HOME/aoc-sim)
#   C5_JOBS         runs in parallel, and compile jobs (default: from CPU and RAM)
#   C5_SIM_TIME     simulated time of a run of the pilot (default: 60s)
#   C5_NTFY_TOPIC   relay topic for the chat notice (default: empty = no relay)
#   C5_NTFY_SERVER  relay server                    (default: https://ntfy.sh)
#
# Written for the A6 and A8 installations on Ubuntu 22.04 (x86_64); its first run is the pilot of C5.
# =============================================================================

C5_SCRIPT_VERSION="1.0"

# ----------------------------------------------------------------------------
# 0. Start from a clean environment (conda and old OMNeT++ paths break the tools).
# ----------------------------------------------------------------------------
if [ -z "${C5_CLEAN_ENV:-}" ]; then
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
        case "$name" in C5_*) keep+=("$name=$value") ;; esac
    done < <(env)
    exec env -i "${keep[@]}" C5_CLEAN_ENV=1 \
        LANG=C.UTF-8 LC_ALL=C.UTF-8 \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        bash "$0" "$@"
fi

set -u
set -o pipefail
umask 022

C5_ROOT="${C5_ROOT:-$HOME/aoc-sim}"
C5_SIM_TIME="${C5_SIM_TIME:-60s}"
C5_NTFY_TOPIC="${C5_NTFY_TOPIC:-}"
C5_NTFY_SERVER="${C5_NTFY_SERVER:-https://ntfy.sh}"

ARTERY_DIR="$C5_ROOT/artery"
BUILD_DIR="$ARTERY_DIR/build"
ENV_FILE="$C5_ROOT/env.sh"
C5_DIR="$C5_ROOT/c5"
LOG_DIR="$C5_DIR/logs"
REPORT="$C5_DIR/c5_report.txt"
EXPORTER="$C5_DIR/c5_export.py"
CHECKER="$C5_DIR/c5_vru_check.py"
VENV="$C5_ROOT/aoc-venv"
EXT_DIR="$ARTERY_DIR/scenarios/aoc"
VRU_DIR="$ARTERY_DIR/scenarios/aoc-vru"
CMAKE_FILE="$ARTERY_DIR/scenarios/CMakeLists.txt"
LIB="$BUILD_DIR/scenarios/aoc/libartery_aoc.so"
CONFIG="vru"
N_RUNS=6

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; N=$'\033[0m'
else G=""; R=""; Y=""; N=""; fi
say()  { printf '%s\n' "[C5 $(date +%H:%M:%S)] $*"; }
warn() { printf '%s\n' "${Y}[C5 $(date +%H:%M:%S)] WARNING: $*${N}"; }
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
    [ -n "$C5_NTFY_TOPIC" ] || return 0
    body="$(printf '%s' "$body" | scrub)"
    body="${body:0:3400}"          # the relay accepts 4096 bytes per message
    if command -v curl >/dev/null 2>&1; then
        printf '%s' "$body" | curl -fsS -m 20 -H "Title: $title" --data-binary @- \
            "$C5_NTFY_SERVER/$C5_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
    elif command -v wget >/dev/null 2>&1; then
        wget -q -T 20 -t 1 -O /dev/null --header="Title: $title" --post-data="$body" \
            "$C5_NTFY_SERVER/$C5_NTFY_TOPIC" >/dev/null 2>&1 && RELAY_OK=1
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
C5_JOBS="${C5_JOBS:-$(default_jobs)}"

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
    put_file "$EXT_DIR/CMakeLists.txt" <<'C5_FILE_EOF' || return 1
# AoC extension of Artery: a logger for collective perception experiments (A8)
# and a CP service with a controlled loss of received CPMs (C1).
# Added to the build by one line in scenarios/CMakeLists.txt: add_subdirectory(aoc)
add_artery_feature(aoc AocLogger.cc AocCpService.cc)
target_link_libraries(aoc PUBLIC envmod)
C5_FILE_EOF
    put_file "$EXT_DIR/AocLogger.ned" <<'C5_FILE_EOF' || return 1
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
}
C5_FILE_EOF
    put_file "$EXT_DIR/AocWorld.ned" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$EXT_DIR/AocLogger.h" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$EXT_DIR/AocLogger.cc" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$EXT_DIR/AocCpService.ned" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$EXT_DIR/AocCpService.h" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$EXT_DIR/AocCpService.cc" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
}

# the scenario: the OMNeT++ configuration, the SUMO files, the services and the sensors
write_scenario() {
    mkdir -p "$VRU_DIR" || return 1
    put_file "$VRU_DIR/omnetpp.ini" <<'C5_FILE_EOF' || return 1
# Occluded-VRU scenario (task C5 of the AoC plan, scenario E1).
#
# A signalized intersection with buildings, a zebra crossing on the south leg, a truck stopped on the right
# lane before the crossing, a pedestrian who comes out of an alley and crosses, an ego car on the left lane
# that cannot see the pedestrian behind the truck, and one to three helper cars that see the pedestrian
# and send it in their CPMs. The road and the buildings: make_vru_scenario.py; who drives where: vru.rou.xml.
#
# Configurations:
#   vru         helpers in {1, 2, 3} x 2 seeds = 6 runs of 60 s (the pilot of the plan: 2 seeds)
#   vru_quick   one run of 45 s with 2 helpers, to check the chain
#
# Run with the AoC library loaded (c5_vru.sh does it, one process per run number):
#   python3 tools/run_artery.py -l build/run-artery.ini -s scenarios/aoc-vru -- -u Cmdenv -c vru -r 0

[General]
network = AocWorld
sim-time-limit = 60s
debug-on-errors = false
print-undisposed = false
cmdenv-express-mode = true
cmdenv-autoflush = true
**.scalar-recording = true
**.vector-recording = false
**.result-recording-modes = all
outputvectormanager-class = "omnetpp::envir::cIndexedFileOutputVectorManager"

# SUMO: cars and pedestrians. A pedestrian is a plain node (no radio), an object for the sensors of the cars.
*.traci.core.version = -1
*.traci.launcher.typename = "PosixLauncher"
*.traci.launcher.sumocfg = "vru.sumo.cfg"
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

# middleware and services: the CP service of Artery with the rules of ETSI (objectInclusionConfig = 1),
# as a car on the road today; which helpers run it is set per configuration below
*.node[*].middleware.updateInterval = 0.1s
*.node[*].middleware.datetime = "2013-06-01 12:35:00"
*.node[*].middleware.CP.addSensorInformation = 1s

# environment model: the buildings are the obstacles, the cars and the pedestrian block the lines of sight too
**.identityRegistryModule = "idRegistry"
*.environmentModel.drawObstacles = false
*.environmentModel.drawVehicles = false
*.environmentModel.obstacleTypes = "building"
*.environmentModel.traciModule = "traci"
*.environmentModel.nodeMobilityModule = ".mobility"

# the three sensors of the plan (sensors-vru.xml): camera 120 degrees / 80 m, radar 120 degrees / 150 m,
# lidar 360 degrees / 100 m; many segments so that the cone is round and not a triangle
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

[Config vru]
description = "occluded VRU: 1, 2 or 3 helpers send CPMs, 2 seeds each"
*.node[*].middleware.services = xmldoc("services-vru${helpers=1,2,3}.xml")
repeat = 2

[Config vru_quick]
description = "occluded VRU: one short run with 2 helpers, to check the chain"
sim-time-limit = 45s
# written as an iteration of one value, so that the logger records helpers in run.csv like the pilot
*.node[*].middleware.services = xmldoc("services-vru${helpers=2}.xml")
C5_FILE_EOF
    put_file "$VRU_DIR/vru.nod.xml" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$VRU_DIR/vru.edg.xml" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$VRU_DIR/vru.con.xml" <<'C5_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->
<!-- The zebra crossing at S1: pedestrians have priority, cars must yield. The crossings at C are
     guessed by netconvert (option crossings.guess) and controlled by the traffic light. -->
<connections>
    <crossing node="S1" edges="S0S1 S1S0" priority="true" width="4.0"/>
</connections>
C5_FILE_EOF
    put_file "$VRU_DIR/vru.poly.geo.xml" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
    put_file "$VRU_DIR/vru.rou.xml" <<'C5_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!--
The occluded-VRU event (task C5, scenario E1 of the plan). The road is written by make_vru_scenario.py:
a four-leg signalized intersection C, two lanes per direction, a sidewalk on the right of each edge (lane 0),
a zebra crossing at S1, 45 m south of C, on the south leg, and an alley for pedestrians that comes out of the
buildings on the east side, right at the crossing.

Who is where (times in s; the pedestrian comes out of the alley at about t = 17 and is on the crossing from
t = 19 to t = 29; the numbers come from a SUMO run of this file and from the lines of sight computed on it):

    truck     stopped on the right car lane of the south leg (S0S1_1), its front 5 m before the zebra crossing.
              It hides the east part of the crossing from the cars that come from the south on the left lane.
    ped       walks west in the alley (hidden by the buildings), comes out on the sidewalk at about t = 17,
              crosses to the west (12.8 m at about 1.25 m/s), then walks south on the west sidewalk.
    ego       comes from the south on the left lane (S0S1_2) at 50 km/h. It is 74 m from the pedestrian when
              the pedestrian comes out of the alley, has a line of sight for about a second, then the truck
              hides the pedestrian for about 2.6 s (from 60 m down to 24 m), and the pedestrian comes out
              from behind the truck in the middle of the right lane, 23 m ahead of the ego.
    helper_a  comes from the north on the left lane. It sees the pedestrian from the alley exit on, stops
              at the crossing and lets the pedestrian pass.
    helper_b  drives about 8 s ahead of the ego on the same lane: it is past the intersection when the
              pedestrian comes out and sees it behind itself (360-degree sensor) until it is 100 m away.
              It slows down at the crossing at t = 13 because SUMO sees the pedestrian coming in the alley.
    helper_c  comes from the north 5 s behind helper_a.
    flows     light traffic on the east-west street, one car every 6 s each way. The corner buildings hide
              the crossing from them until they reach the stop line; from there the lidar sees it.
              They run the CA service only (see the services files), so they send no CPM.

The cars keep their lane (lcKeepRight = 0, lcSpeedGain = 0), have no driver imperfection (sigma = 0) and no
speed deviation, so that the event is the same in every run. The seed of a run changes the radio only.
SUMO makes the cars brake for the pedestrian whatever their sensors see; the perception is in the logs.
Which cars run the CP service is set in omnetpp.ini and the services files: the ego and the configured
helpers only. The truck sees the pedestrian too, from 15 m, but sends no CPM: it is the obstacle, not a helper.
The traffic light program is the one netconvert 1.21 writes: north-south green for the first 37 s, which
the event needs (the pilot checks the timeline, so another program would show up as a FAIL).
-->
<routes>
    <vType id="car" vClass="passenger" length="4.5" width="1.8" height="1.5" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="2.6" decel="4.5" minGap="2.5" tau="1.0" lcKeepRight="0" lcSpeedGain="0" lcStrategic="1"/>
    <vType id="truck" vClass="truck" length="12.0" width="2.5" height="4.0" maxSpeed="13.89" speedFactor="1.0" speedDev="0.0"
           sigma="0.0" accel="1.0" decel="4.0" minGap="2.5" tau="1.0" guiShape="truck"/>
    <vType id="ped" vClass="pedestrian" length="0.5" width="0.5" height="1.7" maxSpeed="1.4" speedFactor="1.0" speedDev="0.0"/>

    <route id="northbound" edges="S0S1 S1C CN0"/>
    <route id="southbound" edges="N0C CS1 S1S0"/>
    <route id="eastbound" edges="W0C CE0"/>
    <route id="westbound" edges="E0C CW0"/>

    <!-- the truck is inserted at its stop position, already standing -->
    <vehicle id="truck" type="truck" route="northbound" depart="0.0" departLane="1" departPos="246.8" departSpeed="0">
        <stop lane="S0S1_1" endPos="246.8" duration="600"/>
    </vehicle>

    <!-- the pedestrian: out of the alley, the crossing, then the west sidewalk towards the south -->
    <person id="ped" type="ped" depart="0.0" departPos="20.0">
        <walk from="alley" to="S1S0" arrivalPos="80.0"/>
    </person>

    <!-- the helpers and the ego, in the order of their departure (SUMO wants the file sorted by departure) -->
    <vehicle id="helper_a" type="car" route="southbound" depart="0.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
    <flow id="east" type="car" route="eastbound" begin="0" end="600" period="6" departLane="best" departSpeed="max"/>
    <vehicle id="helper_b" type="car" route="northbound" depart="0.5" departLane="2" departPos="90.0" departSpeed="13.89"/>
    <flow id="west" type="car" route="westbound" begin="3" end="600" period="6" departLane="best" departSpeed="max"/>
    <vehicle id="ego" type="car" route="northbound" depart="3.5" departLane="2" departPos="0.0" departSpeed="13.89"/>
    <vehicle id="helper_c" type="car" route="southbound" depart="5.0" departLane="2" departPos="40.0" departSpeed="13.89"/>
</routes>
C5_FILE_EOF
    put_file "$VRU_DIR/vru.sumo.cfg" <<'C5_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Written by artery-ext/tools/make_vru_scenario.py. Do not edit by hand. -->
<configuration>
    <input>
        <net-file value="vru.net.xml"/>
        <route-files value="vru.rou.xml"/>
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
C5_FILE_EOF
    put_file "$VRU_DIR/services-vru1.xml" <<'C5_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Occluded-VRU runs (task C5): the CP service runs on the ego and on one helper (helper_a). The other cars (the truck, the other helpers,
     the traffic on the cross street) run CA only, so that the CPMs about the pedestrian come from the configured
     helpers alone. The name filter of Artery matches the whole SUMO id. -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
		<filters>
			<name pattern="ego|helper_a" />
		</filters>
	</service>
</services>
C5_FILE_EOF
    put_file "$VRU_DIR/services-vru2.xml" <<'C5_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Occluded-VRU runs (task C5): the CP service runs on the ego and on two helpers (helper_a, helper_b). The other cars (the truck, the other helpers,
     the traffic on the cross street) run CA only, so that the CPMs about the pedestrian come from the configured
     helpers alone. The name filter of Artery matches the whole SUMO id. -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
		<filters>
			<name pattern="ego|helper_a|helper_b" />
		</filters>
	</service>
</services>
C5_FILE_EOF
    put_file "$VRU_DIR/services-vru3.xml" <<'C5_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Occluded-VRU runs (task C5): the CP service runs on the ego and on the three helpers. The other cars (the truck, the other helpers,
     the traffic on the cross street) run CA only, so that the CPMs about the pedestrian come from the configured
     helpers alone. The name filter of Artery matches the whole SUMO id. -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
		<filters>
			<name pattern="ego|helper_a|helper_b|helper_c" />
		</filters>
	</service>
</services>
C5_FILE_EOF
    put_file "$VRU_DIR/sensors-vru.xml" <<'C5_FILE_EOF' || return 1
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
C5_FILE_EOF
}

write_exporter() {
    put_file "$EXPORTER" <<'C5_FILE_EOF' || return 1
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

Usage:  python a8_export.py RUN_DIR [--out OUT_DIR] [--weather NAME] [--pdr-window 1.0]
Needs:  pandas and pyarrow.
"""
import argparse
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

EXPORT_VERSION = "1.2"

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

    # ---------------------------------------------------------------- observations
    n_loc = len(local)
    obs_local = pd.DataFrame({
        "t_sim": local["t"], "rx_station": local["node_id"], "src": local["src"], "tx_station": pd.array([pd.NA] * n_loc, dtype="string"),
        "obj_id": local["track_id"].astype("string"), "class": local["class"], "x": local["x"], "y": local["y"],
        "vx": local["vx"], "vy": local["vy"], "sender_conf": np.nan, "t_meas": local["t_last"], "t_rx": local["t"],
        "cbr": local["cbr"], "pdr_window": np.nan, "gt_match_id": local["obj_id"], "sensor": local["sensor"],
        "gt_dist": np.where(local["x"].notna(), 0.0, np.nan), "t_pos": local["t"],
        "tx_station_id": pd.array([pd.NA] * n_loc, dtype="Int64"), "reference_time_ms": pd.array([pd.NA] * n_loc, dtype="Int64"),
        "meas_src": local["src"],
    })
    v2x = rx[rx["t_tx"].notna()].merge(
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
C5_FILE_EOF
    put_file "$CHECKER" <<'C5_FILE_EOF' || return 1
"""The occluded-VRU runs (task C5, scenario E1): read the exported runs and check that the event is in the logs.

The scenario (``artery-ext/vru``) is a signalized intersection with buildings. A pedestrian comes out of an
alley and crosses the south leg at a zebra crossing. A truck stopped on the right lane before the crossing
hides the pedestrian from the ego car, which comes from the south on the left lane. One to three helper cars
see the pedestrian and send it in their CPMs. The plan asks for one thing of this task: a pilot of two seeds
runs end to end and the occlusion event is visible in the logs. This module reads the exported runs and
tells, for each one, the timeline of the event and whether it holds:

    aoc-vru ROOT                     # every run folder under ROOT with an export_summary.json
    aoc-vru ROOT --pilot             # also checks that the six runs of the pilot (1, 2, 3 helpers x 2 seeds) are there

The timeline of one run, all read from the logs (no geometry of the scenario is assumed here):
  - the pedestrian: the one object of class pedestrian in the truth;
  - it appears: the first time any car sees it with its own sensors (before, it is in the alley);
  - the ego loses it: the first gap of at least 1.5 s in the ego's own detections of the pedestrian (the
    occlusion comes before the ego passes the pedestrian; when there is no such gap, the longest one is reported);
  - which cars see the pedestrian with their own sensors during that gap, and how often (the truck does too);
  - the CPMs: how many CPM rows about the pedestrian the ego received during the gap, from whom, and the
    age of the first one.

The checks of one run (PASS when all hold):
  - one pedestrian, seen by the ego's own sensors at some time, then lost for at least 1.5 s and at most 6 s
    (the occlusion), with the pedestrian within 100 m when lost and within 40 m when seen again;
  - during the gap, at least one of the configured helpers sees the pedestrian at four steps out of five;
  - during the gap, the ego receives at least one CPM row about the pedestrian; the class of these rows is
    pedestrian;
  - the senders of the CPMs about the pedestrian that the ego received over the run are configured helpers only
    (the truck and the other cars run no CP service; the ego's own CPMs go to the other cars and do not count),
    and a helper whose CP service is off sends no CPM at all.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

HELPERS = ("helper_a", "helper_b", "helper_c")
PILOT_HELPERS = (1, 2, 3)
PILOT_SEEDS = 2
MIN_GAP = 1.5        # s: a shorter gap is not an occlusion, a sensor blink at most
MAX_GAP = 6.0        # s: a longer gap means the ego never had the pedestrian at the crossing
LOST_WITHIN = 100.0  # m: distance ego-pedestrian when the ego loses it (the lidar range)
SEEN_WITHIN = 40.0   # m: distance when the ego sees it again (it comes out from behind the truck)
HELPER_SHARE = 0.8   # share of the steps of the gap at which a helper must see the pedestrian


def find_runs(root: str | os.PathLike) -> list[str]:
    """The folders under ``root`` (itself included) that hold an export_summary.json, in order of name.
    (The same as in ``aoc.analysis.link``; copied so that this file runs alone on the workstation.)"""
    root = os.fspath(root)
    found = []
    for folder, _, files in os.walk(root):
        if "export_summary.json" in files:
            found.append(folder)
    return sorted(found)


def intervals(times: np.ndarray, step: float = 0.1) -> list[tuple[float, float]]:
    """The runs of consecutive times (at most ``step`` apart, with a margin) as (first, last) pairs."""
    times = np.unique(np.asarray(times, dtype=float))
    if times.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(times) > 1.5 * step)
    starts = np.concatenate(([0], cuts + 1))
    ends = np.concatenate((cuts, [times.size - 1]))
    return [(float(times[a]), float(times[b])) for a, b in zip(starts, ends)]


def longest_gap(times: np.ndarray, step: float = 0.1) -> tuple[float, float] | None:
    """The longest hole between two runs of detections: (last time before, first time after), or None."""
    runs = intervals(times, step)
    if len(runs) < 2:
        return None
    gaps = [(runs[i + 1][0] - runs[i][1], runs[i][1], runs[i + 1][0]) for i in range(len(runs) - 1)]
    _, before, after = max(gaps)
    return before, after


def first_gap(times: np.ndarray, min_gap: float, step: float = 0.1) -> tuple[float, float] | None:
    """The first hole of at least ``min_gap`` between two runs of detections, or the longest hole when none is
    that long: (last time before, first time after). None without a hole."""
    runs = intervals(times, step)
    for i in range(len(runs) - 1):
        if runs[i + 1][0] - runs[i][1] >= min_gap:
            return runs[i][1], runs[i + 1][0]
    return longest_gap(times, step)


def distance_at(truth: pd.DataFrame, a: str, b: str, t: float) -> float | None:
    """Distance in m between the true objects ``a`` and ``b`` at the step ``t`` (the nearest step logged)."""
    out = []
    for who in (a, b):
        rows = truth[truth["obj_id"] == who]
        if rows.empty:
            return None
        k = int(np.argmin(np.abs(rows["t"].to_numpy(float) - t)))
        out.append((float(rows["x"].iloc[k]), float(rows["y"].iloc[k])))
    return float(np.hypot(out[0][0] - out[1][0], out[0][1] - out[1][1]))


def read_vru_run(folder: str | os.PathLike, ego: str = "ego") -> dict:
    """The timeline of the occlusion event of one exported run, and the checks. Keys: see ``format_run``."""
    folder = os.fspath(folder)
    with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    variables = info.get("variables") or {}
    helpers = variables.get("helpers")
    out = dict(folder=folder, run_id=info.get("run_id", ""), config=info.get("config", ""), seed=info.get("seed"),
               helpers=None if helpers is None else int(helpers), ego=ego, checks={}, notes=[])
    truth = pd.read_parquet(os.path.join(folder, "truth.parquet"))
    obs = pd.read_parquet(os.path.join(folder, "observations.parquet"))
    tx_path = os.path.join(folder, "cpm_tx.parquet")
    tx = pd.read_parquet(tx_path) if os.path.isfile(tx_path) else None
    checks = out["checks"]

    peds = sorted(truth.loc[truth["class"] == "pedestrian", "obj_id"].dropna().unique().tolist())
    checks["one_pedestrian"] = len(peds) == 1
    if not peds:
        out["notes"].append("no pedestrian in the truth")
        return finish(out)
    ped = peds[0]
    out["pedestrian"] = ped
    ped_truth = truth[truth["obj_id"] == ped]
    out["ped_from"], out["ped_to"] = float(ped_truth["t"].min()), float(ped_truth["t"].max())

    about = obs[obs["gt_match_id"] == ped]
    local = about[about["src"] != "v2x"]
    v2x = about[about["src"] == "v2x"]
    out["appears"] = None if local.empty else float(local["t_sim"].min())
    out["first_seen_by"] = None if local.empty else str(local.sort_values("t_sim")["rx_station"].iloc[0])

    # the ego's own view of the pedestrian
    ego_local = local[local["rx_station"] == ego]
    ego_times = ego_local["t_sim"].to_numpy(float)
    out["ego_first"] = None if ego_local.empty else float(ego_times.min())
    out["ego_first_sensors"] = sorted(ego_local.loc[ego_local["t_sim"] == out["ego_first"], "sensor"].astype(str).unique().tolist()) if not ego_local.empty else []
    out["ego_steps"] = int(np.unique(ego_times).size)
    checks["ego_sees_pedestrian"] = not ego_local.empty
    gap = first_gap(ego_times, MIN_GAP)
    out["lost"], out["seen_again"], out["gap"] = None, None, None
    if gap is not None:
        out["lost"], out["seen_again"] = gap
        out["gap"] = round(gap[1] - gap[0], 3)
        out["lost_distance"] = distance_at(truth, ego, ped, gap[0])
        out["seen_again_distance"] = distance_at(truth, ego, ped, gap[1])
        out["seen_again_sensors"] = sorted(ego_local.loc[ego_local["t_sim"] == gap[1], "sensor"].astype(str).unique().tolist())
    checks["occlusion_gap"] = gap is not None and MIN_GAP <= out["gap"] <= MAX_GAP
    checks["lost_within_range"] = gap is not None and out["lost_distance"] is not None and out["lost_distance"] <= LOST_WITHIN
    checks["seen_again_close"] = gap is not None and out["seen_again_distance"] is not None and out["seen_again_distance"] <= SEEN_WITHIN

    # the cars that see the pedestrian during the gap, and the CPMs the ego receives then
    configured = HELPERS if out["helpers"] is None else HELPERS[:out["helpers"]]
    out["configured_helpers"] = list(configured)
    out["helpers_seeing"] = {}
    out["cpm_in_gap"] = {}
    out["first_cpm_in_gap"] = None
    out["first_cpm_age"] = None
    out["cpm_classes"] = []
    if gap is not None:
        t0, t1 = gap
        steps = np.round(np.arange(t0 + 0.1, t1 - 0.05, 0.1), 1)
        in_gap = local[(local["t_sim"] > t0) & (local["t_sim"] < t1) & (local["rx_station"] != ego)]
        for who, rows in in_gap.groupby("rx_station"):
            seen = np.unique(np.round(rows["t_sim"].to_numpy(float), 1))
            out["helpers_seeing"][str(who)] = (int(np.isin(steps, seen).sum()), int(steps.size))
        rx = v2x[(v2x["rx_station"] == ego) & (v2x["t_rx"] > t0) & (v2x["t_rx"] < t1)]
        out["cpm_in_gap"] = {str(k): int(v) for k, v in rx["tx_station"].value_counts().items()}
        if not rx.empty:
            first = rx.sort_values("t_rx").iloc[0]
            out["first_cpm_in_gap"] = float(first["t_rx"])
            out["first_cpm_age"] = float(first["age"])
            out["first_cpm_from"] = str(first["tx_station"])
            out["cpm_classes"] = sorted(rx["class"].astype(str).unique().tolist())
    checks["helper_sees_in_gap"] = any(n >= HELPER_SHARE * total for who, (n, total) in out["helpers_seeing"].items()
                                       if total and who in configured)
    checks["cpm_received_in_gap"] = bool(out["cpm_in_gap"])
    checks["cpm_class_pedestrian"] = not out["cpm_in_gap"] or out["cpm_classes"] == ["pedestrian"]

    # who sent the CPMs about the pedestrian that the ego received over the run (the ego's own CPMs, received by
    # the other cars, are not counted), and the helpers whose CP service is off
    senders = sorted(v2x.loc[v2x["rx_station"] == ego, "tx_station"].dropna().astype(str).unique().tolist())
    out["senders"] = senders
    out["other_senders"] = [s for s in senders if s not in configured]
    checks["senders_are_helpers"] = not out["other_senders"]
    if out["helpers"] is not None:
        off = HELPERS[out["helpers"]:]
        sent_by_off = [] if tx is None else sorted(tx.loc[tx["tx_id"].isin(off), "tx_id"].astype(str).unique().tolist())
        out["sent_by_off"] = sent_by_off
        checks["helpers_as_configured"] = not sent_by_off
    else:
        out["notes"].append("the run has no 'helpers' variable: the three helpers are taken as configured")
        checks["helpers_as_configured"] = True
    return finish(out)


def finish(out: dict) -> dict:
    out["ok"] = all(out["checks"].values())
    return out


def format_run(r: dict) -> str:
    """The timeline of one run as a few lines."""
    name = os.path.basename(r["folder"].rstrip("/"))
    if name == "parquet":
        name = os.path.basename(os.path.dirname(r["folder"].rstrip("/")))
    head = "%s (helpers = %s, seed %s): %s" % (name, r["helpers"], r["seed"], "PASS" if r["ok"] else "FAIL")
    lines = [head]
    if "pedestrian" not in r:
        lines.append("  no pedestrian in the truth")
        return "\n".join(lines)
    lines.append("  pedestrian %s: in the truth from %.1f to %.1f s; first seen by a car at %s (%s)"
                 % (r["pedestrian"], r["ped_from"], r["ped_to"],
                    "never" if r["appears"] is None else "%.1f s" % r["appears"], r["first_seen_by"] or "-"))
    if r["ego_first"] is None:
        lines.append("  %s: never sees it with its own sensors" % r["ego"])
    else:
        lines.append("  %s: sees it at %.1f s (%s), %d steps in all" % (r["ego"], r["ego_first"], ", ".join(r["ego_first_sensors"]), r["ego_steps"]))
        if r["gap"] is None:
            lines.append("  %s: no gap in its own detections" % r["ego"])
        else:
            lines.append("  %s: loses it at %.1f s (%s m), sees it again at %.1f s (%s m, %s): gap %.1f s"
                         % (r["ego"], r["lost"], fmt_m(r.get("lost_distance")), r["seen_again"], fmt_m(r.get("seen_again_distance")),
                            ", ".join(r.get("seen_again_sensors", [])), r["gap"]))
            if r["helpers_seeing"]:
                seeing = ", ".join("%s (%d of %d steps)" % (k, n, tot) for k, (n, tot) in sorted(r["helpers_seeing"].items()))
                lines.append("  cars that see it during the gap: " + seeing)
            else:
                lines.append("  no other car sees it during the gap")
            if r["cpm_in_gap"]:
                lines.append("  CPM rows about it received by %s during the gap: %s; the first at %.1f s from %s, age %.3f s; class %s"
                             % (r["ego"], ", ".join("%d from %s" % (n, k) for k, n in sorted(r["cpm_in_gap"].items())),
                                r["first_cpm_in_gap"], r["first_cpm_from"], r["first_cpm_age"], ", ".join(r["cpm_classes"]) or "-"))
            else:
                lines.append("  no CPM row about it received by %s during the gap" % r["ego"])
    lines.append("  senders of CPMs about it over the run: %s (configured helpers: %s)"
                 % (", ".join(r["senders"]) or "none", ", ".join(r.get("configured_helpers", []))))
    if r.get("other_senders"):
        lines.append("  senders that are not configured helpers: " + ", ".join(r["other_senders"]))
    if r.get("sent_by_off"):
        lines.append("  helpers with the CP service off that sent CPMs: " + ", ".join(r["sent_by_off"]))
    failed = [k for k, v in r["checks"].items() if not v]
    if failed:
        lines.append("  failed checks: " + ", ".join(failed))
    for note in r["notes"]:
        lines.append("  note: " + note)
    return "\n".join(lines)


def fmt_m(v) -> str:
    return "?" if v is None else "%.0f" % v


def check_pilot(runs: list[dict]) -> tuple[bool, list[str]]:
    """The six runs of the pilot: helpers 1, 2, 3 with two seeds each, once each."""
    problems = []
    for h in PILOT_HELPERS:
        seeds = sorted(set(r["seed"] for r in runs if r["helpers"] == h))
        if len(seeds) < PILOT_SEEDS:
            problems.append("helpers = %d: %d seed(s) instead of %d" % (h, len(seeds), PILOT_SEEDS))
        n = sum(1 for r in runs if r["helpers"] == h)
        if n != len(seeds):
            problems.append("helpers = %d: %d runs for %d seeds (a seed twice)" % (h, n, len(seeds)))
    return not problems, problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the occluded-VRU runs of C5: the timeline of the event in the logs of each run.")
    ap.add_argument("root", help="folder that holds the exported runs (each with an export_summary.json)")
    ap.add_argument("--pilot", action="store_true", help="also check that the six runs of the pilot are there (1, 2, 3 helpers x 2 seeds)")
    ap.add_argument("--ego", default="ego", help="SUMO id of the ego car (default: ego)")
    ap.add_argument("--out", default=None, help="write one row per run to this CSV file")
    args = ap.parse_args(argv)
    folders = find_runs(args.root)
    if not folders:
        print("aoc-vru: no exported run under %s" % args.root)
        return 2
    runs = [read_vru_run(f, ego=args.ego) for f in folders]
    for r in runs:
        print(format_run(r))
    ok = all(r["ok"] for r in runs)
    print("runs: %d, passed: %d" % (len(runs), sum(1 for r in runs if r["ok"])))
    if args.pilot:
        pilot_ok, problems = check_pilot(runs)
        for p in problems:
            print("pilot: " + p)
        print("pilot: %s" % ("the six runs are there" if pilot_ok else "incomplete"))
        ok = ok and pilot_ok
    if args.out:
        rows = []
        for r in runs:
            rows.append({"run_id": r["run_id"], "config": r["config"], "seed": r["seed"], "helpers": r["helpers"], "ok": r["ok"],
                         "pedestrian": r.get("pedestrian"), "appears": r.get("appears"), "ego_first": r.get("ego_first"),
                         "lost": r.get("lost"), "seen_again": r.get("seen_again"), "gap": r.get("gap"),
                         "lost_distance": r.get("lost_distance"), "seen_again_distance": r.get("seen_again_distance"),
                         "helpers_seeing": " ".join(sorted(r.get("helpers_seeing", {}))),
                         "cpm_rows_in_gap": sum(r.get("cpm_in_gap", {}).values()), "first_cpm_age": r.get("first_cpm_age"),
                         "senders": " ".join(r.get("senders", [])), "failed": " ".join(k for k, v in r["checks"].items() if not v)})
        pd.DataFrame(rows).to_csv(args.out, index=False)
        print("table: %s" % args.out)
    print("result: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
C5_FILE_EOF
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
        echo "===== C5 REPORT BEGIN ====="
        echo "result:   $1"
        echo "date:     $(date '+%Y-%m-%d %H:%M:%S %Z')"
        echo "script:   c5_vru.sh $C5_SCRIPT_VERSION"
        versions
        echo "scenario: scenarios/aoc-vru, config $CONFIG, network AocWorld, sim time $SIM_TIME"
        echo "results:  $2"
        echo "steps:"
        printf '%s' "$STEP_LINES"
        [ -n "$3" ] && printf '%s\n' "$3"
        [ -n "$4" ] && printf '%s\n' "$4"
        echo "===== C5 REPORT END ====="
    })"
    if [ -d "$C5_ROOT" ] && mkdir -p "$C5_DIR" 2>/dev/null; then
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
    printf '%s\n' "${R}[C5 $(date +%H:%M:%S)] C5 FAIL: $1${N}"
    printf '%s\n' "[C5 $(date +%H:%M:%S)] What to do: $2"
    relay "C5 FAIL" "$REPORT_TEXT"
    [ -n "$C5_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
    desktop_notify critical "C5 FAIL" "$1"
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
    --quick) CONFIG="vru_quick"; N_RUNS=1; C5_SIM_TIME="45s" ;;
    --check) MODE="check"; CHECK_DIR="${2:-}" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
esac
SIM_TIME="$C5_SIM_TIME"

# export every run of a result folder and check the event in each; prints the check and returns its code
export_and_check() {
    local res="$1" pilot_opt="$2" dir rc=0 n=0 failed=0
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
    "$VENV/bin/python" "$CHECKER" "$res" $pilot_opt --out "$res/c5_vru.csv"
    rc=$?
    return $rc
}

if [ "$MODE" = "check" ]; then
    [ -n "$CHECK_DIR" ] || CHECK_DIR="$(readlink -f "$C5_DIR/latest" 2>/dev/null)"
    [ -n "$CHECK_DIR" ] && [ -d "$CHECK_DIR" ] || { echo "C5: give the result folder of a run of this script."; exit 2; }
    [ -x "$VENV/bin/python" ] || { echo "C5: $VENV is missing. Run 'bash a8_logger.sh' once first."; exit 2; }
    mkdir -p "$C5_DIR" && write_exporter || { echo "C5: cannot write $EXPORTER"; exit 2; }
    PILOT_OPT=""; [ "$(ls -d "$CHECK_DIR"/aoc-* 2>/dev/null | wc -l)" -ge 6 ] && PILOT_OPT="--pilot"
    export_and_check "$CHECK_DIR" "$PILOT_OPT"
    exit $?
fi

say "C5 starts. Install folder: $C5_ROOT. Configuration: $CONFIG ($N_RUNS run(s) of $SIM_TIME)."

# ---- checks before any change
if [ ! -f "$ENV_FILE" ]; then
    finish_fail "The file $ENV_FILE does not exist." "Task A6 is not installed in $C5_ROOT. Run a6_install.sh first, or set C5_ROOT to the right folder." "" ""
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
for tool in cmake python3 sumo netconvert polyconvert; do
    command -v "$tool" >/dev/null 2>&1 || finish_fail "The program '$tool' was not found after loading $ENV_FILE." "Run: bash a6_install.sh --verify" "" ""
done
if ! "$VENV/bin/python" -c 'import pandas, pyarrow, numpy' >/dev/null 2>&1; then
    finish_fail "The Python environment $VENV is missing or incomplete." "Run: bash a8_logger.sh   once (it creates the environment)." "" ""
fi
mkdir -p "$LOG_DIR" || finish_fail "Cannot create $LOG_DIR." "Check the rights of $C5_ROOT." "" ""
relay "C5 START" "C5 started $(date '+%H:%M:%S'). Steps: build the AoC sources, write the scenario, $N_RUNS run(s) of $SIM_TIME, export and check. A second message follows with the result."

# ---- step 1: sources and build
T0=$(date +%s)
CHANGED=0
write_sources || finish_fail "Cannot write the AoC sources to $EXT_DIR." "Check the rights of the Artery folder." "" ""
patch_cmake || finish_fail "Cannot change $CMAKE_FILE." "Check the rights of the Artery folder." "" ""
SRC_CHANGED="$CHANGED"
step_done "sources" "OK" "$( [ "$SRC_CHANGED" = 1 ] && echo "installed in scenarios/aoc" || echo "already up to date" )"
if [ "$SRC_CHANGED" = 1 ] || [ ! -f "$LIB" ] || ! grep -q 'scenarios/aoc' "$BUILD_DIR/run-artery.ini" 2>/dev/null \
   || ! nm -D "$LIB" 2>/dev/null | grep -q AocCpService; then
    say "Building the AoC sources with $C5_JOBS jobs. Logs: $LOG_DIR/cmake.log and build.log"
    if ! ( cd "$ARTERY_DIR" && cmake -S . -B build ) > "$LOG_DIR/cmake.log" 2>&1; then
        step_done "build" "FAILED" "cmake configure"
        finish_fail "CMake could not configure Artery with the AoC sources." "Read $LOG_DIR/cmake.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/cmake.log")"
    fi
    if ! ( cd "$ARTERY_DIR" && cmake --build build --target aoc -j "$C5_JOBS" ) > "$LOG_DIR/build.log" 2>&1; then
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

# ---- step 2: the scenario, its road and its buildings
T0=$(date +%s)
CHANGED=0
write_scenario || finish_fail "Cannot write the scenario to $VRU_DIR." "Check the rights of the Artery folder." "" ""
if [ "$CHANGED" = 1 ] || [ ! -f "$VRU_DIR/vru.net.xml" ] || [ ! -f "$VRU_DIR/vru.poly.xml" ]; then
    if ! ( cd "$VRU_DIR" && netconvert --node-files vru.nod.xml --edge-files vru.edg.xml --connection-files vru.con.xml \
             --proj.utm --crossings.guess --output-file vru.net.xml ) > "$LOG_DIR/netconvert.log" 2>&1; then
        step_done "scenario" "FAILED" "netconvert"
        finish_fail "netconvert could not build the road." "Read $LOG_DIR/netconvert.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/netconvert.log")"
    fi
    if ! ( cd "$VRU_DIR" && polyconvert --net-file vru.net.xml --xml-files vru.poly.geo.xml --output-file vru.poly.xml ) > "$LOG_DIR/polyconvert.log" 2>&1; then
        step_done "scenario" "FAILED" "polyconvert"
        finish_fail "polyconvert could not place the buildings." "Read $LOG_DIR/polyconvert.log. Paste the report in the chat." "" "$(excerpt "$LOG_DIR/polyconvert.log")"
    fi
fi
if ! grep -q 'projParameter="+proj=utm' "$VRU_DIR/vru.net.xml"; then
    step_done "scenario" "FAILED" "no UTM projection in the road file"
    finish_fail "The road file has no UTM projection: Artery could not convert positions to latitude and longitude." "Paste the report in the chat." "" "$(grep -m1 '<location' "$VRU_DIR/vru.net.xml" | cut -c1-300)"
fi
N_CROSS="$(grep -c 'function="crossing"' "$VRU_DIR/vru.net.xml")"
N_POLY="$(grep -c '<poly ' "$VRU_DIR/vru.poly.xml")"
if [ "$N_CROSS" -lt 5 ] || [ "$N_POLY" -lt 6 ]; then
    step_done "scenario" "FAILED" "$N_CROSS crossings, $N_POLY buildings"
    finish_fail "The road has $N_CROSS crossings and $N_POLY buildings; 5 and 6 are expected." "Read $LOG_DIR/netconvert.log and $LOG_DIR/polyconvert.log. Paste the report in the chat." "" ""
fi
step_done "scenario" "OK" "road with $N_CROSS crossings and $N_POLY buildings in scenarios/aoc-vru"
write_exporter || finish_fail "Cannot write $EXPORTER." "Check the rights of $C5_DIR." "" ""

# ---- step 3: the runs, several at a time
STAMP="$(date +%Y%m%d-%H%M%S)"
RES="$C5_DIR/results/$STAMP-$CONFIG"
mkdir -p "$RES" || finish_fail "Cannot create $RES." "Check the rights of $C5_DIR." "" ""
say "Running $N_RUNS run(s) of $SIM_TIME, $C5_JOBS at a time. Logs: $RES/run-N.log"
T0=$(date +%s)
one_run() {
    local n="$1"
    (
        cd "$ARTERY_DIR" || exit 97
        timeout 3600 python3 tools/run_artery.py -l build/run-artery.ini -s scenarios/aoc-vru -- \
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
    sleep 1                      # two runs that start together could pick the same port for SUMO
    if [ "$running" -ge "$C5_JOBS" ]; then wait -n 2>/dev/null || wait; running=$((running - 1)); fi
done
wait
FAILED_RUNS="$(awk '$2 != 0 {print $1}' "$RES/exit-codes.txt" | sort -n | tr '\n' ' ')"
if [ -n "$FAILED_RUNS" ]; then
    FIRST="$(echo "$FAILED_RUNS" | awk '{print $1}')"
    step_done "simulation" "FAILED" "runs $FAILED_RUNS"
    HINT="Read $RES/run-$FIRST.log. "
    if grep -q "NED type\|not found\|no such" "$RES/run-$FIRST.log"; then HINT="${HINT}A module or a file was not found (the AoC library not loaded, or a file of the scenario missing). Paste the report in the chat."
    elif grep -q "Connection refused\|TraCI" "$RES/run-$FIRST.log"; then HINT="${HINT}Artery could not talk to SUMO. Check that 'sumo --version' works after 'source $ENV_FILE'."
    elif grep -qi "person\|pedestrian" "$RES/run-$FIRST.log"; then HINT="${HINT}Something about the pedestrian went wrong (the person node or its mobility). Paste the report in the chat."
    else HINT="${HINT}Paste the report in the chat."; fi
    finish_fail "$(echo "$FAILED_RUNS" | wc -w) run(s) ended with an error." "$HINT" "$RES" "$(grep -v '^[[:space:]]*$' "$RES/run-$FIRST.log" | tail -n 10 | cut -c1-300)"
fi
N_DONE="$(ls -d "$RES"/aoc-* 2>/dev/null | wc -l)"
if [ "$N_DONE" -lt "$N_RUNS" ]; then
    finish_fail "The simulations ran but only $N_DONE of $N_RUNS wrote logs." "Paste the report in the chat." "$RES" "$(tail -n 6 "$RES/run-0.log" | cut -c1-300)"
fi
step_done "simulation" "OK" "$N_DONE runs in $(fmt_dur $(( $(date +%s) - T0 )))"

# ---- step 4: export and check
say "Exporting $N_DONE run(s) to parquet and checking the event in each."
T0=$(date +%s)
PILOT_OPT=""; [ "$CONFIG" = "vru" ] && PILOT_OPT="--pilot"
SUMMARY="$(export_and_check "$RES" "$PILOT_OPT" 2>&1)"
ERC=$?
if [ "$ERC" -ne 0 ]; then
    step_done "export+check" "FAILED" "exit code $ERC"
    finish_fail "The export or the check of the runs failed (exit code $ERC)." "Paste the report in the chat." "$RES" "$(printf '%s\n' "$SUMMARY" | tail -n 30 | cut -c1-300)"
fi
step_done "export+check" "OK" "$(fmt_dur $(( $(date +%s) - T0 )))"
ln -sfn "$RES" "$C5_DIR/latest"

EXTRA="where the files are:"$'\n'"  results:       $RES  (also $C5_DIR/latest)"$'\n'"  one run:       $RES/aoc-$CONFIG-N/parquet"$'\n'"  event table:   $RES/c5_vru.csv"
write_report "PASS" "$RES" "$SUMMARY" "$EXTRA"
echo
printf '%s\n' "$REPORT_TEXT"
printf '%s\n' "${G}[C5 $(date +%H:%M:%S)] C5 PASS. Report: $REPORT${N}"
relay "C5 PASS" "$REPORT_TEXT"
[ -n "$C5_NTFY_TOPIC" ] && { [ "$RELAY_OK" = 1 ] && say "Relay: the result was sent." || warn "Relay: the result could not be sent. Paste the report in the chat."; }
desktop_notify normal "C5 PASS" "The occluded-VRU runs are exported and checked. Report: $REPORT"
exit 0
