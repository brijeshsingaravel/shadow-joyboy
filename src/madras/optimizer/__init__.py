"""Self-optimizer (W4·B1) — GEPA-style reflective evolution of prompts/tool-descs/skills.

In-house Genetic-Pareto loop: reflect on eval-trace failures -> propose -> measure lift ->
keep a Pareto frontier. Propose-not-dispose: returns a gated OptimProposal with measured lift.
"""

from madras.optimizer.evolve import evolve
from madras.optimizer.models import Candidate, OptimProposal, Target

__all__ = ["Candidate", "OptimProposal", "Target", "evolve"]
