"""分析包的行为测试：复核状态、引用链、暴露、可比性、试行、人力与决策。"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.coverage import comparable_parts, coverage_table  # noqa: E402
from analysis.decisions import appeal_impact, build_decisions  # noqa: E402
from analysis.exposure import (compute_exposure, overlap_minutes)  # noqa: E402
from analysis.loader import build_model  # noqa: E402
from analysis.staffing import (evaluate_joint_candidate,  # noqa: E402
                               protection_consistency, staffing_table)
from analysis.state import (EXPLICIT_HARM, ORDINARY, SPILLOVER,  # noqa: E402
                            UNDETERMINED, UNVERIFIED_PATH, Lineage,
                            ReviewState, categorize)
from analysis.trials import evaluate_trials  # noqa: E402

RECORDS = ROOT / 'records'


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = build_model(RECORDS)
        cls.lineage = Lineage(cls.model.records['posts'])


class ReviewStateTest(Base):
    def test_as_of_gates_reviews(self):
        early = ReviewState(self.model.records['reviews'], 1)
        self.assertEqual(early.verdict('P1-0-3'), 'harmful')
        self.assertIsNone(early.verdict('P2-0-0'))  # 第2段才记录

    def test_appeal_supersedes(self):
        before = ReviewState(self.model.records['reviews'], 7)
        after = ReviewState(self.model.records['reviews'], 8)
        self.assertEqual(before.verdict('P6-0-0'), 'harmful')
        self.assertEqual(after.verdict('P6-0-0'), 'ordinary')

    def test_unreviewed_is_undetermined(self):
        state = ReviewState(self.model.records['reviews'], 8)
        self.assertIsNone(state.verdict('P1-0-0'))  # 从未审阅


class LineageTest(Base):
    def test_known_chain(self):
        self.assertEqual(self.lineage.known_ancestors('P1-2-0'),
                         ['P1-1-0', 'P1-0-0'])
        self.assertEqual(self.lineage.root('P1-4-0'), 'P1-0-0')

    def test_uncertain_edge_kept_separate(self):
        self.assertNotIn('P1-4-4', self.lineage.known_parent)
        self.assertEqual(self.lineage.uncertain_parent['P1-4-4'], 'P1-3-4')


class CategorizeTest(Base):
    def test_spillover_via_known_quote(self):
        state = ReviewState(self.model.records['reviews'], 8)
        cat = categorize(self.model.posts['P2-1-0'], state, self.lineage)
        self.assertEqual(cat.category, SPILLOVER)
        self.assertIn('P2-0-0', cat.harmful_sources)

    def test_appeal_clears_chain(self):
        state = ReviewState(self.model.records['reviews'], 8)
        cat = categorize(self.model.posts['P6-1-0'], state, self.lineage)
        self.assertEqual(cat.category, ORDINARY)  # 上游已被申诉撤销

    def test_uncertain_path_not_dismissed(self):
        state = ReviewState(self.model.records['reviews'], 8)
        cat = categorize(self.model.posts['P1-4-4'], state, self.lineage)
        self.assertEqual(cat.category, UNVERIFIED_PATH)

    def test_unreviewed_not_treated_safe(self):
        state = ReviewState(self.model.records['reviews'], 8)
        cat = categorize(self.model.posts['P1-0-0'], state, self.lineage)
        self.assertEqual(cat.category, UNDETERMINED)


class ExposureTest(Base):
    def test_half_open_overlap(self):
        self.assertEqual(overlap_minutes(0, 10, 3, 11), 7)
        self.assertEqual(overlap_minutes(0, 3, 3, 11), 0)  # 半开区间端点相接不算
        self.assertEqual(overlap_minutes(5, 8, 3, 11), 3)

    def test_appeal_changes_exposure(self):
        before = compute_exposure(
            self.model, ReviewState(self.model.records['reviews'], 7),
            self.lineage)
        after = compute_exposure(
            self.model, ReviewState(self.model.records['reviews'], 8),
            self.lineage)
        self.assertGreater(before.cell('main', 6).harm_minutes, 0)
        self.assertEqual(after.cell('main', 6).harm_minutes, 0)
        # 他房独立来源不受申诉影响
        self.assertGreater(after.cell('pt2', 6).harm_minutes, 0)

    def test_cross_room_repeat_detected(self):
        exp = compute_exposure(
            self.model, ReviewState(self.model.records['reviews'], 8),
            self.lineage)
        vpe = exp.viewer_parts[('V1', 2)]
        self.assertTrue(vpe.cross_room_repeats)


class CoverageTest(Base):
    def test_missing_ledger_flagged(self):
        state = ReviewState(self.model.records['reviews'], 8)
        table = {(c['room'], c['part']): c for c in coverage_table(self.model, state)}
        self.assertIn('missing_ledger', table[('pt1', 5)]['flags'])

    def test_coverage_change_flagged(self):
        state = ReviewState(self.model.records['reviews'], 8)
        table = {(c['room'], c['part']): c for c in coverage_table(self.model, state)}
        self.assertTrue(any(f.startswith('coverage_changed')
                            for f in table[('er', 5)]['flags']))

    def test_comparable_parts_excludes_missing(self):
        ok, blocked = comparable_parts(self.model, 'main', 'pt1', [5, 6])
        self.assertEqual(ok, [6])
        self.assertIn(5, blocked)


class TrialsTest(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        exp = compute_exposure(
            cls.model, ReviewState(cls.model.records['reviews'], 8),
            cls.lineage)
        cls.reports = {t['trial']: t for t in evaluate_trials(cls.model, exp)}

    def test_t1_main_er_evaluable_on_independent_parts(self):
        t1 = self.reports['T1']
        contrast = next(c for c in t1['contrasts']
                        if c['treated'] == 'main' and c['control'] == 'er')
        self.assertEqual(contrast['status'], 'evaluable')
        self.assertEqual(contrast['comparable_parts'], [5, 6])
        self.assertIsNotNone(contrast['effect']['did'])

    def test_contaminated_parts_safety_only(self):
        t1 = self.reports['T1']
        er = t1['rooms']['er']
        self.assertEqual(sorted(er['safety_only']), [7, 8])
        main = t1['rooms']['main']
        self.assertEqual(sorted(main['safety_only']), [7, 8])

    def test_thin_contrast_deferred(self):
        t1 = self.reports['T1']
        contrast = next(c for c in t1['contrasts'] if c['control'] == 'pt1')
        self.assertEqual(contrast['status'], 'deferred')

    def test_t2_deferred_pending_data(self):
        t2 = self.reports['T2']
        self.assertEqual(t2['pending_parts'], [9])
        self.assertTrue(all(c['status'] == 'deferred' for c in t2['contrasts']))


class StaffingTest(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.table = staffing_table(cls.model, list(range(1, 10)))

    def test_er_gap_while_locked_elsewhere(self):
        for part in (6, 7, 8):
            self.assertTrue(self.table[part]['rooms']['er']['gap'])

    def test_pt2_gap_before_u3_on_duty(self):
        self.assertTrue(self.table[7]['rooms']['pt2']['gap'])
        self.assertFalse(self.table[8]['rooms']['pt2']['gap'])

    def test_locked_staff_stay_put(self):
        self.assertEqual(self.table[7]['availability']['U1']['locked_to'], 'main')
        self.assertEqual(self.table[8]['availability']['U4']['locked_to'], 'pt1')

    def test_joint_candidate_conflicts(self):
        j2 = next(j for j in self.model.joint_actions if j['id'] == 'J2')
        result = evaluate_joint_candidate(self.model, j2, list(range(1, 10)))
        self.assertTrue(any('自相矛盾' in c for c in result['conflicts']))
        self.assertTrue(any('pt2' in c and '对照' in c for c in result['conflicts']))
        self.assertTrue(any('mix' in c and '归因' in c for c in result['conflicts']))

    def test_protection_consistency(self):
        mismatches = {(m['room'], m['part']): m
                      for m in protection_consistency(self.model)}
        # B1 认领 er slow 但 er 实际全程 standard
        self.assertIn(('er', 6), mismatches)
        # main 第5段实际 slow 但无认领窗口
        self.assertIn(('main', 5), mismatches)
        # review 类承诺不与房间保护状态比对
        self.assertNotIn(('pt1', 7), mismatches)


class DecisionsTest(Base):
    def test_appeal_impact(self):
        impacts = appeal_impact(self.model, self.lineage, 8)
        self.assertEqual(len(impacts), 1)
        imp = impacts[0]
        self.assertEqual(imp['appeal'], 'A-R1')
        self.assertEqual(imp['reclassified']['P6-1-0']['to'], ORDINARY)
        # pt2 第6段仍有独立有害来源，共管不得自动结束
        self.assertIn('P6-3-0', imp['remaining_independent_sources']['pt2#6'])

    def test_decision_states(self):
        state = ReviewState(self.model.records['reviews'], 8)
        exp = compute_exposure(self.model, state, self.lineage)
        trials = evaluate_trials(self.model, exp)
        staffing = staffing_table(self.model, list(range(1, 10)))
        decisions = build_decisions(self.model, exp, state, self.lineage,
                                    trials, staffing, 8)
        by_id = {d['id']: d for d in decisions}
        self.assertEqual(by_id['J1']['status'], 'keep_patrol')
        self.assertEqual(by_id['T2:mixvspt2']['status'], 'defer_ruling')
        self.assertEqual(by_id['T1:mainvser']['status'], 'evaluable')
        # 每项决策都带人员覆盖与观察范围
        for d in decisions:
            self.assertIn('observation_scope', d)


if __name__ == '__main__':
    unittest.main()
