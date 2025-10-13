from dataclasses import dataclass, field
from time import perf_counter_ns
from typing import Dict


@dataclass
class BootMetrics:
    start_ns: int = field(default_factory=perf_counter_ns)
    phase_starts: Dict[str, int] = field(default_factory=dict)
    phase_durations_ns: Dict[str, int] = field(default_factory=dict)
    end_ns: int | None = None

    def start_phase(self, name: str) -> None:
        """Mark the start of a phase."""
        self.phase_starts[name] = perf_counter_ns()

    def end_phase(self, name: str) -> None:
        """Mark the end of a phase and record its duration."""
        if name in self.phase_starts:
            end = perf_counter_ns()
            duration = end - self.phase_starts[name]
            self.phase_durations_ns[name] = duration

    def finish(self) -> None:
        """Mark the end of the entire boot process."""
        self.end_ns = perf_counter_ns()

    def to_dict(self) -> Dict[str, float]:
        """Convert metrics to a dictionary with millisecond precision."""
        total_ms = None
        if self.end_ns is not None:
            total_ms = (self.end_ns - self.start_ns) / 1_000_000.0
        phases_ms = {k: v / 1_000_000.0 for k, v in self.phase_durations_ns.items()}
        return {
            "total_ms": total_ms,
            "phases_ms": phases_ms,
        }

