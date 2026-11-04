//
// AocCpService: the collective perception service of Artery, plus the AoC generation rule (task E1 of the AoC
// plan) and a controlled loss of received CPMs (task C1).
//
// generationRule = "etsi": the service behaves exactly like CpService (the rules of TS 103 324: objectInclusionConfig,
// minInterval, maxInterval, withDccRestriction as in CpService). "etsi_abs": the same rules, with the position change
// of an object (4 m) taken on its absolute position; CpService takes it on the position relative to the sender, so a
// moving sender sends every stopped object it sees at every step.
//
// generationRule = "aoc": each perceived object is sent with the period of the theory note, (age limit - delay) / n,
// where the age limit is the age at which the reliability alpha(age) of the object's class falls to targetAlpha
// (alpha(age) = 1 - exp(-tolerance^2 / (2 (sigmaX^2 + sigmaV^2 age^2 + q age^3 / 3)))). The rule is evaluated at
// every generation step (minInterval, or the DCC interval with withDccRestriction): an object goes into the CPM of
// this step when its last report would be older than the period at the next step; a newly seen object goes in at
// once. An object slower than stoppedSpeed takes the curve of a parked vehicle (qStopped, sigmaXStopped,
// sigmaVStopped). The period never exceeds maxPeriod. With deviationFraction > 0 an object also goes in when the receiver's prediction (the last sent
// position moved at the last sent velocity) is off by that fraction of the tolerance. A CPM is sent only when an
// object or the sensor information is due, and at least every maxInterval; with emptyCpm = true at every step.
//
// Each received CPM is dropped with probability dropProbability, before the service reads it (the random numbers
// of the module, so a seed set gives the same drops). A dropped CPM is announced with the signal CpmDropped, which
// the AoC logger writes to cpm_drop.csv.
//
import artery.envmod.service.CpService;

simple AocCpService extends CpService
{
    parameters:
        @class(artery::AocCpService);
        @signal[CpmDropped](type=artery::CpObject);
        double dropProbability = default(0.0);     // 0 to 1 (excluded): each received CPM is lost with this probability

        string generationRule = default("etsi");   // "etsi", "etsi_abs" or "aoc"
        double targetAlpha = default(0.9);         // the target reliability at the receivers
        double tolerance @unit(m) = default(1.5m); // the tolerance eps of the reliability
        double delayEstimate @unit(s) = default(0.1s);  // the delay d the sender assumes between its measurement and the reception
        int repeats = default(1);                  // n: the reports sent while the reliability is above the target
        double sigmaX @unit(m) = default(0.3m);    // the position error of the sender's reports
        double sigmaV @unit(mps) = default(0.5mps);  // the speed error of the sender's reports
        // the motion value q per class (the names of the exporter: passenger_car, heavy_truck, pedestrian, cyclist, bus, ...),
        // with a "default" for the classes not named; the fitted values of D3 by default
        string qByClass = default("passenger_car=0.269,heavy_truck=0.118,pedestrian=0.0252,cyclist=0.98,default=0.269");
        double stoppedSpeed @unit(mps) = default(0.5mps);  // below this speed an object takes the curve of a parked vehicle; 0 turns it off
        double qStopped = default(0.001);          // the parked vehicle of the note
        double sigmaXStopped @unit(m) = default(0.5m);
        double sigmaVStopped @unit(mps) = default(0.05mps);
        double deviationFraction = default(0.0);   // send when the receiver's prediction is off by this fraction of the tolerance; 0 turns it off
        double maxPeriod @unit(s) = default(4.5s); // the period of an object never exceeds this: the receivers forget an object after their hold (5 s)
        bool emptyCpm = default(false);            // send a CPM at every generation step even when nothing is due
}
