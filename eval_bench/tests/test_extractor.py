import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from extractor import extract_code


def test_extract_fenced_python():
    text = "Here is the solution:\n```python\nprint('hello')\n```\nDone."
    assert extract_code(text, "python") == "print('hello')"


def test_extract_fenced_no_lang():
    text = "Solution:\n```\nx = 1\nprint(x)\n```"
    assert extract_code(text, "python") == "x = 1\nprint(x)"


def test_extract_multiple_blocks_returns_first():
    text = "```python\nfirst()\n```\nand\n```python\nsecond()\n```"
    assert extract_code(text, "python") == "first()"


def test_extract_no_block_returns_full_text():
    text = "print('hello')"
    result = extract_code(text, "python")
    assert "print" in result


def test_extract_strips_think_tags():
    text = "<think>reasoning here</think>\n```python\nprint(42)\n```"
    assert extract_code(text, "python") == "print(42)"


def test_extract_javascript():
    text = "```javascript\nconsole.log('hi');\n```"
    assert extract_code(text, "javascript") == "console.log('hi');"


def test_extract_go():
    code = 'package main\nimport "fmt"\nfunc main() { fmt.Println("hi") }'
    text = f"```go\n{code}\n```"
    assert extract_code(text, "go") == code
