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
# AoC extension of Artery: a logger for collective perception experiments (A8)
# and a CP service with a controlled loss of received CPMs (C1).
# Added to the build by one line in scenarios/CMakeLists.txt: add_subdirectory(aoc)
add_artery_feature(aoc AocLogger.cc AocCpService.cc)
target_link_libraries(aoc PUBLIC envmod)
C1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.ned" <<'C1_FILE_EOF' || return 1
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
C1_FILE_EOF
    put_file "$EXT_DIR/AocWorld.ned" <<'C1_FILE_EOF' || return 1
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
C1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.h" <<'C1_FILE_EOF' || return 1
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
C1_FILE_EOF
    put_file "$EXT_DIR/AocLogger.cc" <<'C1_FILE_EOF' || return 1
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
C1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.ned" <<'C1_FILE_EOF' || return 1
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
        @signal[CpmDropped](type=CpObject);
        double dropProbability = default(0.0);     // 0 to 1 (excluded): each received CPM is lost with this probability
}
C1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.h" <<'C1_FILE_EOF' || return 1
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
C1_FILE_EOF
    put_file "$EXT_DIR/AocCpService.cc" <<'C1_FILE_EOF' || return 1
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
C1_FILE_EOF
}

# the scenario: the OMNeT++ configuration, the SUMO files, the services and the sensors
write_scenario() {
    mkdir -p "$LINK_DIR" || return 1
    put_file "$LINK_DIR/omnetpp.ini" <<'C1_FILE_EOF' || return 1
# Single-link validation scenario (task C1 of the AoC plan).
#
# Three cars on a straight road at the same speed (see link.rou.xml). The receiver knows the object only
# through the CPMs of the sender. The CPMs are sent with a fixed period T and each copy is lost with a known
# probability p (AocCpService). The logs let tasks C2 and C3 compare the age at the receiver and E[alpha]
# with the formulas of the theory note.
#
# Configurations:
#   link        the grid of the plan: T in {1, 0.5, 0.2, 0.1} s (1, 2, 5, 10 Hz) x p in {0, 0.1, 0.3, 0.5}, 16 runs of 330 s
#   link_quick  one run of 30 s (T = 0.5 s, p = 0.3), to check the chain
#
# Run with the AoC logger library loaded (c1_link.sh does it):
#   python3 tools/run_artery.py -l build/run-artery.ini -s scenarios/aoc-link --all -j 4 -- -u Cmdenv -c link

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

# SUMO
*.traci.core.version = -1
*.traci.launcher.typename = "PosixLauncher"
*.traci.launcher.sumocfg = "link.sumo.cfg"
*.traci.mapper.vehicleType = "artery.envmod.Car"
num-rngs = 2
*.traci.mapper.rng-0 = 1

# radio (the inet configuration of the Artery example)
*.node[*].wlan[*].typename = "VanetNic"
*.node[*].wlan[*].radio.channelNumber = 180
*.node[*].wlan[*].radio.carrierFrequency = 5.9 GHz
*.node[*].wlan[*].radio.transmitter.power = 200 mW

# middleware and services
*.node[*].middleware.updateInterval = 0.1s
*.node[*].middleware.datetime = "2013-06-01 12:35:00"
*.node[*].middleware.services = xmldoc("services-link.xml")

# the CP service: a fixed period, every perceived object in every CPM, no DCC restriction
*.node[*].middleware.CP.withDccRestriction = false
*.node[*].middleware.CP.objectInclusionConfig = 0
*.node[*].middleware.CP.addSensorInformation = 1s

# environment model and sensors (the radars of the AoC runs, same ranges as the Artery example)
**.identityRegistryModule = "idRegistry"
*.environmentModel.drawObstacles = false
*.environmentModel.drawVehicles = false
*.environmentModel.obstacleTypes = "building shop"
*.environmentModel.traciModule = "traci"
*.environmentModel.nodeMobilityModule = ".mobility"
*.node[*].environmentModel.sensors = xmldoc("sensors-aoc.xml")
*.node[*].environmentModel.*.drawSensorCone = false
*.node[*].environmentModel.*.drawLinesOfSight = false
*.node[*].environmentModel.*.drawDetectedObjects = false
*.node[*].environmentModel.*.drawBlockingObstacles = false
*.node[*].environmentModel.FrontLongRangeRadar.fovRange = 150m
*.node[*].environmentModel.FrontLongRangeRadar.fovAngle = 10
*.node[*].environmentModel.FrontShortRangeRadar.fovRange = 65m
*.node[*].environmentModel.FrontShortRangeRadar.fovAngle = 80
*.node[*].environmentModel.FrontShortRangeRadar.numSegments = 4
*.node[*].environmentModel.RearLongRangeRadar.fovRange = 150m
*.node[*].environmentModel.RearLongRangeRadar.fovAngle = 10
*.node[*].environmentModel.RearShortRangeRadar.fovRange = 65m
*.node[*].environmentModel.RearShortRangeRadar.fovAngle = 80
*.node[*].environmentModel.RearShortRangeRadar.numSegments = 4

# the logger
*.aocLogger.enabled = true
*.aocLogger.logChannelLoad = true

[Config link]
description = "single link, the grid of the plan: period T x loss p"
*.node[*].middleware.CP.minInterval = ${T = 1s, 0.5s, 0.2s, 0.1s}
*.node[*].middleware.CP.maxInterval = ${T}
*.node[*].middleware.CP.dropProbability = ${p = 0, 0.1, 0.3, 0.5}
repeat = 1

[Config link_quick]
description = "single link, one short run to check the chain"
sim-time-limit = 30s
*.node[*].middleware.CP.minInterval = 0.5s
*.node[*].middleware.CP.maxInterval = 0.5s
*.node[*].middleware.CP.dropProbability = 0.3
C1_FILE_EOF
    put_file "$LINK_DIR/link.nod.xml" <<'C1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- Two nodes 5 km apart on the same parallel, in longitude and latitude (netconvert projects them with proj.utm). -->
<nodes>
    <node id="west" x="6.8600" y="47.6300" type="priority"/>
    <node id="east" x="6.9267" y="47.6300" type="priority"/>
</nodes>
C1_FILE_EOF
    put_file "$LINK_DIR/link.edg.xml" <<'C1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- One straight road, one lane, from west to east. The cars drive at 10 m/s (their type), below the lane limit. -->
<edges>
    <edge id="road" from="west" to="east" priority="1" numLanes="1" speed="13.89"/>
</edges>
C1_FILE_EOF
    put_file "$LINK_DIR/link.rou.xml" <<'C1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!--
Three cars on one straight road, all at 10 m/s, so the distances between them never change:

    receiver ........ 180 m ........ sender .. 40 m .. object        (direction of travel: east)

The sender sees the object with its front radars (40 m, within 65 m and 150 m) and nothing else.
The object sees the sender with its rear radars and nothing else.
The receiver sees nothing: the sender is 180 m ahead (beyond 150 m), the object 220 m.
So the receiver knows the object only through the CPMs of the sender: one link, one object.
The CPMs of the object about the sender make a second link of the same kind.

The cars enter one second apart so that Artery gives them the modules node[0], node[1], node[2]
in this order: object, sender, receiver. The departure positions take the first seconds into account.
-->
<routes>
    <vType id="car" vClass="passenger" length="4.5" width="1.8" maxSpeed="10.0" speedFactor="1.0" speedDev="0.0" sigma="0.0"
           accel="2.6" decel="4.5" minGap="2.5" tau="1.0"/>
    <route id="eastbound" edges="road"/>
    <vehicle id="object"   type="car" route="eastbound" depart="0.0" departPos="300.0" departSpeed="10.0"/>
    <vehicle id="sender"   type="car" route="eastbound" depart="1.0" departPos="270.0" departSpeed="10.0"/>
    <vehicle id="receiver" type="car" route="eastbound" depart="2.0" departPos="100.0" departSpeed="10.0"/>
</routes>
C1_FILE_EOF
    put_file "$LINK_DIR/link.sumo.cfg" <<'C1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
    <input>
        <net-file value="link.net.xml"/>
        <route-files value="link.rou.xml"/>
    </input>
    <time>
        <begin value="0"/>
        <step-length value="0.1"/>
    </time>
    <processing>
        <collision.action value="warn"/>
        <time-to-teleport value="-1"/>
    </processing>
    <report>
        <verbose value="false"/>
        <no-step-log value="true"/>
    </report>
</configuration>
C1_FILE_EOF
    put_file "$LINK_DIR/services-link.xml" <<'C1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!-- The services of the single-link scenario (task C1): CAMs, and the CP service with a controlled loss. -->
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="AocCpService" name="CP">
		<listener port="2009" />
	</service>
</services>
C1_FILE_EOF
    put_file "$LINK_DIR/sensors-aoc.xml" <<'C1_FILE_EOF' || return 1
<?xml version="1.0" encoding="UTF-8"?>
<sensors>
	<sensor name="FrontShortRangeRadar" type="artery.envmod.sensor.FrontRadar" />
	<sensor name="FrontLongRangeRadar" type="artery.envmod.sensor.FrontRadar" />
	<sensor name="RearLongRangeRadar" type="artery.envmod.sensor.RearRadar" />
	<sensor name="RearShortRangeRadar" type="artery.envmod.sensor.RearRadar" />
</sensors>
C1_FILE_EOF
}

