"""值班长滚动报告：汇总暴露、覆盖可比性、试行评估、人力与决策。"""
from __future__ import annotations

from .coverage import coverage_table
from .decisions import appeal_impact, build_decisions
from .exposure import (HARM_CATEGORIES, audience_migration, compute_exposure,
                       content_spread, language_context_exposure)
from .loader import Model
from .staffing import (evaluate_joint_candidate, protection_consistency,
                       staffing_table)
from .state import (CATEGORIES, EXPLICIT_HARM, ORDINARY, SPILLOVER,
                    UNDETERMINED, UNVERIFIED_PATH, Lineage, ReviewState)
from .trials import evaluate_trials

CATEGORY_LABELS = {
    EXPLICIT_HARM: '显害',
    SPILLOVER: '溢出',
    UNVERIFIED_PATH: '未证实路径',
    UNDETERMINED: '未判定',
    ORDINARY: '普通',
}


def build_report(model: Model, as_of: int) -> dict:
    state = ReviewState(model.records['reviews'], as_of)
    lineage = Lineage(model.records['posts'])
    exposure = compute_exposure(model, state, lineage)
    trials = evaluate_trials(model, exposure)
    staff_parts = sorted({p for s in model.shifts for p in s['parts']}
                         | set(model.parts))
    staffing = staffing_table(model, staff_parts)
    decisions = build_decisions(model, exposure, state, lineage, trials,
                                staffing, as_of)

    cells = []
    for (room, part), cell in sorted(exposure.cells.items()):
        cells.append({
            'room': room, 'part': part,
            'visit_minutes': cell.visit_minutes,
            'viewers': len(cell.viewers),
            'harm_viewers': len(cell.harm_viewers),
            'minutes': {c: cell.category_minutes[c] for c in CATEGORIES},
            'harm_viewer_list': sorted(cell.harm_viewers),
        })

    cross_room = {}
    for (viewer, part), vpe in sorted(exposure.viewer_parts.items()):
        repeats = vpe.cross_room_repeats
        if repeats:
            cross_room.setdefault(part, {})[viewer] = {
                root: sorted(rooms) for root, rooms in repeats.items()}

    suspected = {}
    for (viewer, part), vpe in sorted(exposure.viewer_parts.items()):
        if vpe.suspected_rooms:
            suspected.setdefault(part, {})[viewer] = {
                src: sorted(rooms) for src, rooms in vpe.suspected_rooms.items()}

    return {
        'as_of': as_of,
        'parts': model.parts,
        'exposure_cells': cells,
        'language_context': language_context_exposure(model, exposure),
        'cross_room_repeats': cross_room,
        'unverified_path_exposure': suspected,
        'migration_main_audience': audience_migration(model, exposure, 'main', 4),
        'content_spread': {p: content_spread(model, exposure, lineage, p)
                           for p in model.parts},
        'coverage': coverage_table(model, state),
        'trials': trials,
        'staffing': {p: {'on_duty': t['on_duty'],
                         'rooms': {r: {'covering': e['covering'],
                                       'gap': e['gap'],
                                       'commitments': [
                                           f"{c['kind']}:{c['id']}:{c['action']}"
                                           for c in e['commitments']]}
                                   for r, e in t['rooms'].items()}}
                     for p, t in staffing.items()},
        'joint_candidates': [evaluate_joint_candidate(model, j, staff_parts)
                             for j in model.joint_actions if not j['approved']],
        'protection_consistency': protection_consistency(model),
        'appeal_impacts': appeal_impact(model, lineage, as_of),
        'decisions': decisions,
    }


def _fmt_minutes(minutes: dict) -> str:
    parts = []
    for cat in (EXPLICIT_HARM, SPILLOVER, UNVERIFIED_PATH, UNDETERMINED):
        v = minutes.get(cat, 0)
        if v:
            parts.append(f'{CATEGORY_LABELS[cat]}{v}')
    return ' '.join(parts) if parts else '—'


