/*
 * AocLoadService: a source of channel load (task E2 of the AoC plan). Licensed under GPLv2 like Artery.
 */
#ifndef AOC_LOAD_SERVICE_H_
#define AOC_LOAD_SERVICE_H_

#include "artery/application/ItsG5Service.h"
#include "artery/application/NetworkInterface.h"

#include <omnetpp/cmessage.h>

namespace artery
{

class AocLoadService : public ItsG5Service
{
public:
    ~AocLoadService();
    void indicate(const vanetza::btp::DataIndication&, omnetpp::cPacket*, const NetworkInterface&) override;
    void trigger() override;

protected:
    void initialize() override;
    void finish() override;
    void handleMessage(omnetpp::cMessage*) override;

private:
    void sendOne();
    void scheduleNext(bool first);

    omnetpp::cMessage* mTimer = nullptr;
    double mRate = 0.0;        // packets per second
    long mSize = 300;          // bytes
    double mJitter = 0.1;      // share of the period
    unsigned long mSent = 0;
    unsigned long mReceived = 0;
};

}  // namespace artery

#endif /* AOC_LOAD_SERVICE_H_ */