write_exporter() {
    put_file "$EXPORTER" <<'C1_FILE_EOF' || return 1
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
C1_FILE_EOF
    put_file "$CHECKER" <<'C1_FILE_EOF' || return 1
"""The single-link runs (task C1): read the grid of runs and check each run against its period and its loss.

The single-link scenario (``artery-ext/link``) puts three cars on a straight road at the same speed. The
receiver knows the object only through the CPMs of the sender. The CPMs are sent with a fixed period T and each
copy is lost with probability p (``AocCpService``). The grid of the plan is T in {1, 0.5, 0.2, 0.1} s and
p in {0, 0.1, 0.3, 0.5}: 16 runs. This module reads the exported runs and checks that each one is what its
settings say, before tasks C2 and C3 compare them with the formulas of the theory note.

    aoc-link ROOT                    # every run folder under ROOT with an export_summary.json
    aoc-link ROOT --grid             # also checks that the 16 runs of the plan are all there, once each

The checks of one run:
  - the period: the median time between two CPMs of a sender is T, within 1%;
  - the loss: the share of the copies dropped, among the copies that arrived or were dropped, is p within
    three standard errors (plus 0.005 for the rounding);
  - the receiver got the object: at least one received row about an object that the receiver's own sensors
    never saw, and these rows are at least 90% of what the sender sent after the receiver's first reception;
  - the single link: in the scenario of the plan the receiver's own sensors see nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

GRID_T = (1.0, 0.5, 0.2, 0.1)
GRID_P = (0.0, 0.1, 0.3, 0.5)


def parse_time(text) -> float | None:
    """'0.5s' or '500ms' or '0.5' to seconds."""
    if text is None:
        return None
    text = str(text).strip()
    for unit, factor in (("ms", 1e-3), ("us", 1e-6), ("s", 1.0)):
        if text.endswith(unit):
            try:
                return float(text[:-len(unit)]) * factor
            except ValueError:
                return None
    try:
        return float(text)
    except ValueError:
        return None


def find_runs(root: str | os.PathLike) -> list[str]:
    """The folders under ``root`` (itself included) that hold an export_summary.json, in order of name."""
    root = os.fspath(root)
    found = []
    for folder, _, files in os.walk(root):
        if "export_summary.json" in files:
            found.append(folder)
    return sorted(found)


def read_link_run(folder: str | os.PathLike) -> dict:
    """The numbers of one exported run that the single-link check needs. Missing tables give None."""
    folder = os.fspath(folder)
    with open(os.path.join(folder, "export_summary.json"), encoding="utf-8") as fh:
        info = json.load(fh)
    variables = info.get("variables") or {}
    out = dict(folder=folder, run_id=info.get("run_id", ""), config=info.get("config", ""), seed=info.get("seed"),
               T=parse_time(variables.get("T")), p=None if variables.get("p") is None else float(variables["p"]))

    def table(name):
        path = os.path.join(folder, name + ".parquet")
        return pd.read_parquet(path) if os.path.isfile(path) else None

    tx, rx, drop, obs = table("cpm_tx"), table("cpm_rx"), table("cpm_drop"), table("observations")
    out["cpm_sent"] = None if tx is None else int(len(tx))
    out["cpm_received"] = None if rx is None else int(len(rx))
    out["cpm_dropped"] = None if drop is None else int(len(drop))
    # the period: median gap between two CPMs of one sender
    out["period_median"] = None
    if tx is not None and len(tx) > 1:
        gaps = tx.sort_values("t_tx").groupby("tx_id")["t_tx"].diff().dropna().to_numpy(dtype=float)
        out["period_median"] = round(float(np.median(gaps)), 4) if len(gaps) else None
    # the loss: dropped among (received + dropped)
    out["loss_share"] = None
    if rx is not None and drop is not None and len(rx) + len(drop) > 0:
        out["loss_share"] = round(len(drop) / (len(rx) + len(drop)), 4)
    # the delay of a copy
    out["delay_median"] = None
    if rx is not None and len(rx) and "delay" in rx.columns:
        out["delay_median"] = round(float(np.nanmedian(rx["delay"].to_numpy(dtype=float))), 4)
    # what the receivers got about the objects they do not see themselves
    out["links"] = out["object_rows"] = out["age_median"] = out["object_share"] = None
    out["receiver_sees"] = None
    if obs is not None and len(obs):
        is_v2x = (obs["src"] == "v2x").to_numpy(dtype=bool, na_value=False)
        own = obs[~is_v2x]
        seen = set(zip(own["rx_station"].astype(object), own["gt_match_id"].astype(object)))
        v2x = obs[is_v2x]
        v2x = v2x[(v2x["gt_match_id"] != v2x["rx_station"]).to_numpy(dtype=bool, na_value=True)]            # rows about the receiver itself
        unseen = v2x[[(r, o) not in seen for r, o in zip(v2x["rx_station"].astype(object), v2x["gt_match_id"].astype(object))]]
        out["object_rows"] = int(len(unseen))
        out["links"] = int(unseen.groupby(["tx_station", "rx_station", "gt_match_id"]).ngroups)
        out["age_median"] = round(float(unseen["age"].astype(float).median()), 4) if len(unseen) else None
        if tx is not None and len(unseen) and "t_meas" in unseen.columns:
            # share of the sender's CPMs that reached the receiver, from the first one received to the end
            shares = []
            for (sender, receiver, _), rows in unseen.groupby(["tx_station", "rx_station", "gt_match_id"]):
                first = float(rows["t_meas"].astype(float).min())
                sent = tx[(tx["tx_id"] == sender) & (tx["t_tx"] >= first - 1e-6)]
                if len(sent):
                    shares.append(rows["t_meas"].astype(float).nunique() / len(sent))
            out["object_share"] = round(float(np.mean(shares)), 4) if shares else None
        receivers = set(v2x["rx_station"].astype(object))
        out["receiver_sees"] = int(own["rx_station"].isin(receivers).sum())
    return out


def read_grid(root: str | os.PathLike) -> pd.DataFrame:
    """One row per exported run under ``root``."""
    rows = [read_link_run(folder) for folder in find_runs(root)]
    columns = ["folder", "run_id", "config", "seed", "T", "p", "cpm_sent", "cpm_received", "cpm_dropped", "period_median", "loss_share",
               "delay_median", "links", "object_rows", "object_share", "age_median", "receiver_sees"]
    return pd.DataFrame(rows, columns=columns)


def check_grid(table: pd.DataFrame, grid: bool = False, single_link: bool = False) -> list[str]:
    """The problems of a grid of runs; an empty list is a PASS."""
    problems = []
    if not len(table):
        return ["no exported run found"]
    for _, row in table.iterrows():
        name = os.path.basename(os.path.dirname(row["folder"])) or row["folder"]
        if row["T"] is None or row["p"] is None or np.isnan(row["T"]) or np.isnan(row["p"]):
            problems.append("%s: the variables T and p are not in export_summary.json" % name)
            continue
        if row["period_median"] is None or np.isnan(row["period_median"]):
            problems.append("%s: no CPM sent" % name)
        elif abs(row["period_median"] - row["T"]) > 0.01 * row["T"]:
            problems.append("%s: the CPM period is %.3f s, not %.3f s" % (name, row["period_median"], row["T"]))
        n = (row["cpm_received"] or 0) + (row["cpm_dropped"] or 0)
        if row["cpm_dropped"] is None:
            problems.append("%s: no table of the dropped CPMs" % name)
        elif n == 0:
            problems.append("%s: no CPM arrived" % name)
        else:
            tolerance = 3.0 * np.sqrt(row["p"] * (1.0 - row["p"]) / n) + 0.005
            if abs(row["loss_share"] - row["p"]) > tolerance:
                problems.append("%s: the loss is %.3f, not %.2f (tolerance %.3f)" % (name, row["loss_share"], row["p"], tolerance))
        if not row["object_rows"]:
            problems.append("%s: the receiver got nothing about an object it does not see" % name)
        elif row["object_share"] is not None and not np.isnan(row["object_share"]) and row["object_share"] < 0.9 * (1.0 - row["p"]) - 0.05:
            problems.append("%s: the receiver got only %.0f%% of the sender's CPMs about the object" % (name, 100 * row["object_share"]))
        if single_link and row["receiver_sees"]:
            problems.append("%s: the receiver's own sensors see something (%d rows): not a single link" % (name, row["receiver_sees"]))
    if grid:
        cells = {(round(float(t), 3), round(float(p), 3)) for t, p in zip(table["T"], table["p"]) if t is not None and p is not None}
        wanted = {(t, p) for t in GRID_T for p in GRID_P}
        missing = sorted(wanted - cells)
        if missing:
            problems.append("runs missing from the grid: " + ", ".join("T=%g p=%g" % c for c in missing))
        counts = table.groupby(["T", "p"]).size()
        if (counts > 1).any():
            problems.append("some cells of the grid have more than one run: " + ", ".join("T=%g p=%g" % c for c in counts[counts > 1].index))
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read the single-link runs under a folder and check each one against its period T and its loss p.")
    ap.add_argument("root", help="folder that holds the exported runs (each with an export_summary.json)")
    ap.add_argument("--grid", action="store_true", help="also check that the 16 runs of the plan are all there, once each")
    ap.add_argument("--single-link", action="store_true", help="also check that the receiver's own sensors see nothing")
    ap.add_argument("--out", default=None, help="write the table to this file (.csv or .parquet)")
    args = ap.parse_args(argv)
    if not os.path.isdir(args.root):
        print("ERROR: %s is not a folder" % args.root, file=sys.stderr)
        return 2
    table = read_grid(args.root)
    problems = check_grid(table, grid=args.grid, single_link=args.single_link)
    if args.out:
        if args.out.endswith(".parquet"):
            table.to_parquet(args.out, index=False)
        else:
            table.to_csv(args.out, index=False)
    print("C1 LINK CHECK")
    print("  runs:                %d under %s" % (len(table), os.path.abspath(args.root)))
    print("  %-6s %-5s %7s %7s %7s %9s %7s %8s %8s %8s %6s" % ("T", "p", "sent", "got", "dropped", "period", "loss", "delay", "age", "share", "sees"))
    number = lambda value, digits=3: "none" if value is None or (isinstance(value, float) and np.isnan(value)) else ("%%.%df" % digits) % value
    for _, row in table.sort_values(["T", "p"], ascending=[False, True]).iterrows():
        print("  %-6s %-5s %7s %7s %7s %9s %7s %8s %8s %8s %6s" % (number(row["T"], 1), number(row["p"], 1), row["cpm_sent"], row["cpm_received"],
                                                                  row["cpm_dropped"], number(row["period_median"]), number(row["loss_share"]),
                                                                  number(row["delay_median"]), number(row["age_median"]), number(row["object_share"]),
                                                                  row["receiver_sees"]))
    print("  period: median time between two CPMs of a sender. loss: dropped among the copies that arrived or were dropped.")
    print("  delay: median delay of a copy. age: median age of the received objects the receiver does not see itself.")
    print("  share: of the sender's CPMs that reached the receiver. sees: rows of the receivers' own sensors.")
    print("  result:              %s%s" % ("PASS" if not problems else "FAIL", "" if not problems else "\n    " + "\n    ".join(problems)))
    return 0 if not problems else 3


if __name__ == "__main__":
    sys.exit(main())
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
