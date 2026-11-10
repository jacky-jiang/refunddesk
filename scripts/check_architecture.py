"""Static Python import boundary checks. Not a security sandbox or semantic proof."""
import argparse
import ast
import sys
from importlib.util import resolve_name
from pathlib import Path

ALLOWED = {
    'domain': {'domain'},
    'application': {'domain', 'application'},
    'infrastructure': {'domain', 'application', 'infrastructure'},
    'adapters': {'application', 'adapters'},
    'workers': {'application', 'workers'},
    'agents': {'agents'},
    'entrypoints': {'domain', 'application', 'infrastructure', 'adapters',
                    'workers', 'agents', 'entrypoints', 'db', 'settings', 'events'},
}
# Exact I0 foundation modules; they are not importable by new business layers.
LEGACY = {'db.py', 'settings.py', 'events.py'}
BLOCK_EXTERNAL = {
    'adapters': {'sqlalchemy', 'psycopg', 'psycopg2', 'asyncpg', 'alembic'},
    'workers': {'sqlalchemy', 'psycopg', 'psycopg2', 'asyncpg', 'alembic'},
    'agents': {'sqlalchemy', 'psycopg', 'psycopg2', 'asyncpg', 'alembic'},
}


def imported_names(node: ast.AST, package: str) -> list[str]:
    if isinstance(node, ast.Import):
        return [x.name for x in node.names]
    if isinstance(node, ast.ImportFrom):
        module = node.module or ''
        if node.level:
            module = resolve_name('.' * node.level + module, package)
        # Include children to catch `from refunddesk import infrastructure`.
        return [module] + [module + '.' + x.name for x in node.names]
    return []


def check(root: Path) -> tuple[list[str], int]:
    base = root / 'src/refunddesk'
    if not base.is_dir():
        return ['缺少 src/refunddesk；请在项目根目录执行，不能将空目录视为检查通过。'], 0
    errors = []
    count = 0
    for path in sorted(base.rglob('*.py')):
        count += 1
        relative = path.relative_to(base)
        parts = relative.parts
        if len(parts) == 1 and path.name in LEGACY:
            continue
        layer = parts[0] if len(parts) > 1 else '__root__'
        module_parts = ['refunddesk', *relative.with_suffix('').parts]
        package = '.'.join(module_parts[:-1])
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except SyntaxError as exc:
            errors.append(f'{relative}: 语法错误 {exc}')
            continue
        if layer not in ALLOWED and relative.as_posix() != '__init__.py':
            errors.append(f'{relative}: 未登记的代码层；按架构落位或通过 ADR 更新检查器')
        for node in ast.walk(tree):
            try:
                names = imported_names(node, package)
            except (ValueError, ImportError) as exc:
                errors.append(f'{relative}:{node.lineno}: 非法相对导入 {exc}')
                continue
            for name in names:
                bits = name.split('.')
                first = bits[0]
                reason = ''
                if first == 'refunddesk' and len(bits) > 1:
                    target = bits[1]
                    if target not in ALLOWED.get(layer, set()):
                        reason = f'{layer} 不得依赖 {target}'
                    elif layer == 'adapters' and target == 'adapters' and len(parts) > 2 and len(bits) > 2 and parts[1] != bits[2]:
                        reason = 'HTTP/MCP 适配器不得交叉依赖'
                elif first != 'refunddesk':
                    if layer in {'domain', 'application'} and first not in sys.stdlib_module_names:
                        reason = f'{layer} 不得依赖第三方包 {first}'
                    elif first in BLOCK_EXTERNAL.get(layer, set()):
                        reason = f'{layer} 不得访问数据库驱动 {first}'
                if reason:
                    errors.append(f'{relative}:{node.lineno}: {reason} ({name})')
    if not count:
        errors.append('未发现 Python 源文件')
    return sorted(set(errors)), count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    errors, count = check(args.root)
    for error in errors:
        print(error)
    print(f'扫描 {count} 个 Python 文件；{len(errors)} 个问题。'
          '只检查静态导入；不验证事务、授权、动态导入或前端依赖。')
    return int(bool(errors))


if __name__ == '__main__':
    raise SystemExit(main())
