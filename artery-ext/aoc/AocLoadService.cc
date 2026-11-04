/*
 * AocLoadService: a source of channel load (task E2 of the AoC plan). Licensed under GPLv2 like Artery.
 *
 * Packets of a fixed size at a fixed rate, single hop broadcast, DP3, through the same stack as the CAMs and the
 * CPMs. They carry nothing; the receivers count and drop them. See AocLoadService.ned.
 */
#include "AocLoadService.h"

#include "artery/application/MultiChannelPolicy.h"
#include "artery/application/NetworkInterfaceTable.h"

#include <omnetpp/cexception.h>
#include <omnetpp/cpacket.h>
#include <vanetza/btp/data_request.hpp>
#include <vanetza/dcc/profile.hpp>
#include <vanetza/geonet/interface.hpp>

using namespace omnetpp;
using namespace vanetza;

namespace artery
{

Define_Module(AocLoadService)

static const ItsAid scLoadItsAid = 16481;   // an ITS-AID reserved for testing, next to the one of Artery's example service

AocLoadService::~AocLoadService()
{
    cancelAndDelete(mTimer);
}

void AocLoadService::initialize()
{
    ItsG5Service::initialize();
    mRate = par("rate");
    mSize = par("size");
    mJitter = par("jitterShare");
    if (mRate < 0.0 || mSize < 1 || mJitter < 0.0 || mJitter >= 1.0) {
        throw cRuntimeError("AocLoadService: rate must not be negative, size at least 1 byte, jitterShare between 0 and 1 (excluded)");
    }
    mTimer = new cMessage("AocLoad");
    if (mRate > 0.0) {
        scheduleNext(true);
    }
}

void AocLoadService::finish()
{
    recordScalar("aocLoadSent", mSent);
    recordScalar("aocLoadReceived", mReceived);
    ItsG5Service::finish();
}

void AocLoadService::scheduleNext(bool first)
{
    const double period = 1.0 / mRate;
    const double wait = first ? uniform(0.0, period) : period * uniform(1.0 - mJitter, 1.0 + mJitter);
    scheduleAt(simTime() + wait, mTimer);
}

void AocLoadService::handleMessage(cMessage* msg)
{
    Enter_Method("handleMessage");
    if (msg == mTimer) {
        sendOne();
        scheduleNext(false);
    }
}

void AocLoadService::trigger()
{
    // the timer does the sending; nothing to do at the update of the middleware
}

void AocLoadService::sendOne()
{
    auto& mco = getFacilities().get_const<MultiChannelPolicy>();
    auto& networks = getFacilities().get_const<NetworkInterfaceTable>();
    for (auto channel : mco.allChannels(scLoadItsAid)) {
        auto network = networks.select(channel);
        if (!network) {
            EV_ERROR << "AocLoadService: no network interface for channel " << channel << "\n";
            continue;
        }
        btp::DataRequestB req;
        req.destination_port = host_cast(getPortNumber(channel));
        req.gn.transport_type = geonet::TransportType::SHB;
        req.gn.traffic_class.tc_id(static_cast<unsigned>(dcc::Profile::DP3));
        req.gn.communication_profile = geonet::CommunicationProfile::ITS_G5;
        req.gn.its_aid = scLoadItsAid;
        cPacket* packet = new cPacket("AocLoad");
        packet->setByteLength(mSize);
        request(req, packet, network.get());
        ++mSent;
    }
}

void AocLoadService::indicate(const btp::DataIndication&, cPacket* packet, const NetworkInterface&)
{
    Enter_Method("indicate");
    ++mReceived;
    delete packet;
}

}  // namespace artery
