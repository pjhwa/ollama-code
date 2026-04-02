from runners.python_runner import run as _py_run
from runners.js_runner import run as _js_run
from runners.go_runner import run as _go_run
from runners.bash_runner import run as _bash_run
from runners.base import RunResult


_RUNNERS = {
    "python": _py_run,
    "javascript": _js_run,
    "js": _js_run,
    "go": _go_run,
    "bash": _bash_run,
}


def get_runner(lang: str):
    """Return run(code, stdin_input, timeout) callable for given language."""
    key = lang.lower()
    if key not in _RUNNERS:
        raise ValueError(f"Unsupported language: {lang!r}. Supported: {list(_RUNNERS)}")
    return _RUNNERS[key]
