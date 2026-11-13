"""Keep the domain independent of third-party packages and outer layers."""

import ast
import sys
from pathlib import Path


def test_domain_imports_only_domain_and_python_standard_library() -> None:
    domain = Path(__file__).resolve().parents[1] / "src" / "domain"
    violations: list[str] = []
    for path in domain.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""] if node.level == 0 else []
            else:
                continue
            for module in modules:
                root = module.split(".", 1)[0]
                if root != "domain" and root not in sys.stdlib_module_names:
                    violations.append(
                        f"{path.relative_to(domain)}:{node.lineno} imports {module}"
                    )
    assert not violations, "\n".join(violations)


def test_api_does_not_import_infrastructure() -> None:
    api = Path(__file__).resolve().parents[1] / "src" / "api"
    violations: list[str] = []
    for path in api.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""] if node.level == 0 else []
            else:
                continue
            for module in modules:
                if module == "infrastructure" or module.startswith("infrastructure."):
                    violations.append(
                        f"{path.relative_to(api)}:{node.lineno} imports {module}"
                    )
    assert not violations, "\n".join(violations)
