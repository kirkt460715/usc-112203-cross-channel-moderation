"""申诉影响与干预决策状态。

每个在册干预处于三种状态之一，且都可查到人员覆盖与观察范围：
- pause：暂停干预（窗口结束且剩余风险不足以支撑继续）；
- keep_patrol：保留巡视（仍有独立有害来源或暴露未降）；
- defer_ruling：推迟效果裁定（可比证据不足，继续按安全巡查记录）。

申诉撤销只重定被撤销帖及其已知链下游的类别；其他房间的独立
有害来源仍然成立，联合干预不因此自动结束。
"""
from __future__ import annotations

from .exposure import ExposureResult, compute_exposure
from .loader import Model
from .state import (EXPLICIT_HARM, Lineage, ReviewState, categorize)


def appeal_impact(model: Model, lineage: Lineage, as_of: int) -> list[dict]:
    """逐条申诉的重分类影响：以"含该申诉"与"剔除该申诉"两种状态对比。"""
    appeals = [r for r in model.records['reviews']
               if r['source'] == 'appeal' and r['recorded_part'] <= as_of]
    impacts = []
    for appeal in appeals:
        with_appeal = ReviewState(model.records['reviews'], as_of)
        without = ReviewState(
            [r for r in model.records['reviews'] if r['id'] != appeal['id']],
            as_of)
        post = model.posts[appeal['post']]
        room, part = post['room'], post['part']
        # 受影响的下游：已知链上以该帖为祖先的所有帖
        descendants = [p['id'] for p in model.records['posts']
                       if appeal['post'] in lineage.known_ancestors(p['id'])]
        affected = [appeal['post']] + descendants
        reclassified = {}
        for pid in affected:
            p = model.posts[pid]
            before = categorize(p, without, lineage).category
            after = categorize(p, with_appeal, lineage).category
            if before != after:
                reclassified[pid] = {'from': before, 'to': after,
                                     'room': p['room'], 'part': p['part']}
        # 暴露分钟变化（按含/不含申诉两套状态各算一次）
        exp_with = compute_exposure(model, with_appeal, lineage)
        exp_without = compute_exposure(model, without, lineage)
        harm_delta = {}
        for (r, pt), cell in exp_with.cells.items():
            before_cell = exp_without.cells[(r, pt)]
            delta = cell.harm_minutes - before_cell.harm_minutes
            if delta:
                harm_delta[f'{r}#{pt}'] = delta
        # 同房同段是否仍有独立有害来源（决定是否可自动松绑）
        remaining = {}
        for (r, pt) in {(model.posts[d]['room'], model.posts[d]['part'])
                        for d in affected}:
            sources = [p['id'] for p in model.posts_by_cell[(r, pt)]
                       if categorize(p, with_appeal, lineage).category
                       == EXPLICIT_HARM]
            remaining[f'{r}#{pt}'] = sources
        impacts.append({
            'appeal': appeal['id'], 'post': appeal['post'],
            'room': room, 'part': part,
            'recorded_part': appeal['recorded_part'],
            'new_verdict': appeal['verdict'],
            'reclassified': reclassified,
            'harm_minutes_delta': harm_delta,
            'remaining_independent_sources': remaining,
        })
    return impacts


def build_decisions(model: Model, exposure: ExposureResult,
                    state: ReviewState, lineage: Lineage,
                    trial_reports: list[dict], staffing: dict,
                    as_of: int) -> list[dict]:
    decisions = []
    decisions.extend(_policy_decisions(model, exposure, state, lineage,
                                       staffing, as_of))
    decisions.extend(_joint_decisions(model, exposure, state, lineage,
                                      staffing, as_of))
    decisions.extend(_trial_decisions(trial_reports, staffing))
    return decisions


def _personnel(staffing: dict, room: str, parts: list[int]) -> dict:
    out = {}
    for part in parts:
        tbl = staffing.get(part)
        if not tbl:
            continue
        entry = tbl['rooms'].get(room)
        if entry:
            out[part] = {'covering': entry['covering'], 'gap': entry['gap']}
    return out


