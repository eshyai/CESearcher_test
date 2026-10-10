"""审计 backend/app 下的第三方依赖，用于核对容器镜像 requirements 是否完整。

用法:
    python test/audit_imports.py

输出:
    - 代码中实际 import 的第三方顶层模块
    - requirements.txt 中声明但代码未用到的（仅提示）
    - 代码用到但 requirements.txt 未声明的（关键，会导致容器启动失败）
"""
import ast
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"
REQ_FILE = APP_DIR / "requirements.txt"

STDLIB = set(sys.stdlib_module_names)

# 模块名 -> PyPI 发行包名（不一致的映射）
MODULE_TO_DIST = {
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "bcrypt": "bcrypt",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "lxml": "lxml",
    "jwt": "pyjwt",
    "fitz": "pymupdf",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "readability": "readability-lxml",
    "hanziconv": "hanziconv",
    "datrie": "datrie",
}


def local_modules() -> set:
    """app 目录下可直接 import 的本地包/模块名"""
    names = {p.stem for p in APP_DIR.glob("*.py")}
    names |= {p.name for p in APP_DIR.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    return names


def iter_imports():
    for py in APP_DIR.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield py, alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    yield py, node.module.split(".")[0]


def declared() -> set:
    names = set()
    for line in REQ_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line.split("[")[0]
        for sep in ("==", ">=", "<=", "~=", ">", "<", ";"):
            name = name.split(sep)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def main() -> int:
    local = local_modules()
    used = {}
    for path, mod in iter_imports():
        if mod in STDLIB or mod in local or mod.startswith("_"):
            continue
        used.setdefault(mod, set()).add(str(path.relative_to(APP_DIR)))

    decl = declared()
    missing, unmapped = [], []
    for mod, files in sorted(used.items()):
        dist = MODULE_TO_DIST.get(mod, mod).lower().replace("_", "-")
        if dist not in decl:
            (missing if mod in MODULE_TO_DIST or True else unmapped).append((mod, dist, sorted(files)[:2]))

    print("=" * 60)
    print(f"代码实际使用的第三方顶层模块: {len(used)} 个")
    print("=" * 60)
    for mod in sorted(used):
        print(f"  {mod:<24} <- {sorted(used[mod])[0]}")

    print()
    print("=" * 60)
    print(f"⚠️  代码用到但 requirements.txt 未声明: {len(missing)} 个")
    print("=" * 60)
    for mod, dist, files in missing:
        print(f"  {mod:<20} 应为发行包: {dist:<24} 例: {files[0]}")
    if not missing:
        print("  （无，依赖完整）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
