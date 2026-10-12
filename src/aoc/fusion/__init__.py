"""The replay and fusion pipeline. Empty for now.

Planned modules (tasks B2 to B7 of the Plan):
  replay       loads observations.parquet and replays it step by step, with the noise model (B2)
  association  aligns the objects in time and matches them (B3)
  bba          builds the mass functions for existence and class (B4)
  discounting  contextual discounting with alpha(age) (B5)
  combination  Dempster and cautious rules (B6)
  kinematics   fusion of position and speed by covariance intersection (B7)
"""
