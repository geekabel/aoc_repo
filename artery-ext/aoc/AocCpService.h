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
