"""The replay and fusion pipeline.

Written:
  noise        the noise model of the replay: true state at the measurement time plus seeded noise (B2)
  replay       loads an exported run and replays it tick by tick for one vehicle (B2, command ``aoc-replay``)
  alignment    moves every object to the time of the tick, with its uncertainty (B3)
  association  decides which objects of a tick are the same real object, with the adapted greedy
               algorithm of Wolf et al. and a likelihood distance (B3, command ``aoc-associate``)

  belief       mass functions on small frames, for many objects at once: belief, plausibility, pignistic (B4),
               classical and contextual discounting, disjunctive rule (B5)
  bba          the existence and class evidence of each reported object (B4, command ``aoc-evidence``)
  discounting  the reliability alpha of each object from its age, and the discount of its existence evidence,
               with the two baselines full trust and hard cutoff (B5, command ``aoc-discount``)
  combination  one piece of evidence per real object: the conjunctive rule for the on-board sensors, the cautious
               rule of Denoeux for the received objects (B6, command ``aoc-combine``). The rules are in ``belief``.
  kinematics   one place and one speed per real object, by covariance intersection (B7, command ``aoc-kinematics``)

The evidential layer (bba, discounting, combination) and the kinematic layer (kinematics) stay separate.
"""
