//
// AocLoadService: a source of channel load (task E2 of the AoC plan). Every station that runs it broadcasts
// packets of a fixed size at a fixed rate, single hop, with the lowest priority (DP3), through the same stack as
// the CAMs and the CPMs (so the DCC of the station throttles them too). The packets carry nothing and nobody
// reads them: they stand for the other applications and the other stations that load the channel. With rate = 0
// the service sends nothing. The first packet goes out at a random time in the first period, so the stations do
// not send together; the following ones every period, with a jitter of jitterShare of the period.
//
import artery.application.ItsG5Service;

simple AocLoadService like ItsG5Service
{
    parameters:
        @class(artery::AocLoadService);
        double rate @unit(Hz) = default(0Hz);        // packets per second per station; 0 sends nothing
        int size @unit(byte) = default(300byte);     // the size of a packet (a CPM with a few objects is 150 to 400 bytes)
        double jitterShare = default(0.1);           // the period varies by this share, uniformly, so the stations stay apart
}