def _cell_explicit_sources(model: Model, state: ReviewState,
                           lineage: Lineage, room: str, part: int) -> list[str]:
    return [p['id'] for p in model.posts_by_cell.get((room, part), [])
            if categorize(p, state, lineage).category == EXPLICIT_HARM]


def _policy_decisions(model, exposure, state, lineage, staffing, as_of):
    out = []
    for pol in model.policies:
        parts = list(range(pol['starts'], pol['ends'] + 1))
        active = pol['starts'] <= as_of <= pol['ends']
        ended = as_of > pol['ends']
        scope = {room: [p for p in parts if p in model.parts]
                 for room in pol['rooms']}
        sources = {room: {p: _cell_explicit_sources(model, state, lineage, room, p)
                          for p in scope[room]}
                   for room in pol['rooms']}
        harm_by_room = {
            room: {p: (exposure.cell(room, p).harm_minutes
                       if exposure.cell(room, p) else 0)
                   for p in scope[room]}
            for room in pol['rooms']}
        still_harmful = any(s for by_part in sources.values()
                            for s in by_part.values())
        if ended and not still_harmful:
            status = 'pause'
            rationale = '窗口已结束且房内无有效独立有害来源，可暂停'
        elif ended or not active:
            status = 'keep_patrol'
            rationale = ('窗口已结束但房内仍有有效有害暴露，'
                         '建议保留巡视或交班，不得仅因窗口到期自动结束')
        else:
            status = 'keep_patrol'
            rationale = '窗口执行中，按生效期维持'
        out.append({
            'kind': 'policy', 'id': pol['id'], 'action': pol['action'],
            'rooms': pol['rooms'], 'window': [pol['starts'], pol['ends']],
            'status': status, 'rationale': rationale,
            'independent_sources': sources,
            'harm_minutes': harm_by_room,
            'personnel': {room: _personnel(staffing, room, scope[room])
                          for room in pol['rooms']},
            'observation_scope': scope,
        })
    return out


def _joint_decisions(model, exposure, state, lineage, staffing, as_of):
    out = []
    for j in model.joint_actions:
        if not j['approved']:
            continue
        parts = [p for p in range(j['start'], j['end'] + 1) if p in model.parts]
        sources = {room: {p: _cell_explicit_sources(model, state, lineage, room, p)
                          for p in parts}
                   for room in j['rooms']}
        rooms_with_sources = [room for room, by_part in sources.items()
                              if any(by_part.values())]
        if rooms_with_sources:
            status = 'keep_patrol'
            rationale = (f'{rooms_with_sources} 仍有独立有害来源，'
                         '共管继续；单一来源被撤销不自动结束共管')
        else:
            status = 'pause'
            rationale = '共管范围内已无有效独立有害来源，可评估暂停'
        out.append({
            'kind': 'joint', 'id': j['id'], 'rooms': j['rooms'],
            'window': [j['start'], j['end']],
            'keep_controls': j.get('keep', []),
            'status': status, 'rationale': rationale,
            'independent_sources': sources,
            'personnel': {room: _personnel(staffing, room, parts)
                          for room in j['rooms']},
            'observation_scope': {room: parts for room in j['rooms']},
        })
    return out


def _trial_decisions(trial_reports, staffing):
    out = []
    for report in trial_reports:
        for c in report['contrasts']:
            if c['status'] == 'evaluable':
                status = 'evaluable'
                rationale = '可比独立段满足下限，可给出效果估计'
            else:
                status = 'defer_ruling'
                rationale = '；'.join(c['defer_reasons'])
            out.append({
                'kind': 'trial_contrast',
                'id': f"{report['trial']}:{c['treated']}vs{c['control']}",
                'trial': report['trial'],
                'treated': c['treated'], 'control': c['control'],
                'status': status, 'rationale': rationale,
                'comparable_parts': c['comparable_parts'],
                'effect': c['effect'],
                'observation_scope': {
                    c['treated']: c['comparable_parts'],
                    c['control']: c['comparable_parts'],
                },
            })
    return out
