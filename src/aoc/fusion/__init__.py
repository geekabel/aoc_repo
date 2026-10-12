"""The replay and fusion pipeline.

Written:
  noise        the noise model of the replay: true state at the measurement time plus seeded noise (B2)
  replay       loads an exported run and replays it tick by tick for one vehicle (B2, command ``aoc-replay``)
  alignment    moves every object to the time of the tick, with its uncertainty (B3)
  association  decides which objects of a tick are the same real object (B3, command ``aoc-associate``)

Planned (tasks B4 to B7 of the Plan):
  bba          builds the mass functions for existence and class (B4)
  discounting  contextual discounting with alpha(age) (B5)
  combination  Dempster and cautious rules (B6)
  kinematics   fusion of position and speed by covariance intersection (B7)
"""
