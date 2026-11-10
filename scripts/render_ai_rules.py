"""Deterministic rule renderer; refuses to overwrite files it does not own."""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = '<!-- GENERATED: refunddesk-ai-rules; edit .ai/rules.json -->'
TARGETS = ['AGENTS.md', 'CLAUDE.md', '.github/copilot-instructions.md',
           'docs/ai-coding-guidelines.md', '.cursor/rules/refunddesk.mdc']


def render(root: Path) -> dict[str, str]:
    data = json.loads((root / '.ai/rules.json').read_text(encoding='utf-8'))
    body = MARKER + '\n# RefundDesk 编码规则 ' + data['version'] + '\n\n'
    body += '\n\n'.join('## ' + s['id'] + ' ' + s['title'] + '\n\n' + s['text']
                        for s in data['sections']) + '\n'
    return {name: ('---\ndescription: RefundDesk architecture and coding rules\n'
                   'alwaysApply: true\n---\n' if name.endswith('.mdc') else '') + body
            for name in TARGETS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = render(ROOT)
    changed = [name for name, value in expected.items()
               if not (ROOT / name).exists() or
               (ROOT / name).read_text(encoding='utf-8') != value]
    if args.check:
        print('规则同步通过' if not changed else '规则不同步: ' + ', '.join(changed))
        return int(bool(changed))
    # Preflight all files before writing any.
    for name in changed:
        path = ROOT / name
        if path.exists() and MARKER not in path.read_text(encoding='utf-8'):
            raise SystemExit(f'拒绝覆盖现有非生成文件: {name}；先合并其规则到源文件。')
    for name in changed:
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected[name], encoding='utf-8')
    print(f'已更新 {len(changed)} 个规则文件')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
