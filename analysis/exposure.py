"""观看暴露计量。

暴露判定：观看区间与帖子可见区间（段内半开分钟）有交集即计暴露，
暴露分钟为交集长度。观众跨房看到同一条引用链时，按链根去重计
"触达的独立有害内容"，分钟数如实累加并单独标出跨房重复。

人群口径：记录未给观众直接标注语种，按观看行为分为
- 房间受众：在某房有观看片段的观众（房间语种即受众语种环境）；
- 语言环境段：es 房 / pt 房 / 混语房中的暴露，跨语种观众会同时
  出现在多个段中，因此各段不可相加，观众级总量另行去重。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .loader import Model
from .state import (CATEGORIES, EXPLICIT_HARM, ORDINARY, SPILLOVER,
                    UNDETERMINED, UNVERIFIED_PATH, Lineage, ReviewState,
                    categorize)

HARM_CATEGORIES = (EXPLICIT_HARM, SPILLOVER)


def overlap_minutes(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    """半开区间交集长度。"""
    return max(0, min(a_end, b_end) - max(a_start, b_start))


@dataclass
class CellExposure:
    room: str
    part: int
    visit_minutes: int = 0
    viewers: set[str] = field(default_factory=set)
    category_minutes: dict[str, int] = field(
        default_factory=lambda: {c: 0 for c in CATEGORIES})
    category_viewers: dict[str, set] = field(
        default_factory=lambda: {c: set() for c in CATEGORIES})

    @property
    def harm_minutes(self) -> int:
        return sum(self.category_minutes[c] for c in HARM_CATEGORIES)

    @property
    def harm_viewers(self) -> set[str]:
        return self.category_viewers[EXPLICIT_HARM] | self.category_viewers[SPILLOVER]

    def harm_minutes_per_visit_minute(self) -> float | None:
        if not self.visit_minutes:
            return None
        return self.harm_minutes / self.visit_minutes


@dataclass
class ViewerPartExposure:
    viewer: str
    part: int
    category_minutes: dict[str, int] = field(
        default_factory=lambda: {c: 0 for c in CATEGORIES})
    # 有害暴露分钟按发生房间拆分（迁移去向的依据）
    harm_minutes_by_room: dict[str, int] = field(default_factory=dict)
    # 链根 -> 在哪些房看到（含未证实路径的疑似来源，单独记录）
    lineage_rooms: dict[str, set] = field(default_factory=dict)
    suspected_rooms: dict[str, set] = field(default_factory=dict)

    @property
    def harm_minutes(self) -> int:
        return sum(self.category_minutes[c] for c in HARM_CATEGORIES)

    @property
    def cross_room_repeats(self) -> dict[str, set]:
        """同一条有害链在多个房间看到：链根 -> 房间集合。"""
        return {r: rooms for r, rooms in self.lineage_rooms.items() if len(rooms) > 1}


@dataclass
class ExposureResult:
    as_of: int
    cells: dict[tuple[str, int], CellExposure]
    viewer_parts: dict[tuple[str, int], ViewerPartExposure]
    post_categories: dict[str, object]  # post_id -> PostCategory

    def cell(self, room: str, part: int) -> CellExposure | None:
        return self.cells.get((room, part))

    def viewers_exposed_to_harm(self, part: int) -> set[str]:
        return {v for (v, p), e in self.viewer_parts.items()
                if p == part and e.harm_minutes > 0}


def compute_exposure(model: Model, state: ReviewState, lineage: Lineage) -> ExposureResult:
    cells: dict[tuple[str, int], CellExposure] = {}
    viewer_parts: dict[tuple[str, int], ViewerPartExposure] = {}
    post_categories = {}

    for (room, part), posts in sorted(model.posts_by_cell.items()):
        visits = model.visits_by_cell.get((room, part), [])
        cell = CellExposure(room, part)
        cats = {p['id']: categorize(p, state, lineage) for p in posts}
        post_categories.update(cats)
        for v in visits:
            duration = v['end'] - v['start']
            cell.visit_minutes += duration
            cell.viewers.add(v['viewer'])
            vpe = viewer_parts.setdefault(
                (v['viewer'], part), ViewerPartExposure(v['viewer'], part))
            for p in posts:
                minutes = overlap_minutes(v['start'], v['end'], p['start'], p['end'])
                if minutes <= 0:
                    continue
                cat = cats[p['id']]
                cell.category_minutes[cat.category] += minutes
                cell.category_viewers[cat.category].add(v['viewer'])
                vpe.category_minutes[cat.category] += minutes
                if cat.category in HARM_CATEGORIES:
                    vpe.harm_minutes_by_room[room] = (
                        vpe.harm_minutes_by_room.get(room, 0) + minutes)
                for src in cat.harmful_sources:
                    root = lineage.root(src)
                    vpe.lineage_rooms.setdefault(root, set()).add(room)
                for src in cat.suspected_sources:
                    vpe.suspected_rooms.setdefault(src, set()).add(room)
        cells[(room, part)] = cell
    return ExposureResult(state.as_of, cells, viewer_parts, post_categories)


def language_context_exposure(model: Model, exposure: ExposureResult
                              ) -> dict[str, dict[int, dict[str, int]]]:
    """按语言环境段的暴露分钟：context -> part -> category -> minutes。

    跨语种观众在多个环境中都有暴露，各环境段不可相加。
    """
    contexts = {'es': set(), 'pt': set(), 'mixed': set()}
    for rid, room in model.rooms.items():
        langs = set(room['language'].split('-'))
        if langs == {'es'}:
            contexts['es'].add(rid)
        elif langs == {'pt'}:
            contexts['pt'].add(rid)
        else:
            contexts['mixed'].add(rid)
    out: dict[str, dict[int, dict[str, int]]] = {
        name: {} for name in contexts}
    for name, rooms in contexts.items():
        for (room, part), cell in exposure.cells.items():
            if room not in rooms:
                continue
            bucket = out[name].setdefault(part, {c: 0 for c in CATEGORIES})
            for cat, minutes in cell.category_minutes.items():
                bucket[cat] += minutes
    return out


def audience_migration(model: Model, exposure: ExposureResult,
                       base_room: str, base_part: int) -> dict:
    """某房基准段观众群在后续各段的有害暴露去向。

    用于回答"主房看起来更清净"是否只是互动迁往他房：
    跟踪基准段在该房有观看片段的观众，其后每段的有害暴露分钟
    按实际发生房间拆分，与基准段总量对比。
    """
    cohort = {v['viewer'] for v in model.visits_by_cell.get((base_room, base_part), [])}
    by_part: dict[int, dict[str, int]] = {}
    for (viewer, part), vpe in exposure.viewer_parts.items():
        if viewer not in cohort or part < base_part:
            continue
        for room, minutes in vpe.harm_minutes_by_room.items():
            bucket = by_part.setdefault(part, {})
            bucket[room] = bucket.get(room, 0) + minutes
            bucket['_total'] = bucket.get('_total', 0) + minutes
    return {'base_room': base_room, 'base_part': base_part,
            'cohort_size': len(cohort),
            'harm_minutes_by_part': {p: rooms for p, rooms in sorted(by_part.items())}}


def content_spread(model: Model, exposure: ExposureResult, lineage: Lineage,
                   part: int) -> dict[str, list[str]]:
    """该段每条有害链根实际触达的房间（经已知引用链）。"""
    spread: dict[str, set] = {}
    for (room, p), cell in exposure.cells.items():
        if p != part:
            continue
        for post in model.posts_by_cell[(room, p)]:
            cat = exposure.post_categories[post['id']]
            for src in cat.harmful_sources:
                spread.setdefault(lineage.root(src), set()).add(room)
    return {root: sorted(rooms) for root, rooms in sorted(spread.items())}
