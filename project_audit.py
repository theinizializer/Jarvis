"""Deterministic source audit. Unreachable files are candidates, never deleted."""
import ast
import hashlib
import json
from pathlib import Path

EXCLUDED = {'venv', '.runtime', '.install-source', 'backups', '__pycache__', 'graphify-out', '.git', 'tests','artifacts'}

def audit_project(root):
    root = Path(root)
    sources = {str(p.relative_to(root)): p for p in root.rglob('*.py')
               if not EXCLUDED.intersection(p.relative_to(root).parts)}
    modules = {name[:-3].replace('/', '.'): name for name in sources}
    edges, errors, hashes = {}, {}, {}
    for name, path in sources.items():
        raw = path.read_bytes()
        hashes.setdefault(hashlib.sha256(raw).hexdigest(), []).append(name)
        try:
            tree = ast.parse(raw, filename=name)
        except SyntaxError as exc:
            errors[name] = str(exc)
            continue
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        edges[name] = sorted({modules[m] for m in imports if m in modules})
    roots = ['jarvis_v10.py', 'installer.py']
    reached, stack = set(), roots[:]
    while stack:
        name = stack.pop()
        if name in reached:
            continue
        reached.add(name)
        stack.extend(edges.get(name, []))
    # Skill modules are discovered through AST metadata, not static imports.
    reached.update(name for name in sources if name.startswith('skills/'))
    candidates = sorted(set(sources) - reached)
    return {'entrypoints': roots, 'python_files': len(sources), 'syntax_errors': errors,
            'identical_files': [names for names in hashes.values() if len(names) > 1],
            'not_reachable_by_static_imports': candidates,
            'note': 'I candidati possono essere utility, test o integrazioni dinamiche: non eliminarli senza verificarne l’uso.',
            'imports': edges}

if __name__ == '__main__':
    print(json.dumps(audit_project(Path(__file__).parent), ensure_ascii=False, indent=2))
