"""Project composition: register mathematical adapters outside generic cluster code."""

DEFAULT_CAMPAIGN_PROGRAM = 'dp'


def configure(register):
    """Register installed trusted solver adapters through the supplied callback."""

    from dp_solver.adapter import DPAdapter
    register(DPAdapter())
    from matching_solver.adapter import MatchingAdapter
    register(MatchingAdapter())
    from matching_solver_multi.adapter import PartitionedAdapter
    register(PartitionedAdapter())
    from gpu_match_solver.adapter import GPUMatchingAdapter
    register(GPUMatchingAdapter())
