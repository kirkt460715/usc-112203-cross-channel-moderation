"""试行名单与预先登记观测窗口的效果评估。

原则：
- 效果只在各房保留独立覆盖的段上估计；跨房共用（actual_part 起）、
  联合干预覆盖、或对照房实际领到相同措施的段，只可用于安全巡查，
  不得记为效果证据；
- 可比段数不足 effect_policy.minimum_comparable_parts 时推迟裁定，
  并列出待补的段；
- 对照被消耗（失去独立覆盖）时，若 effect_policy.require_independent_control
  为真，则该对比只能推迟或显式放弃，不得用全局平均替代。
"""
from __future__ import annotations

from .coverage import comparable_parts
from .exposure import ExposureResult
from .loader import Model


def _room_protection(model: Model, room: str, part: int) -> str | None:
    cov = model.coverage_by_cell.get((room, part))
    return cov['protection'] if cov else None


def _joint_parts(model: Model, room: str) -> dict[int, str]:
    """房间被已批准联合干预覆盖的段 -> 干预 id。"""
    out = {}
    for j in model.joint_actions:
        if not j['approved']:
            continue
        if room in j['rooms']:
            for part in range(j['start'], j['end'] + 1):
                out[part] = j['id']
    return out


def evaluate_trials(model: Model, exposure: ExposureResult) -> list[dict]:
    policy = model.effect_policy
    min_parts = policy['minimum_comparable_parts']
    trials: dict[str, list[dict]] = {}
    for w in model.comparison_windows:
        trials.setdefault(w['trial'], []).append(w)

    reports = []
    for trial_id, windows in sorted(trials.items()):
        treated = [w for w in windows if w['arm'] != 'standard']
        controls = [w for w in windows if w['arm'] == 'standard']
        window = windows[0]['window']
        win_parts = list(range(window[0], window[1] + 1))
        data_parts = [p for p in win_parts if p in model.parts]
        pending_parts = [p for p in win_parts if p not in model.parts]

        room_status = {}
        for w in windows:
            room = w['room']
            joints = _joint_parts(model, room)
            safety_only = {}
            for part in data_parts:
                reasons = []
                if part not in w['independent']:
                    reasons.append('outside_independent')
                if part in joints:
                    reasons.append(f'joint_action:{joints[part]}')
                if (w['actual_part'] is not None and part >= w['actual_part']
                        and w['arm'] == 'standard'):
                    reasons.append('cross_room_sharing')
                if (w['arm'] == 'standard'
                        and _room_protection(model, room, part) not in (None, 'standard')):
                    reasons.append('received_treatment')
                if reasons:
                    safety_only[part] = reasons
            room_status[room] = {
                'arm': w['arm'],
                'assigned_part': w['assigned_part'],
                'actual_part': w['actual_part'],
                'independent': w['independent'],
                'safety_only': safety_only,
            }

        contrasts = []
        for t in treated:
            for c in controls:
                contrasts.append(_contrast(model, exposure, t, c, win_parts,
                                           min_parts, pending_parts))
        reports.append({
            'trial': trial_id,
            'window': window,
            'pending_parts': pending_parts,
            'rooms': room_status,
            'contrasts': contrasts,
        })
    return reports


def _contrast(model: Model, exposure: ExposureResult, treated: dict,
              control: dict, win_parts: list[int], min_parts: int,
              pending_parts: list[int]) -> dict:
    tr, cr = treated['room'], control['room']
    shared = sorted(set(treated['independent']) & set(control['independent'])
                    & set(win_parts))
    ok, blocked = comparable_parts(model, tr, cr, shared)
    # 覆盖率口径差异必须显式记录
    prob_notes = []
    for part in ok:
        pa = model.coverage_by_cell[(tr, part)]['probability']
        pb = model.coverage_by_cell[(cr, part)]['probability']
        if pa != pb:
            prob_notes.append(
                f'part {part}: {tr} 覆盖率 {pa} vs {cr} {pb}，只可用率比较')

    result = {
        'treated': tr, 'control': cr,
        'comparable_parts': ok,
        'blocked_parts': blocked,
        'coverage_notes': prob_notes,
        'status': 'evaluable',
        'effect': None,
        'defer_reasons': [],
    }
    if len(ok) < min_parts:
        result['status'] = 'deferred'
        if len(ok) < min_parts:
            result['defer_reasons'].append(
                f'可比独立段仅 {len(ok)} 段，不足 {min_parts} 段')
        if pending_parts:
            result['defer_reasons'].append(
                f'窗口内尚有段未产生数据: {pending_parts}')
        return result

    pre_parts = [p for p in model.parts if p < min(win_parts)]
    pre_ok, pre_blocked = comparable_parts(model, tr, cr, pre_parts)
    effect = {
        'metric': 'harm_minutes_per_visit_minute',
        'pre_parts': pre_ok,
        'pre_blocked': pre_blocked,
        'treated': _series(exposure, tr, pre_ok, ok),
        'control': _series(exposure, cr, pre_ok, ok),
    }
    t_pre, t_post = effect['treated']['pre_mean'], effect['treated']['post_mean']
    c_pre, c_post = effect['control']['pre_mean'], effect['control']['post_mean']
    if None not in (t_pre, t_post, c_pre, c_post):
        effect['did'] = (t_post - t_pre) - (c_post - c_pre)
    else:
        effect['did'] = None
        result['status'] = 'deferred'
        result['defer_reasons'].append('前期或干预期缺少可用观看数据')
    result['effect'] = effect
    return result


def _series(exposure: ExposureResult, room: str, pre_parts: list[int],
            post_parts: list[int]) -> dict:
    def mean_of(parts):
        vals = []
        for p in parts:
            cell = exposure.cell(room, p)
            if cell is None:
                continue
            rate = cell.harm_minutes_per_visit_minute()
            if rate is not None:
                vals.append(rate)
        return sum(vals) / len(vals) if vals else None

    return {
        'pre_mean': mean_of(pre_parts),
        'post_mean': mean_of(post_parts),
        'post_by_part': {p: (exposure.cell(room, p)
                             and exposure.cell(room, p).harm_minutes_per_visit_minute())
                         for p in post_parts},
    }
