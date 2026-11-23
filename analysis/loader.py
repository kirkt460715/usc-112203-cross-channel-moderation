"""载入并校验演练记录，建立查询索引。

记录目录视为不可信输入：先按 catalog.json 校验行数与 sha256，
再建立索引；任何校验失败都直接抛错，不静默继续。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path

TABLES = (
    'rooms', 'posts', 'visits', 'coverage', 'reviews', 'shifts',
    'policies', 'comparison_windows', 'joint_actions', 'effect_policy',
)


def load_records(records_dir: str | Path) -> dict[str, list[dict]]:
    root = Path(records_dir)
    catalog = json.loads((root / 'catalog.json').read_bytes())
    records: dict[str, list[dict]] = {}
    for name in TABLES:
        raw = (root / f'{name}.jsonl').read_bytes()
        expected = catalog[name]
        digest = sha256(raw).hexdigest()
        if digest != expected['sha256']:
            raise ValueError(f'{name}: sha256 不符 {digest} != {expected["sha256"]}')
        rows = [json.loads(line) for line in raw.splitlines()]
        if len(rows) != expected['rows']:
            raise ValueError(f'{name}: 行数不符 {len(rows)} != {expected["rows"]}')
        ids = [row['id'] for row in rows]
        if len(set(ids)) != len(ids):
            raise ValueError(f'{name}: id 重复')
        records[name] = rows
    return records


@dataclass
class Model:
    """记录索引。时间坐标一律为赛段编号；段内分钟为半开区间。"""

    records: dict[str, list[dict]]
    rooms: dict[str, dict] = field(init=False)
    posts: dict[str, dict] = field(init=False)
    posts_by_cell: dict[tuple[str, int], list[dict]] = field(init=False)
    reviews_by_post: dict[str, list[dict]] = field(init=False)
    coverage_by_cell: dict[tuple[str, int], dict] = field(init=False)
    visits_by_cell: dict[tuple[str, int], list[dict]] = field(init=False)
    parts: list[int] = field(init=False)

    def __post_init__(self) -> None:
        d = self.records
        self.rooms = {r['id']: r for r in d['rooms']}
        self.posts = {p['id']: p for p in d['posts']}
        self.posts_by_cell = {}
        for p in d['posts']:
            self.posts_by_cell.setdefault((p['room'], p['part']), []).append(p)
        self.reviews_by_post = {}
        for r in d['reviews']:
            self.reviews_by_post.setdefault(r['post'], []).append(r)
        self.coverage_by_cell = {(c['room'], c['part']): c for c in d['coverage']}
        self.visits_by_cell = {}
        for v in d['visits']:
            self.visits_by_cell.setdefault((v['room'], v['part']), []).append(v)
        self.parts = sorted({p['part'] for p in d['posts']})

    @property
    def policies(self) -> list[dict]:
        return self.records['policies']

    @property
    def shifts(self) -> list[dict]:
        return self.records['shifts']

    @property
    def comparison_windows(self) -> list[dict]:
        return self.records['comparison_windows']

    @property
    def joint_actions(self) -> list[dict]:
        return self.records['joint_actions']

    @property
    def effect_policy(self) -> dict:
        return self.records['effect_policy'][0]

    def room_languages(self, room: str) -> set[str]:
        return set(self.rooms[room]['language'].split('-'))


def build_model(records_dir: str | Path) -> Model:
    return Model(load_records(records_dir))
