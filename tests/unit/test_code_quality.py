"""Tests de qualité du code — annotations de type et respect du PEP8 (app.py)."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

APP_PY = Path(__file__).resolve().parent.parent.parent / "webserver" / "app.py"
MAX_LINE_LENGTH = 100


def _is_app_route_decorator(dec: ast.expr) -> bool:
    """Vrai si le décorateur est `@app.route(...)`."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    return (isinstance(target, ast.Attribute) and target.attr == "route"
            and isinstance(target.value, ast.Name) and target.value.id == "app")


def _route_functions() -> list[ast.FunctionDef]:
    """Renvoie les définitions des vues Flask décorées par `@app.route`."""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"))
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(_is_app_route_decorator(dec) for dec in node.decorator_list)
    ]


# ── Annotations de type et docstrings ─────────────────────────────────────────

class TestRouteAnnotations:
    """Vérifie que toutes les routes Flask sont documentées et typées."""

    def test_route_functions_are_found(self):
        """Garde-fou : s'assure que la détection AST fonctionne toujours."""
        assert len(_route_functions()) >= 40

    def test_all_routes_have_docstring(self):
        missing = [f.name for f in _route_functions() if not ast.get_docstring(f)]
        assert not missing, f"Routes sans docstring : {missing}"

    def test_all_routes_have_return_annotation(self):
        missing = [f.name for f in _route_functions() if f.returns is None]
        assert not missing, f"Routes sans annotation de retour : {missing}"

    def test_all_route_parameters_are_annotated(self):
        unannotated = [
            f"{f.name}({arg.arg})"
            for f in _route_functions()
            for arg in f.args.args
            if arg.arg != "self" and arg.annotation is None
        ]
        assert not unannotated, f"Paramètres non annotés : {unannotated}"


# ── Respect du PEP8 ────────────────────────────────────────────────────────────

class TestPep8Style:
    """Vérifie le respect des règles de style PEP8 de base sur app.py."""

    @pytest.fixture(scope="class")
    def lines(self) -> list[str]:
        return APP_PY.read_text(encoding="utf-8").splitlines()

    def test_no_line_too_long(self, lines: list[str]):
        too_long = [(i, len(line)) for i, line in enumerate(lines, 1)
                    if len(line) > MAX_LINE_LENGTH]
        assert not too_long, f"Lignes trop longues (> {MAX_LINE_LENGTH} car.) : {too_long[:10]}"

    def test_no_trailing_whitespace(self, lines: list[str]):
        trailing = [i for i, line in enumerate(lines, 1) if line != line.rstrip()]
        assert not trailing, f"Espaces en fin de ligne aux lignes : {trailing[:10]}"

    def test_no_tabs(self, lines: list[str]):
        tabbed = [i for i, line in enumerate(lines, 1) if "\t" in line]
        assert not tabbed, f"Tabulations trouvées aux lignes : {tabbed[:10]}"

    def test_ends_with_single_newline(self):
        content = APP_PY.read_text(encoding="utf-8")
        assert content.endswith("\n") and not content.endswith("\n\n"), (
            "Le fichier doit se terminer par exactement un saut de ligne"
        )


# ── Vérification de typage statique (mypy) ───────────────────────────────────

class TestMypy:
    """Vérifie que app.py passe la vérification de types mypy sans erreur."""

    def test_app_passes_mypy(self):
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--ignore-missing-imports", str(APP_PY)],
            capture_output=True, text=True, cwd=str(APP_PY.parent),
        )
        assert "Success: no issues found" in result.stdout, result.stdout
