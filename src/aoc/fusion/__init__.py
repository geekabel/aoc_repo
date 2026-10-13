"""The replay and fusion pipeline.

Written:
  noise        the noise model of the replay: true state at the measurement time plus seeded noise (B2)
  replay       loads an exported run and replays it tick by tick for one vehicle (B2, command ``aoc-replay``)
  alignment    moves every object to the time of the tick, with its uncertainty (B3)
  association  decides which objects of a tick are the same real object, with the adapted greedy
               algorithm of Wolf et al. and a likelihood distance (B3, command ``aoc-associate``)

  belief       mass functions on small frames, for many objects at once: belief, plausibility, pignistic (B4)
  bba          the existence and class evidence of each reported object (B4, command ``aoc-evidence``)

Planned (tasks B5 to B7 of the Plan):
  discounting  contextual discounting with alpha(age) (B5)
  combination  Dempster and cautious rules (B6)
  kinematics   fusion of position and speed by covariance intersection (B7)
"""
