from __future__ import annotations
from dataclasses import dataclass


@dataclass
class RunResult:
    stdout: str
    stderr: str
    exit_code: int
    elapsed_sec: float

    @property
    def success(self) -> bool:
        return self.exit_code == 0
