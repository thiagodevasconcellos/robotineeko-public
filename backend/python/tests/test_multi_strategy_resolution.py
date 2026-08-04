import unittest

from backend.python.lib.strategy.multi_strategy import (
    StrategyOpenCandidate,
    resolve_open_conflicts,
    sort_strategy_open_candidates,
)


class MultiStrategyConflictResolutionTest(unittest.TestCase):
    def candidate(self, strategy_id, priority, side):
        return StrategyOpenCandidate(
            strategy_id=strategy_id,
            strategy_label=strategy_id,
            priority=priority,
            side=side,
        )

    def test_candidates_are_sorted_by_priority_then_id(self):
        candidates = [
            self.candidate('b', 10, 'long'),
            self.candidate('a', 10, 'long'),
            self.candidate('z', 5, 'short'),
        ]

        sorted_candidates = sort_strategy_open_candidates(candidates)
        self.assertEqual([item.strategy_id for item in sorted_candidates], ['z', 'a', 'b'])

    def test_no_hedge_accepts_first_direction_and_skips_opposite_side(self):
        result = resolve_open_conflicts([
            self.candidate('short-top', 1, 'short'),
            self.candidate('long-next', 2, 'long'),
            self.candidate('short-late', 3, 'short'),
        ])

        self.assertEqual([item.strategy_id for item in result['accepted']], ['short-top', 'short-late'])
        self.assertEqual([entry['candidate'].strategy_id for entry in result['skipped']], ['long-next'])
        self.assertEqual(result['effective_direction'], -1)

    def test_existing_long_portfolio_blocks_new_shorts_when_hedge_is_disabled(self):
        result = resolve_open_conflicts([
            self.candidate('long-add', 1, 'long'),
            self.candidate('short-blocked', 2, 'short'),
        ], current_portfolio_position=1, allow_hedge=False)

        self.assertEqual([item.strategy_id for item in result['accepted']], ['long-add'])
        self.assertEqual([entry['candidate'].strategy_id for entry in result['skipped']], ['short-blocked'])
        self.assertEqual(result['skipped'][0]['reason'], 'conflict_with_portfolio_direction')

    def test_allow_hedge_accepts_both_directions(self):
        result = resolve_open_conflicts([
            self.candidate('long-top', 1, 'long'),
            self.candidate('short-next', 2, 'short'),
        ], allow_hedge=True)

        self.assertEqual([item.strategy_id for item in result['accepted']], ['long-top', 'short-next'])
        self.assertEqual(result['skipped'], [])


if __name__ == '__main__':
    unittest.main()
