"""复核状态与引用链。

复核状态按 as_of 滚动：只采纳 recorded_part <= as_of 的复核；
申诉通过 supersedes 覆盖旧结论，同段时申诉优先于人工初判。
未审阅的内容没有正式结论，显式记为 undetermined，不当作安全。

引用链分两类：
- known：已确认的传播路径，可沿链向上追溯有害来源；
- uncertain：声称的引用未证实，单独成桶，既不并入已知传播，
  也不当作没有引用。
"""
from __future__ import annotations

from dataclasses import dataclass

# 暴露类别，按优先级从高到低
EXPLICIT_HARM = 'explicit_harm'      # 本帖当前有效结论为 harmful
SPILLOVER = 'spillover'              # 沿已知引用链暴露到有害来源
UNVERIFIED_PATH = 'unverified_path'  # 引用路径未证实，危害存疑
UNDETERMINED = 'undetermined'        # 未审阅，无正式结论
ORDINARY = 'ordinary'                # 有效结论为 ordinary 且无有害上游

CATEGORIES = (EXPLICIT_HARM, SPILLOVER, UNVERIFIED_PATH, UNDETERMINED, ORDINARY)


class ReviewState:
    """as_of 时点下各帖的有效复核结论。"""

    def __init__(self, reviews: list[dict], as_of: int):
        self.as_of = as_of
        self._effective: dict[str, dict] = {}
        for r in reviews:
            if r['recorded_part'] > as_of:
                continue
            key = (r['recorded_part'], 1 if r['source'] == 'appeal' else 0)
            cur = self._effective.get(r['post'])
            if cur is None or key >= cur[0]:
                self._effective[r['post']] = (key, r)
        self._effective = {p: v[1] for p, v in self._effective.items()}

    def verdict(self, post_id: str) -> str | None:
        r = self._effective.get(post_id)
        return r['verdict'] if r else None

    def review(self, post_id: str) -> dict | None:
        return self._effective.get(post_id)

    def reviewed_posts(self) -> set[str]:
        return set(self._effective)


class Lineage:
    """引用图。known 边参与传播追溯；uncertain 边单独保留。"""

    def __init__(self, posts: list[dict]):
        self.known_parent: dict[str, str] = {}
        self.uncertain_parent: dict[str, str] = {}
        for p in posts:
            q = p.get('quoted')
            if not q:
                continue
            if p['link_status'] == 'uncertain':
                self.uncertain_parent[p['id']] = q
            else:
                self.known_parent[p['id']] = q

    def known_ancestors(self, post_id: str) -> list[str]:
        out, cur, seen = [], post_id, set()
        while cur in self.known_parent and cur not in seen:
            seen.add(cur)
            cur = self.known_parent[cur]
            out.append(cur)
        return out

    def root(self, post_id: str) -> str:
        ancestors = self.known_ancestors(post_id)
        return ancestors[-1] if ancestors else post_id


@dataclass
class PostCategory:
    category: str
    harmful_sources: list[str]      # 已知链上的有害来源（含本帖）
    suspected_sources: list[str]    # 未证实路径指向的有害来源
    uncertain_target: str | None    # 未证实引用的目标帖


def categorize(post: dict, state: ReviewState, lineage: Lineage) -> PostCategory:
    pid = post['id']
    verdict = state.verdict(pid)
    harmful_known = [a for a in lineage.known_ancestors(pid)
                     if state.verdict(a) == 'harmful']
    suspected: list[str] = []
    uncertain_target = lineage.uncertain_parent.get(pid)
    if uncertain_target is not None:
        chain = [uncertain_target] + lineage.known_ancestors(uncertain_target)
        suspected = [a for a in chain if state.verdict(a) == 'harmful']

    if verdict == 'harmful':
        category = EXPLICIT_HARM
    elif harmful_known:
        category = SPILLOVER
    elif uncertain_target is not None:
        category = UNVERIFIED_PATH
    elif verdict is None:
        category = UNDETERMINED
    else:
        category = ORDINARY

    sources = ([pid] if verdict == 'harmful' else []) + harmful_known
    return PostCategory(category, sources, suspected, uncertain_target)
