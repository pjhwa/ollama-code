import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from runners.base import RunResult


def test_run_result_success():
    r = RunResult(stdout="hello\n", stderr="", exit_code=0, elapsed_sec=0.1)
    assert r.success is True


def test_run_result_failure():
    r = RunResult(stdout="", stderr="error", exit_code=1, elapsed_sec=0.2)
    assert r.success is False


def test_run_result_fields():
    r = RunResult(stdout="out", stderr="err", exit_code=0, elapsed_sec=1.5)
    assert r.stdout == "out"
    assert r.stderr == "err"
    assert r.elapsed_sec == 1.5


from runners.python_runner import run as py_run


def test_python_runner_hello():
    result = py_run('print("hello")', "", timeout=5)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_python_runner_stdin():
    code = "import sys; n = int(sys.stdin.read().strip()); print(n * 2)"
    result = py_run(code, "21", timeout=5)
    assert result.stdout.strip() == "42"


def test_python_runner_syntax_error():
    result = py_run("def foo(:", "", timeout=5)
    assert result.exit_code != 0
    assert result.stderr != ""


def test_python_runner_timeout():
    result = py_run("import time; time.sleep(10)", "", timeout=1)
    assert result.exit_code != 0


from runners.js_runner import run as js_run
from runners.go_runner import run as go_run
from runners.bash_runner import run as bash_run


def test_js_runner_hello():
    result = js_run('process.stdout.write("hello\\n")', "", timeout=5)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_js_runner_stdin():
    code = (
        "const lines = [];\n"
        "process.stdin.on('data', d => lines.push(d.toString()));\n"
        "process.stdin.on('end', () => { const n = parseInt(lines.join('').trim()); console.log(n * 3); });\n"
    )
    result = js_run(code, "7", timeout=5)
    assert result.stdout.strip() == "21"


def test_go_runner_hello():
    code = 'package main\nimport "fmt"\nfunc main() { fmt.Println("hello") }'
    result = go_run(code, "", timeout=15)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_bash_runner_hello():
    result = bash_run('echo "hello"', "", timeout=5)
    assert result.stdout.strip() == "hello"
    assert result.exit_code == 0


def test_bash_runner_stdin():
    result = bash_run("read n; echo $((n * 4))", "5", timeout=5)
    assert result.stdout.strip() == "20"
