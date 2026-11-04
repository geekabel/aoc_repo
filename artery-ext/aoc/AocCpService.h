/*
 * AocCpService: CpService with the AoC generation rule (task E1 of the AoC plan) and a controlled loss of
 * received CPMs (task C1). Licensed under GPLv2 like Artery.
 */
#ifndef AOC_CP_SERVICE_H_
#define AOC_CP_SERVICE_H_

#include "artery/envmod/service/CpService.h"

#include <cmath>
#include <map>
#include <string>
#include <unordered_map>

namespace artery
{

class AocCpService : public CpService
{
public:
    void initialize() override;
    void finish() override;
    void indicate(const vanetza::btp::DataIndication&, std::unique_ptr<vanetza::UpPacket>) override;

    // the reliability curve of the theory note: alpha(age) = 1 - exp(-eps^2 / (2 sigma^2(age))),
    // sigma^2(age) = sx^2 + sv^2 age^2 + q age^3 / 3
    struct Curve {
        double q = 0.0;       // motion value, m^2/s^3
        double sigmaX = 0.0;  // position error of the sender, m
        double sigmaV = 0.0;  // speed error of the sender, m/s
    };
    static double alphaOfAge(const Curve&, double age, double eps);
    // the age at which alpha falls to the target (0 when the target cannot be met even fresh)
    static double ageLimit(const Curve&, double target, double eps);
    // the sending period of the rule of the note: (age limit - delay) / n, never negative, at most maxPeriod
    static double period(const Curve&, double target, double eps, double delay, int repeats, double maxPeriod = 1.0e9);

    // what the rule remembers about an object (keyed by its CPM object id)
    struct ObjectMemory {
        bool haveSeen = false;
        omnetpp::SimTime seen;        // the last generation step at which the object was in the snapshot
        double x = 0.0, y = 0.0;      // its absolute position then, m
        double vx = 0.0, vy = 0.0;    // its velocity from the last two positions, m/s
        bool haveVelocity = false;
        omnetpp::SimTime included;    // the last inclusion in a CPM
        double xi = 0.0, yi = 0.0;    // the position and the velocity sent then
        double vxi = 0.0, vyi = 0.0;
        bool stoppedSent = false;     // the state sent then: the curve the receiver ages the report with
        bool haveIncluded = false;
    };

protected:
    void checkTriggeringConditions(const omnetpp::SimTime& T_now) override;
    void sendCpm(const omnetpp::SimTime& T_now) override;
    bool checkPerceivedObjectTrigger(const omnetpp::SimTime& T_now) override;

private:
    Curve curveOf(const PerceivedObjectSnapshot&, bool stopped) const;
    bool isStopped(const PerceivedObjectSnapshot&) const;
    void markSent(ObjectMemory&, const PerceivedObjectSnapshot&, const omnetpp::SimTime& T_now, double x, double y) const;
    static std::string className(int stationType);
    void parseQByClass(const std::string&);

    // the AoC rule
    bool mAocRule = false;
    bool mEtsiAbsolute = false;   // the rules of ETSI with the position change taken on the absolute position
    double mTargetAlpha = 0.9;
    double mTolerance = 1.5;      // eps, m
    double mDelayEstimate = 0.1;  // d, s: the delay the sender assumes between its measurement and the reception
    int mRepeats = 1;             // n of the rule
    double mSigmaX = 0.3;
    double mSigmaV = 0.5;
    std::map<std::string, double> mQByClass;  // class name -> q; "default" for the classes not named
    double mStoppedSpeed = 0.5;   // below this speed the object is treated as stopped (m/s); 0 turns it off
    double mQStopped = 0.001;
    double mSigmaXStopped = 0.5;
    double mSigmaVStopped = 0.05;
    double mDeviationFraction = 0.0;  // send when the receiver's prediction is off by this fraction of eps; 0 turns it off
    double mMaxPeriod = 4.5;          // the period never exceeds this (the receivers forget after their hold)
    bool mEmptyCpm = false;           // send a CPM at every generation step even without an object due
    omnetpp::SimTime mGenStep;        // the generation interval in force at the current step
    bool mCpmGoesOutAnyway = false;   // at this step a CPM goes out whatever the objects say (sensor information, maxInterval)
    std::unordered_map<int, ObjectMemory> mMemory;

    // counters, recorded as scalars
    unsigned long mIncludedNew = 0;
    unsigned long mIncludedAge = 0;
    unsigned long mIncludedState = 0;
    unsigned long mIncludedDeviation = 0;
    unsigned long mIncludedEtsi = 0;
    unsigned long mIncludedTypeA = 0;
    unsigned long mCpmSkipped = 0;
    unsigned long mCpmSent = 0;

    // the controlled loss of C1
    double mDropProbability = 0.0;
    unsigned long mDropped = 0;
    unsigned long mPassed = 0;
};

}  // namespace artery

#endif /* AOC_CP_SERVICE_H_ */
