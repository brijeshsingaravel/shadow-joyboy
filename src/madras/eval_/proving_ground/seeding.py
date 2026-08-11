"""Per-(scenario, resample) sub-seed derivation for reproducible sweeps.

`run_sweep`'s `seed` param is a single base value for the whole run, but each
scenario runs `k` resamples that must still explore distinct sampling (that's
the point of k-repeat pass^k scoring). `derive_seed` deterministically fans the
base seed out to one sub-seed per (scenario_id, resample) pair, so the same
base_seed always reproduces the same k trajectories, while resamples within one
scenario still differ from each other.
"""

from __future__ import annotations

import hashlib

_MASK31 = 2**31 - 1


def derive_seed(base_seed: int, scenario_id: str, resample: int) -> int:
    """A stable non-negative int < 2**31, unique per (base_seed, scenario_id, resample)."""
    key = f"{base_seed}:{scenario_id}:{resample}".encode()
    digest = hashlib.sha256(key).digest()
    return int.from_bytes(digest[:4], "big") % _MASK31
