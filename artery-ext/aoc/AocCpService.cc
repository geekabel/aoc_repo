/*
 * AocCpService: CpService with the AoC generation rule (task E1 of the AoC plan) and a controlled loss of
 * received CPMs (task C1). Licensed under GPLv2 like Artery.
 *
 * The AoC generation rule (generationRule = "aoc"). The receiver of a report predicts the object forward
 * from the report; the reliability of that prediction is alpha(age) of the theory note, with the motion
 * value q of the object's class and the position and speed errors of the sender. The rule of the note
 * (Step 4, eq. "rule") sends each object with the period T = (age limit - delay) / n, where the age limit
 * is the age at which alpha falls to the target: the reliability at the receivers then stays above the
 * target unless n reports in a row are lost. Here the rule is applied at every generation step of the
 * service: an object is included in the CPM of this step when its report would be older than T at the next
 * evaluation (one update interval away; one generation interval, the DCC interval, once a CPM goes out at this
 * step). The curve is the one of the report the receiver holds (the class and the state sent). A newly seen
 * object is included at once, and so is an object that changed state (stopped, moving) since its last report. A
 * stopped object (speed below stoppedSpeed) takes the curve of a parked vehicle. The period never exceeds maxPeriod: a receiver
 * forgets an object after its memory hold (5 s in the evaluation of the plan), whatever the curve says. With
 * deviationFraction > 0 the sender also
 * includes an object when the receiver's prediction (the last sent position moved at the last sent
 * velocity) is off by that fraction of the tolerance: what the sender knows and the age alone does not say.
 * A CPM is sent only when an object is due or the sensor information is due, and at least every
 * maxInterval (with emptyCpm = true, at every generation step, like CpService).
 *
 * With generationRule = "etsi" the service behaves exactly like CpService (the rules of TS 103 324). With
 * "etsi_abs" it applies the same rules, except that the position change of an object (rule 2b, 4 m) is taken on its
 * absolute position (CpService takes it on the position relative to the sender, so a sender that moves sends every
 * stopped object it sees at every step), and that a CPM goes out only when something is due (emptyCpm as above;
 * the standard allows a generation event to produce no CPM). The control grid compares the AoC rule with both.
 *
 * The loss (dropProbability) is drawn per received CPM, independently, with the random number generator of
 * the module: the same seed set gives the same drops. A dropped CPM never reaches the service, so the
 * receiver behaves exactly as if the radio had lost it. The signal CpmDropped tells the logger.
 */
#include "AocCpService.h"

#include "artery/application/Asn1PacketVisitor.h"
#include "artery/application/Timer.h"
#include "artery/envmod/service/CpObject.h"

#include <boost/units/cmath.hpp>
#include <omnetpp/cexception.h>
#include <vanetza/btp/ports.hpp>
#include <vanetza/dcc/transmission.hpp>
#include <vanetza/dcc/transmit_rate_control.hpp>
#include <vanetza/geonet/station_type.hpp>

#include <algorithm>
#include <cmath>
#include <sstream>

using namespace omnetpp;

