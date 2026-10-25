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
