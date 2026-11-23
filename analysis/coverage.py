"""抽样覆盖与可比性。

巡查密度会改变观测：受理较多的房间复核密度更高，原始计数不可直接
横向或前后比较。本模块把每个（房， 段）单元的覆盖状态显式化：

- observed=false：台账缺失，记 missing，不参与任何比较但也不当作零；
- probability 变化：审查强度变化，计数必须改用率（每审阅帖的有害比、
  每观看分钟的有害暴露），并在报告中标注；
- 估计总量 = 有害率 × total_posts，仅用于同一覆盖率口径下的对比。
"""
from __future__ import annotations

from .loader import Model
from .state import ReviewState


def cell_coverage(model: Model, state: ReviewState, room: str, part: int) -> dict:
    cov = model.coverage_by_cell.get((room, part))
    posts = model.posts_by_cell.get((room, part), [])
    reviewed = [p for p in posts if state.verdict(p['id']) is not None]
    harmful = [p for p in reviewed if state.verdict(p['id']) == 'harmful']
    out = {
        'room': room, 'part': part,
        'observed': bool(cov and cov['observed']),
        'probability': cov['probability'] if cov else None,
        'protection': cov['protection'] if cov else None,
        'total_posts': cov['total_posts'] if cov else None,
        'sampled_posts': len(posts),
        'reviewed': len(reviewed),
        'harmful': len(harmful),
        'flags': [],
    }
    if not out['observed']:
        out['flags'].append('missing_ledger')
    if reviewed:
        out['harm_rate'] = len(harmful) / len(reviewed)
        if cov:
            out['est_harmful_total'] = out['harm_rate'] * cov['total_posts']
    else:
        out['harm_rate'] = None
        out['est_harmful_total'] = None
    return out


def coverage_table(model: Model, state: ReviewState) -> list[dict]:
    table = []
    for (room, part) in sorted(model.coverage_by_cell):
        cell = cell_coverage(model, state, room, part)
        prev = model.coverage_by_cell.get((room, part - 1))
        if (cell['observed'] and prev and prev['observed']
                and cell['probability'] != prev['probability']):
            cell['flags'].append(
                f"coverage_changed:{prev['probability']}->{cell['probability']}")
        table.append(cell)
    return table


def comparable_parts(model: Model, room_a: str, room_b: str,
                     parts: list[int]) -> tuple[list[int], dict[int, str]]:
    """两房在给定段集合中可比的段：双方台账齐全。

    返回 (可比段, 各段排除原因)。覆盖率不同不自动排除，但调用方
    必须使用率而非计数，并把覆盖率差异写进报告。
    """
    ok, blocked = [], {}
    for part in parts:
        ca = model.coverage_by_cell.get((room_a, part))
        cb = model.coverage_by_cell.get((room_b, part))
        if not (ca and ca['observed']):
            blocked[part] = f'{room_a}:missing_ledger'
        elif not (cb and cb['observed']):
            blocked[part] = f'{room_b}:missing_ledger'
        else:
            ok.append(part)
    return ok, blocked
