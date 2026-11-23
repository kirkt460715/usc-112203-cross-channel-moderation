"""值班人力与保护承诺。

约束来源：
- 语种能力：值班员 languages 须覆盖房间语种（混语房须同时覆盖）；
- 轮值时间：只在 shifts.parts 列出的段在岗；
- 保护承诺：locked_room/locked_until 期间不可调离该房；
  policies 认领的窗口内每个房间须满足 minimum 在岗；
  已批准联合干预的每个房间至少 1 名合格在岗。

转班/共管评估时，原房最低在岗能力必须先保住，再谈能腾出多少人。
"""
from __future__ import annotations

from .loader import Model


def qualified(model: Model, staff: dict, room: str) -> bool:
    return model.room_languages(room) <= set(staff['languages'])


def on_duty(model: Model, part: int) -> list[dict]:
    return [s for s in model.shifts if part in s['parts']]


def locked_to(staff: dict, part: int) -> str | None:
    if staff['locked_room'] and staff['locked_until'] is not None \
            and part <= staff['locked_until']:
        return staff['locked_room']
    return None


def active_commitments(model: Model, part: int) -> list[dict]:
    """该段生效的人员承诺：policies 窗口 + 已批准联合干预。"""
    out = []
    for p in model.policies:
        if p['starts'] <= part <= p['ends']:
            for room in p['rooms']:
                out.append({'kind': 'policy', 'id': p['id'], 'room': room,
                            'minimum': p['minimum'], 'action': p['action']})
    for j in model.joint_actions:
        if j['approved'] and j['start'] <= part <= j['end']:
            for room in j['rooms']:
                out.append({'kind': 'joint', 'id': j['id'], 'room': room,
                            'minimum': 1, 'action': 'joint_patrol'})
    return out


def staffing_table(model: Model, parts: list[int]) -> dict[int, dict]:
    """逐段的人力覆盖表与缺口。"""
    table = {}
    for part in parts:
        staff = on_duty(model, part)
        availability = {}
        for s in staff:
            lock = locked_to(s, part)
            availability[s['id']] = {
                'languages': s['languages'],
                'locked_to': lock,
                'free_rooms': ([lock] if lock else [
                    r for r in model.rooms if qualified(model, s, r)]),
        }
        rooms_status = {}
        for c in active_commitments(model, part):
            room = c['room']
            entry = rooms_status.setdefault(room, {
                'commitments': [], 'covering': [], 'minimum': 0, 'gap': False})
            entry['commitments'].append(c)
            entry['minimum'] = max(entry['minimum'], c['minimum'])
        for room, entry in rooms_status.items():
            covering = []
            for sid, a in availability.items():
                if a['locked_to'] and a['locked_to'] != room:
                    continue
                if qualified(model, {'languages': a['languages']}, room):
                    covering.append(sid)
            entry['covering'] = covering
            entry['gap'] = len(covering) < entry['minimum']
        table[part] = {'on_duty': sorted(availability), 'rooms': rooms_status,
                       'availability': availability}
    return table


def evaluate_joint_candidate(model: Model, joint: dict,
                             parts: list[int]) -> dict:
    """评估候选联合干预：人力可行性 + 对照消耗。

    - 人力：窗口内每段每房至少 1 名合格在岗，且不破坏既有承诺；
    - 对照：keep 房须保留 minimum_control_parts 个独立段；
      若候选房本身是试行对照，须显式列出被消耗的试行结论。
    """
    conflicts = []
    staffing = staffing_table(model, parts)
    gaps = {}
    for part in range(joint['start'], joint['end'] + 1):
        if part not in staffing:
            gaps[part] = ['无排班数据']
            continue
        for room in joint['rooms']:
            entry = staffing[part]['rooms'].get(room)
            avail = staffing[part]['availability']
            n = sum(1 for a in avail.values()
                    if (not a['locked_to'] or a['locked_to'] == room)
                    and set(model.room_languages(room)) <= set(a['languages']))
            existing_min = entry['minimum'] if entry else 0
            need = max(1, existing_min)
            if n < need:
                gaps.setdefault(part, []).append(
                    f'{room}: 合格在岗 {n} < 所需 {need}')

    # 对照消耗：候选房与 keep 房在试行名单中的独立段
    consumed = []
    for room in set(joint['rooms']) | set(joint.get('keep', [])):
        for w in model.comparison_windows:
            if w['room'] != room:
                continue
            overlap = [p for p in w['independent']
                       if joint['start'] <= p <= joint['end']]
            if not overlap:
                continue
            remaining = [p for p in w['independent']
                         if not (joint['start'] <= p <= joint['end'])]
            need = (joint['minimum_control_parts']
                    if room in joint.get('keep', []) else 0)
            consumed.append({
                'trial': w['trial'], 'room': room, 'arm': w['arm'],
                'lost_independent_parts': overlap,
                'remaining_independent_parts': remaining,
                'keep_required_parts': need,
                'keep_satisfied': len(remaining) >= need,
            })
    for room in joint.get('keep', []):
        if room in joint['rooms']:
            conflicts.append(f'keep 房 {room} 同时列在共管房间中，约定自相矛盾')
    if not joint['approved']:
        for c in consumed:
            if c['arm'] == 'standard' and not c['keep_satisfied']:
                conflicts.append(
                    f"批准 {joint['id']} 将消耗 {c['trial']} 对照 {c['room']} "
                    f"的独立段 {c['lost_independent_parts']}，剩余不足 "
                    f"{c['keep_required_parts']} 段；须显式放弃该对照结论才能批准")
            elif c['arm'] != 'standard' and c['lost_independent_parts']:
                conflicts.append(
                    f"批准 {joint['id']} 将使 {c['trial']} 处理房 {c['room']} "
                    f"在段 {c['lost_independent_parts']} 上叠加共管，"
                    f"效果归因失效；须显式放弃该试行结论才能批准")
    return {'joint': joint['id'], 'approved': joint['approved'],
            'staffing_gaps': gaps, 'control_consumption': consumed,
            'conflicts': conflicts}


def protection_consistency(model: Model) -> list[dict]:
    """认领的干预窗口与房间实际启用保护的比对。

    coverage 登记的是房间实际生效的保护；policies 是认领的承诺。
    只有房间级保护动作（如 slow）才应与 coverage 对照；review 一类
    复核台行动不改变房间保护状态，不参与比对。
    两者不符的单元必须显式列出：承诺未兑现或实际保护无承诺，
    都会影响效果段与安全巡查段的划分。
    """
    room_level_actions = {'slow'}
    mismatches = []
    claimed: dict[tuple[str, int], list[str]] = {}
    for p in model.policies:
        if p['action'] not in room_level_actions:
            continue
        for room in p['rooms']:
            for part in range(p['starts'], p['ends'] + 1):
                claimed.setdefault((room, part), []).append(
                    f"{p['id']}:{p['action']}")
    for (room, part), cov in sorted(model.coverage_by_cell.items()):
        actual = cov['protection']
        claims = claimed.get((room, part), [])
        if claims and actual not in {c.split(':', 1)[1] for c in claims}:
            mismatches.append({
                'room': room, 'part': part, 'claimed': claims,
                'actual': actual,
                'issue': '承诺的干预未在实际保护中生效'})
        elif not claims and actual != 'standard':
            mismatches.append({
                'room': room, 'part': part, 'claimed': [],
                'actual': actual,
                'issue': '实际启用的保护无对应认领窗口'})
    return mismatches
