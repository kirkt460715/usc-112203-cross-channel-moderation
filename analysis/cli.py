"""命令行入口：python3 -I report.py --records records [--as-of N] [--format json|text]"""
from __future__ import annotations

import argparse
import json
import sys

from .loader import build_model
from .report import build_report, render_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='赛事跨频道互动巡查滚动分析')
    parser.add_argument('--records', required=True, help='记录目录路径')
    parser.add_argument('--as-of', type=int, default=None,
                        help='只采纳 recorded_part <= N 的复核结论（默认最新）')
    parser.add_argument('--format', choices=('text', 'json'), default='text')
    args = parser.parse_args(argv)

    model = build_model(args.records)
    as_of = args.as_of if args.as_of is not None else max(model.parts)
    report = build_report(model, as_of)
    if args.format == 'json':
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2,
                  default=sorted)
        sys.stdout.write('\n')
    else:
        print(render_text(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
