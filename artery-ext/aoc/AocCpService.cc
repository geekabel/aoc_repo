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
