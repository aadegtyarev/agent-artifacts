"""The skill's design_check.py, on this repository and on small made-up ones."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "skill" / "agent-artifacts" / "scripts" / "design_check.py"
DESIGN = ROOT / "docs" / "design"


def check(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 (our own script)
        [sys.executable, str(SCRIPT), str(root)], capture_output=True, text=True, timeout=60
    )


def test_repo_matches_its_design():
    r = check(ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


def test_every_pin_class_is_in_pins_md():
    css = (ROOT / "src" / "agent_artifacts" / "static" / "pin.css").read_text()
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selectors = " ".join(re.findall(r"([^{}]+)\{", css))
    classes = set(re.findall(r"\.pin((?:\.[\w-]+)+)", selectors))
    names = {c for chain in classes for c in chain.split(".") if c}
    names |= set(re.findall(r"\.(pins-[\w-]+)", selectors))
    assert {"own", "ag", "line", "rej", "pins-big", "pins-shaped"} <= names
    doc = (DESIGN / "pins.md").read_text()
    missing = [n for n in sorted(names) if f"`.{n}`" not in doc and f"`{n}`" not in doc]
    assert not missing, f"pin.css classes not described in pins.md: {missing}"


def mini(tmp_path: Path, css: str = "", ui: str = "", svg: bool = True) -> Path:
    d = tmp_path / "docs" / "design"
    (d / "icons").mkdir(parents=True)
    (d / "tokens.md").write_text("| `--fg` | `#1d1d1f` | `rgb(0 0 0 / 0.25)` |\n")
    (d / "tone-of-voice.md").write_text(
        '| Concept | Avoid |\n| --- | --- |\n| file | `artifact` · "with the agent" |\n'
    )
    (d / "icons" / "star.svg").write_text("<svg/>")
    (d / "icons.md").write_text("![star](icons/star.svg)\n" if svg else "nothing\n")
    (tmp_path / "app.css").write_text(css)
    (tmp_path / "app.js").write_text(ui)
    return tmp_path


def test_clean_mini_repo_with_other_spellings(tmp_path):
    css = "/* #123456 in a comment */\na { color: #1D1D1F; box-shadow: 0 0 rgba(0,0,0,.25); }\n"
    ui = 'el.style.color = "#1d1d1f";\n// "with the agent" in a comment\nconst k = "artifacts";\n'
    r = check(mini(tmp_path, css, ui))
    assert r.returncode == 0, r.stdout + r.stderr


def test_unknown_colour(tmp_path):
    r = check(mini(tmp_path, css="a {\n  color: #fff;\n}\n"))
    assert r.returncode == 1
    assert "app.css:2: colour #fff" in r.stdout


def test_avoided_word(tmp_path):
    r = check(mini(tmp_path, ui='label("Comment with the Agent");\n'))
    assert r.returncode == 1
    assert "app.js:1:" in r.stdout and "with the Agent" in r.stdout


def test_svg_not_in_icons_md(tmp_path):
    r = check(mini(tmp_path, svg=False))
    assert r.returncode == 1
    assert "docs/design/icons/star.svg:1: not referenced in icons.md" in r.stdout


def test_no_design_folder(tmp_path):
    r = check(tmp_path)
    assert r.returncode == 2
    assert "Write the design system there first" in r.stderr
