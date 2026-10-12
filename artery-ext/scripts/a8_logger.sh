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
# AoC extension of Artery: a logger for collective perception experiments.
# Added to the build by one line in scenarios/CMakeLists.txt: add_subdirectory(aoc)
add_artery_feature(aoc AocLogger.cc)
target_link_libraries(aoc PUBLIC envmod)
A8_FILE_EOF
    put_file "$EXT_DIR/AocLogger.ned" <<'A8_FILE_EOF' || return 1
//
// AocLogger: writes what the AoC replay needs, as CSV files.
//
//  truth.csv    true state of every object at every step of the environment model
//  local.csv    which object each on-board sensor of each vehicle detected, at every step
//  cpm_tx.csv   one row per CPM sent
//  cpm_obj.csv  one row per perceived object of each CPM sent
//  cpm_rx.csv   one row per CPM received
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
A8_FILE_EOF
    put_file "$EXT_DIR/AocWorld.ned" <<'A8_FILE_EOF' || return 1
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
A8_FILE_EOF
    put_file "$EXT_DIR/AocLogger.h" <<'A8_FILE_EOF' || return 1
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

    // CpmSent, CpmReceived, EnvironmentModel.refresh
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
    std::ofstream mTruth, mLocal, mTx, mObj, mRx, mCbr, mNodesFile;
    unsigned long mRowsTruth = 0, mRowsLocal = 0, mRowsTx = 0, mRowsObj = 0, mRowsRx = 0, mRowsCbr = 0;
};

#endif /* AOC_LOGGER_H_ */
A8_FILE_EOF
    put_file "$EXT_DIR/AocLogger.cc" <<'A8_FILE_EOF' || return 1
/*
 * AoC logger for Artery (task A8 of the AoC plan).
 * Licensed under GPLv2 like Artery.
 *
 * The logger is a passive listener at the top of the network:
 *  - "CpmSent" and "CpmReceived" are emitted by artery.envmod.service.CpService
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
const simsignal_t sigRefresh = cComponent::registerSignal("EnvironmentModel.refresh");
const simsignal_t sigChannelLoad = cComponent::registerSignal("ChannelLoad");

const char* const kLoggerVersion = "1.0";

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
        sys->unsubscribe(sigRefresh, this);
        if (sys->isSubscribed(sigChannelLoad, this)) {
            sys->unsubscribe(sigChannelLoad, this);
        }
    }
}

void AocLogger::closeAll()
{
    for (std::ofstream* f : {&mTruth, &mLocal, &mTx, &mObj, &mRx, &mCbr, &mNodesFile}) {
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
A8_FILE_EOF
}

write_exporter() {
    put_file "$EXPORTER" <<'A8_FILE_EOF'
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

EXPORT_VERSION = "1.1"

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
    write_parquet(local, os.path.join(out, "local.parquet"))
    write_parquet(nodes, os.path.join(out, "nodes.parquet"))
    write_parquet(obs, os.path.join(out, "observations.parquet"), {c: d for c, _, d in OBS_COLUMNS})

    # ---------------------------------------------------------------- summary and verdict
    v2x_rows = int((obs["src"] == "v2x").sum())
    summary = {
        "export_version": EXPORT_VERSION, "logger_version": run.get("logger_version", ""), "run_dir": run_dir, "out_dir": out,
        "run_id": run_id, "config": run.get("config", ""), "seed": seed, "weather": args.weather,
        "t_first_s": float(steps[0]), "t_last_s": float(steps[-1]), "steps": int(len(steps)),
        "objects_true": int(truth["obj_id"].nunique()), "vehicles_with_middleware": int(nodes["node_id"].nunique()),
        "rows": {"truth": int(len(truth)), "cpm_tx": int(len(tx)), "cpm_obj": int(len(obj)), "cpm_rx": int(len(rx)),
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
    print("  CPMs:                %d sent, %d received, %.1f objects per CPM on average" % (r["cpm_tx"], r["cpm_rx"], summary["objects_per_cpm_mean"] or 0))
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
A8_FILE_EOF
}

write_services() {
    put_file "$SERVICES" <<'A8_FILE_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<services>
	<service type="artery.application.CaService" name="CA">
		<listener port="2001" />
	</service>
	<service type="artery.envmod.service.CpService" name="CP">
		<listener port="2009" />
	</service>
</services>
A8_FILE_EOF
}

# Sensors for AoC runs. Two sensors of the Artery example are left out:
#  - the see-through sensor: it has no line-of-sight check, so it detects objects behind obstacles
#  - the CAM sensor: it puts every vehicle known from its CAMs into the local model, and so into the CPMs
write_sensors() {
    put_file "$SENSORS_AOC" <<'A8_FILE_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<sensors>
	<sensor name="FrontShortRangeRadar" type="artery.envmod.sensor.FrontRadar" />
	<sensor name="FrontLongRangeRadar" type="artery.envmod.sensor.FrontRadar" />
	<sensor name="RearLongRangeRadar" type="artery.envmod.sensor.RearRadar" />
	<sensor name="RearShortRangeRadar" type="artery.envmod.sensor.RearRadar" />
</sensors>
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