namespace artery
{

Define_Module(AocCpService)

static const simsignal_t scSignalCpmDropped = cComponent::registerSignal("CpmDropped");
static const simsignal_t scSignalAocCpmSent = cComponent::registerSignal("CpmSent");

double AocCpService::alphaOfAge(const Curve& c, double age, double eps)
{
    const double variance = c.sigmaX * c.sigmaX + c.sigmaV * c.sigmaV * age * age + c.q * age * age * age / 3.0;
    if (variance <= 0.0) {
        return 1.0;
    }
    return 1.0 - std::exp(-eps * eps / (2.0 * variance));
}

double AocCpService::ageLimit(const Curve& c, double target, double eps)
{
    // alpha(age) decreases with the age: bisection on alpha(age) = target
    if (alphaOfAge(c, 0.0, eps) <= target) {
        return 0.0;
    }
    double lo = 0.0, hi = 1.0;
    while (alphaOfAge(c, hi, eps) > target) {
        hi *= 2.0;
        if (hi > 1.0e6) {
            return hi;  // the curve never reaches the target (no motion at all)
        }
    }
    for (int i = 0; i < 60; ++i) {
        const double mid = 0.5 * (lo + hi);
        if (alphaOfAge(c, mid, eps) > target) {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    return 0.5 * (lo + hi);
}

double AocCpService::period(const Curve& c, double target, double eps, double delay, int repeats, double maxPeriod)
{
    const double limit = ageLimit(c, target, eps);
    return std::min(maxPeriod, std::max(0.0, limit - delay) / std::max(1, repeats));
}

std::string AocCpService::className(int stationType)
{
    // the names of the station types of ETSI, as the exporter of the package writes them
    switch (stationType) {
        case 1: return "pedestrian";
        case 2: return "cyclist";
        case 3: return "moped";
        case 4: return "motorcycle";
        case 5: return "passenger_car";
        case 6: return "bus";
        case 7: return "light_truck";
        case 8: return "heavy_truck";
        case 9: return "trailer";
        case 10: return "special_vehicle";
        case 11: return "tram";
        case 15: return "rsu";
        default: return "unknown";
    }
}

void AocCpService::parseQByClass(const std::string& text)
{
    // "passenger_car=0.27,heavy_truck=0.118,pedestrian=0.025,default=0.27"
    mQByClass.clear();
    std::istringstream in(text);
    std::string item;
    while (std::getline(in, item, ',')) {
        const auto eq = item.find('=');
        if (eq == std::string::npos) {
            throw cRuntimeError("AocCpService: qByClass item '%s' is not class=value", item.c_str());
        }
        std::string name = item.substr(0, eq);
        name.erase(0, name.find_first_not_of(" \t"));
        name.erase(name.find_last_not_of(" \t") + 1);
        double value = 0.0;
        try {
            value = std::stod(item.substr(eq + 1));
        } catch (const std::exception&) {
            throw cRuntimeError("AocCpService: qByClass value of '%s' is not a number", name.c_str());
        }
        if (!(value > 0.0)) {
            throw cRuntimeError("AocCpService: q of class '%s' must be positive, not %g", name.c_str(), value);
        }
        mQByClass[name] = value;
    }
    if (mQByClass.find("default") == mQByClass.end()) {
        throw cRuntimeError("AocCpService: qByClass needs a 'default' entry");
    }
}

bool AocCpService::isStopped(const PerceivedObjectSnapshot& po) const
{
    return mStoppedSpeed > 0.0 && po.hasVelocity && po.speedMps < mStoppedSpeed;
}

void AocCpService::markSent(ObjectMemory& mem, const PerceivedObjectSnapshot& po, const SimTime& T_now, double x, double y) const
{
    mem.included = T_now;
    mem.xi = x;
    mem.yi = y;
    mem.vxi = mem.haveVelocity ? mem.vx : 0.0;
    mem.vyi = mem.haveVelocity ? mem.vy : 0.0;
    mem.stoppedSent = isStopped(po);
    mem.haveIncluded = true;
}

AocCpService::Curve AocCpService::curveOf(const PerceivedObjectSnapshot& po, bool stopped) const
{
    Curve c;
    if (stopped) {
        c.q = mQStopped;
        c.sigmaX = mSigmaXStopped;
        c.sigmaV = mSigmaVStopped;
        return c;
    }
    auto it = mQByClass.find(className(po.stationType));
    c.q = (it != mQByClass.end()) ? it->second : mQByClass.at("default");
    c.sigmaX = mSigmaX;
    c.sigmaV = mSigmaV;
    return c;
}

void AocCpService::initialize()
{
    CpService::initialize();
    mDropProbability = par("dropProbability");
    if (!(mDropProbability >= 0.0 && mDropProbability < 1.0)) {
        throw cRuntimeError("AocCpService: dropProbability must be between 0 (included) and 1 (excluded), not %g", mDropProbability);
    }

    const std::string rule = par("generationRule").stdstringValue();
    mAocRule = false;
    mEtsiAbsolute = false;
    if (rule == "aoc") {
        mAocRule = true;
    } else if (rule == "etsi_abs") {
        mEtsiAbsolute = true;
    } else if (rule != "etsi") {
        throw cRuntimeError("AocCpService: generationRule must be 'etsi', 'etsi_abs' or 'aoc', not '%s'", rule.c_str());
    }
    mTargetAlpha = par("targetAlpha");
    mTolerance = par("tolerance");
    mDelayEstimate = par("delayEstimate");
    mRepeats = par("repeats");
    mSigmaX = par("sigmaX");
    mSigmaV = par("sigmaV");
    mStoppedSpeed = par("stoppedSpeed");
    mQStopped = par("qStopped");
    mSigmaXStopped = par("sigmaXStopped");
    mSigmaVStopped = par("sigmaVStopped");
    mDeviationFraction = par("deviationFraction");
    mMaxPeriod = par("maxPeriod");
    mEmptyCpm = par("emptyCpm");
    parseQByClass(par("qByClass").stdstringValue());
    if (!(mTargetAlpha > 0.0 && mTargetAlpha < 1.0)) {
        throw cRuntimeError("AocCpService: targetAlpha must be between 0 and 1 (excluded), not %g", mTargetAlpha);
    }
    if (!(mTolerance > 0.0) || mDelayEstimate < 0.0 || mRepeats < 1 || mSigmaX < 0.0 || mSigmaV < 0.0 || mStoppedSpeed < 0.0 ||
        (mStoppedSpeed > 0.0 && !(mQStopped > 0.0)) || mDeviationFraction < 0.0 || !(mMaxPeriod > 0.0)) {
        throw cRuntimeError("AocCpService: tolerance, qStopped and maxPeriod must be positive, delayEstimate, sigmaX, sigmaV, stoppedSpeed and "
                            "deviationFraction not negative, repeats at least 1");
    }
    mGenStep = mGenCpmMin;

    if (mAocRule) {
        // the periods of the rule for the classes named, for the log of the run
        for (const auto& entry : mQByClass) {
            Curve c{entry.second, mSigmaX, mSigmaV};
            EV_INFO << "AocCpService: class " << entry.first << " q " << entry.second << " age limit " << ageLimit(c, mTargetAlpha, mTolerance)
                    << " s, period " << period(c, mTargetAlpha, mTolerance, mDelayEstimate, mRepeats, mMaxPeriod) << " s\n";
        }
        if (mStoppedSpeed > 0.0) {
            Curve c{mQStopped, mSigmaXStopped, mSigmaVStopped};
            EV_INFO << "AocCpService: stopped object: age limit " << ageLimit(c, mTargetAlpha, mTolerance) << " s, period "
                    << period(c, mTargetAlpha, mTolerance, mDelayEstimate, mRepeats, mMaxPeriod) << " s\n";
        }
    }
}

void AocCpService::finish()
{
    recordScalar("aocCpmDropped", mDropped);
    recordScalar("aocCpmPassed", mPassed);
    if (mAocRule || mEtsiAbsolute) {
        recordScalar("aocCpmSent", mCpmSent);
        recordScalar("aocCpmSkipped", mCpmSkipped);
        recordScalar("aocIncludedNew", mIncludedNew);
        recordScalar("aocIncludedAge", mIncludedAge);
        recordScalar("aocIncludedState", mIncludedState);
        recordScalar("aocIncludedDeviation", mIncludedDeviation);
        recordScalar("aocIncludedEtsi", mIncludedEtsi);
        recordScalar("aocIncludedTypeA", mIncludedTypeA);
    }
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

void AocCpService::checkTriggeringConditions(const SimTime& T_now)
{
    if (!mAocRule && !mEtsiAbsolute) {
        CpService::checkTriggeringConditions(T_now);
        return;
    }
    // the same gate as CpService: a generation step every minInterval, or every DCC interval
    mGenStep = mDccRestriction ? genCpmDcc() : mGenCpmMin;
    if (T_now - mLastCpmTimestamp >= mGenStep) {
        sendCpm(T_now);
    }
}

void AocCpService::sendCpm(const SimTime& T_now)
{
    if (!mAocRule && !mEtsiAbsolute) {
        CpService::sendCpm(T_now);
        return;
    }

    // the same snapshots as CpService
    captureVdpSnapshot();
    const auto lemSensorsSnapshot = mLocalEnvironmentModel->getSensors();
    const auto lemObjectsSnapshot = mLocalEnvironmentModel->allObjects();
    const auto objectVdpSnapshots = captureObjectVdpSnapshot(lemObjectsSnapshot);
    const auto referenceTime = countTaiMilliseconds(mTimer->getTimeFor(mVdpSnapshot.updated));

    const uint32_t stationId = mVdpSnapshot.stationId;
    if (!mHaveStationId) {
        mHaveStationId = true;
        mLastStationId = stationId;
    } else if (stationId != mLastStationId) {
        handlePseudonymChange();
        mLastStationId = stationId;
        mMemory.clear();
    }

    captureSensorSnapshot(T_now, lemSensorsSnapshot);
    capturePerceivedObjectSnapshot(T_now, referenceTime, lemObjectsSnapshot, objectVdpSnapshots);

    // what is due at this step: the sensor information, the CPM of maxInterval, then the objects of the rule
    const bool sensorsDue = checkSensorInformationTrigger(T_now);
    const bool periodicDue = (T_now - mLastCpmTimestamp >= mGenCpmMax);
    mCpmGoesOutAnyway = sensorsDue || periodicDue || mEmptyCpm;
    const bool objectsDue = checkPerceivedObjectTrigger(T_now);
    if (!objectsDue && !mCpmGoesOutAnyway) {
        ++mCpmSkipped;
        return;
    }

    auto cpm = createCollectivePerceptionMessage(mVdpSnapshot, referenceTime);
    if (mVdpSnapshot.stationType == static_cast<int>(vanetza::geonet::StationType::RSU)) {
        addOriginatingRsuContainer(cpm);
    } else {
        addOriginatingVehicleContainer(cpm, mVdpSnapshot);
    }
    if (sensorsDue) {
        addSensorInformationContainer(cpm, mSensorSnapshot);
        mLastSensorInformationTimestamp = T_now;
    }
    if (checkPerceptionRegionTrigger()) {
        addPerceptionRegionContainer(cpm);
    }
    if (objectsDue) {
        addPerceivedObjectContainer(cpm, mPerceivedObjectSnapshot, mSelectedCpmObjects, mLastCpmTimestamp);
    }

    std::string error;
    if (!cpm.validate(error)) {
        throw cRuntimeError("Invalid CPM: %s", error.c_str());
    }

    mLastCpmTimestamp = T_now;
    ++mCpmSent;

    using namespace vanetza;
    btp::DataRequestB request;
    request.destination_port = btp::ports::CPM;
    request.gn.its_aid = aid::CP;
    request.gn.transport_type = geonet::TransportType::SHB;
    request.gn.maximum_lifetime = geonet::Lifetime{geonet::Lifetime::Base::One_Second, 1};
    request.gn.traffic_class.tc_id(static_cast<unsigned>(dcc::Profile::DP2));
    request.gn.communication_profile = geonet::CommunicationProfile::ITS_G5;

    CpObject obj(std::move(cpm));
    emit(scSignalAocCpmSent, &obj);

    using CpmByteBuffer = convertible::byte_buffer_impl<Cpm>;
    std::unique_ptr<geonet::DownPacket> payload{new geonet::DownPacket()};
    std::unique_ptr<convertible::byte_buffer> buffer{new CpmByteBuffer(obj.shared_ptr())};
    payload->layer(OsiLayer::Application) = std::move(buffer);
    this->request(request, std::move(payload));
}

bool AocCpService::checkPerceivedObjectTrigger(const SimTime& T_now)
{
    if (!mAocRule && !mEtsiAbsolute) {
        return CpService::checkPerceivedObjectTrigger(T_now);
    }
    if (mPerceivedObjectSnapshot.empty()) {
        return false;
    }

    // the rules of ETSI with absolute positions: all type A objects go in when one is new or one is older than maxInterval / 2
    bool includeAllTypeA = false;
    if (mEtsiAbsolute) {
        for (const auto idx : mSelectedCpmObjects) {
            const auto& po = mPerceivedObjectSnapshot[idx];
            if (po.type != PerceivedObjectType::TypeA) {
                continue;
            }
            if (po.objectAgeMs < (T_now - mLastCpmTimestamp).dbl() * 1000) {  // newly detected
                includeAllTypeA = true;
                break;
            }
            auto it = mPerceivedObjectHistories.find(static_cast<int>(po.cpsId));
            if (it != mPerceivedObjectHistories.end() && T_now - it->second.inclusion >= mGenCpmMax / 2) {
                includeAllTypeA = true;
                break;
            }
        }
    }

    const double egoX = mVdpSnapshot.position.x.value();
    const double egoY = mVdpSnapshot.position.y.value();
    // the report is measured before this step: the lag of the position of the sender plus the lag of the measurement
    const double refLag = std::max(0.0, (T_now - mVdpSnapshot.updated).dbl());
    const double tiny = 1.0e-6;
    // the next evaluation is one update interval away (the gate of the service is open until a CPM goes out), or one
    // generation interval away once a CPM goes out at this step (the DCC interval, or minInterval): two passes, the
    // second one only when a CPM goes out
    const double stepShort = std::min(mGenCpmMin, mGenStep).dbl();
    const double stepLong = mGenStep.dbl();

    std::vector<std::size_t> selected;
    selected.reserve(mSelectedCpmObjects.size());
    std::vector<std::size_t> dueLater;                                 // due before the next step once a CPM goes out

    for (const auto idx : mSelectedCpmObjects) {
        auto& po = mPerceivedObjectSnapshot[idx];
        po.haveHistory = false;
        po.distanceDiffM = 0.0;
        po.speedDiffMps = 0.0;
        po.orientationDiffDeg = 0.0;
        po.sinceLastInclusionSeconds = 0.0;

        if (po.objectPerceptionQuality <= mObjectPerceptionQualityThreshold) {
            continue;
        }
        if (po.type == PerceivedObjectType::Unknown) {
            EV_WARN << "perceived object type is unknown, skipping object";
            continue;
        }

        // the object now, in absolute coordinates, and its velocity from the last two steps
        const int key = static_cast<int>(po.cpsId);
        const double x = egoX + po.xCm / 100.0;
        const double y = egoY + po.yCm / 100.0;
        auto histIt = mPerceivedObjectHistories.find(key);
        ObjectMemory& mem = mMemory[key];
        if (histIt == mPerceivedObjectHistories.end()) {
            mem = ObjectMemory();                                      // a new object (or an object id reused for another one)
        }
        if (mem.haveSeen && T_now > mem.seen) {
            const double dt = (T_now - mem.seen).dbl();
            mem.vx = (x - mem.x) / dt;
            mem.vy = (y - mem.y) / dt;
            mem.haveVelocity = true;
        }
        mem.haveSeen = true;
        mem.seen = T_now;
        mem.x = x;
        mem.y = y;

        // the cached values of CpService, for the utility function and the sort
        if (histIt != mPerceivedObjectHistories.end()) {
            const PerceivedObjectHistory& history = histIt->second;
            po.haveHistory = true;
            po.sinceLastInclusionSeconds = (T_now - history.inclusion).dbl();
            Position objectPos(static_cast<double>(po.xCm), static_cast<double>(po.yCm));
            po.distanceDiffM = distance(objectPos, history.position).value() / 100.0;
            po.speedDiffMps = std::abs((po.hasVelocity ? po.speedMps : 0.0) - history.speed);
            po.orientationDiffDeg = std::abs((po.hasVelocity ? po.headingDeg : 0.0) - history.orientation.degree());
        }

        bool include = false;
        if (mEtsiAbsolute) {
            // the rules of TS 103 324 as CpService applies them, with the position change of rule 2b taken on the
            // absolute position of the object (CpService takes it on the position relative to the sender, which moves)
            if (po.type == PerceivedObjectType::TypeA) {
                include = includeAllTypeA;
            } else if (!mem.haveIncluded || !po.haveHistory) {
                include = true;
                ++mIncludedNew;
            } else {
                const double moved = std::hypot(x - mem.xi, y - mem.yi);
                include = (po.objectAgeMs < (T_now - mLastCpmTimestamp).dbl() * 1000) || moved > mMinPositionChangeThreshold.value() ||
                          po.speedDiffMps > mMinGroundSpeedChangeThreshold.value() ||
                          po.orientationDiffDeg >= mMinGroundVelocityOrientationChangeThreshold.value() ||
                          po.sinceLastInclusionSeconds >= mGenCpmMax.dbl();
                if (include) {
                    ++mIncludedEtsi;
                }
            }
            if (include && po.type == PerceivedObjectType::TypeA) {
                ++mIncludedTypeA;
            }
        } else if (!mem.haveIncluded || !po.haveHistory) {
            // a new object: included at once, as in the rules of ETSI
            include = true;
            ++mIncludedNew;
        } else if (isStopped(po) != mem.stoppedSent) {
            // the object changed state since its last report: the receiver ages it with the wrong curve until the next one
            include = true;
            ++mIncludedState;
        } else {
            // the rule of the note: the report must not be older than the period at the next evaluation. The curve is the
            // one of the report the receiver holds (its class and its state), not of the object now
            const Curve curve = curveOf(po, mem.stoppedSent);
            const double T = period(curve, mTargetAlpha, mTolerance, mDelayEstimate, mRepeats, mMaxPeriod);
            const double lag = refLag + std::max(0L, po.measurementDeltaTimeMs) / 1000.0;
            const double age = (T_now - mem.included).dbl() + lag;
            if (age + stepShort > T + tiny) {
                include = true;
                ++mIncludedAge;
            } else if (age + stepLong > T + tiny) {
                dueLater.push_back(idx);                               // due only if a CPM goes out at this step anyway
            } else if (mDeviationFraction > 0.0 && mem.haveVelocity) {
                // what the sender knows: the receiver's prediction against the object now
                const double dt = (T_now - mem.included).dbl();
                const double dx = x - (mem.xi + mem.vxi * dt);
                const double dy = y - (mem.yi + mem.vyi * dt);
                if (std::sqrt(dx * dx + dy * dy) >= mDeviationFraction * mTolerance) {
                    include = true;
                    ++mIncludedDeviation;
                }
            }
        }
        if (include) {
            selected.push_back(idx);
            markSent(mem, po, T_now, x, y);
        }
    }

    // the second pass: a CPM goes out at this step (objects due, or the sensor information, or the periodic CPM), so the
    // objects due before the next generation step go in too
    if (!dueLater.empty() && (!selected.empty() || mCpmGoesOutAnyway)) {
        for (const auto idx : dueLater) {
            auto& po = mPerceivedObjectSnapshot[idx];
            const double x = egoX + po.xCm / 100.0;
            const double y = egoY + po.yCm / 100.0;
            selected.push_back(idx);
            markSent(mMemory[static_cast<int>(po.cpsId)], po, T_now, x, y);
            ++mIncludedAge;
        }
        std::sort(selected.begin(), selected.end());
    }

    // forget the objects not seen for a while (the CPM object ids are reused after a retention period)
    if (mMemory.size() > 4 * mPerceivedObjectSnapshot.size() + 64) {
        for (auto it = mMemory.begin(); it != mMemory.end();) {
            if (T_now - it->second.seen > mGenCpmMax * 10) {
                it = mMemory.erase(it);
            } else {
                ++it;
            }
        }
    }

    mSelectedCpmObjects = std::move(selected);
    if (mSelectedCpmObjects.empty()) {
        return false;
    }

    // the history and the utility of CpService for the included objects, then its sort
    for (const auto idx : mSelectedCpmObjects) {
        auto& po = mPerceivedObjectSnapshot[idx];
        po.utility = calculateUtilityFunction(po, po.distanceDiffM, po.speedDiffMps, po.orientationDiffDeg, po.sinceLastInclusionSeconds);
        mPerceivedObjectHistories[static_cast<int>(po.cpsId)] = PerceivedObjectHistory{
            T_now, Position(static_cast<double>(po.xCm), static_cast<double>(po.yCm)), (po.hasVelocity ? po.speedMps : 0.0),
            Angle((po.hasVelocity ? po.headingDeg : 0.0) * M_PI / 180.0)};
    }
    sortPerceivedObjects();
    return true;
}

}  // namespace artery
