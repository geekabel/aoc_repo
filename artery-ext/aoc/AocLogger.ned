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