def render_text(report: dict) -> str:
    lines = []
    add = lines.append
    add(f"== 巡查滚动分析（截至第 {report['as_of']} 段）==")
    add('')
    add('一、各房各段有害暴露（分钟：显害/溢出；未判定与未证实路径单列）')
    by_room: dict[str, list[dict]] = {}
    for c in report['exposure_cells']:
        by_room.setdefault(c['room'], []).append(c)
    for room, cells in sorted(by_room.items()):
        add(f'  [{room}]')
        for c in cells:
            add(f"    段{c['part']}: 观众{c['viewers']} 涉害观众{c['harm_viewers']} "
                f"| {_fmt_minutes(c['minutes'])}")
    add('')
    add('二、语言环境段暴露（跨语种观众会重复计入各环境，段间不可相加）')
    for ctx, by_part in sorted(report['language_context'].items()):
        row = []
        for part in sorted(by_part):
            m = by_part[part]
            harm = m[EXPLICIT_HARM] + m[SPILLOVER]
            row.append(f'段{part}:害{harm}/未判{m[UNDETERMINED]}')
        add(f'  {ctx}: ' + '  '.join(row))
    add('')
    add('三、跨房重复看到同一有害链的观众（同链多房）')
    if report['cross_room_repeats']:
        for part, viewers in sorted(report['cross_room_repeats'].items()):
            add(f'  段{part}: {len(viewers)} 名观众跨房重见 '
                + ', '.join(sorted(viewers)[:6]) + (' …' if len(viewers) > 6 else ''))
    else:
        add('  无')
    add('')
    add('四、主房基准观众群（段4）暴露去向：总量 vs 主房内')
    mig = report['migration_main_audience']
    add(f"  基准观众 {mig['cohort_size']} 人")
    for part, rooms in mig['harm_minutes_by_part'].items():
        total = rooms.pop('_total')
        main = rooms.get('main', 0)
        elsewhere = total - main
        add(f'  段{part}: 总有害暴露 {total} 分钟 = 主房 {main} + 他房 {elsewhere}'
            f'  去向 { {r: m for r, m in sorted(rooms.items())} }')
    add('')
    add('五、覆盖可比性警示')
    for c in report['coverage']:
        if c['flags']:
            add(f"  {c['room']} 段{c['part']}: {', '.join(c['flags'])}"
                f"（复核 {c['reviewed']} 有害 {c['harmful']}）")
    add('')
    add('六、试行效果（只用保留独立覆盖的段；被污染段仅作安全巡查）')
    for t in report['trials']:
        add(f"  {t['trial']} 窗口{t['window']}"
            + (f" 待补段{t['pending_parts']}" if t['pending_parts'] else ''))
        for room, st in sorted(t['rooms'].items()):
            so = st['safety_only']
            so_txt = (f" 仅安全巡查段: {sorted(so)}" if so else '')
            add(f"    {room}({st['arm']}) 独立段{st['independent']}{so_txt}")
        for c in t['contrasts']:
            if c['status'] == 'evaluable' and c['effect'] and c['effect']['did'] is not None:
                e = c['effect']
                add(f"    {c['treated']} vs {c['control']} 可比段{c['comparable_parts']}: "
                    f"DiD={e['did']:.3f} （处理 {e['treated']['pre_mean']:.3f}→"
                    f"{e['treated']['post_mean']:.3f}, 对照 {e['control']['pre_mean']:.3f}→"
                    f"{e['control']['post_mean']:.3f}）")
            else:
                add(f"    {c['treated']} vs {c['control']}: 推迟裁定 — "
                    + '；'.join(c['defer_reasons']))
            for note in c['coverage_notes']:
                add(f'      注意: {note}')
    add('')
    add('七、人力缺口（承诺 vs 语种/轮值/锁定）')
    any_gap = False
    for part, t in sorted(report['staffing'].items()):
        for room, e in sorted(t['rooms'].items()):
            if e['gap']:
                any_gap = True
                add(f"  段{part} {room}: 缺口 覆盖={e['covering']} 承诺={e['commitments']}")
    if not any_gap:
        add('  无缺口')
    if report['protection_consistency']:
        add('  承诺与实际保护不符:')
        for m in report['protection_consistency']:
            add(f"    {m['room']} 段{m['part']}: {m['issue']}"
                f"（认领 {m['claimed'] or '无'}，实际 {m['actual']}）")
    add('')
    add('八、候选联合干预评估')
    for j in report['joint_candidates']:
        add(f"  {j['joint']}: 冲突 {len(j['conflicts'])} 项")
        for c in j['conflicts']:
            add(f'    - {c}')
        for g, rooms in sorted(j['staffing_gaps'].items()):
            add(f'    - 人力缺口 段{g}: {rooms}')
    add('')
    add('九、申诉影响')
    for a in report['appeal_impacts']:
        add(f"  {a['appeal']}: {a['post']}({a['room']}段{a['part']}) "
            f"第{a['recorded_part']}段复核→{a['new_verdict']}")
        for pid, rc in sorted(a['reclassified'].items()):
            add(f"    重分类 {pid}({rc['room']}段{rc['part']}): "
                f"{CATEGORY_LABELS.get(rc['from'], rc['from'])}→"
                f"{CATEGORY_LABELS.get(rc['to'], rc['to'])}")
        for cell, delta in sorted(a['harm_minutes_delta'].items()):
            add(f'    有害暴露分钟变化 {cell}: {delta:+d}')
        remaining = {k: v for k, v in a['remaining_independent_sources'].items() if v}
        add(f'    仍有独立有害来源的单元: {remaining if remaining else "无"}')
    add('')
    add('十、干预状态清单（暂停/保留巡视/推迟裁定）')
    for d in report['decisions']:
        add(f"  [{d['status']}] {d['kind']}:{d['id']} — {d['rationale']}")
    return '\n'.join(lines)
