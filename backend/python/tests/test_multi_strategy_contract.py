import unittest
from unittest.mock import patch

from backend.python.services.research_service import (
    _compact_compare_summary,
    _compact_preset_compare_request_payload,
)
from backend.python.strategy_backend import (
    ApplyStrategyRequest,
    PresetCompareRequest,
    StrategyPayload,
    build_strategy_request_payload,
    build_backtest_params,
    build_indicator_alias_registry,
    build_strategy_params,
    execute_preset_compare_request,
    get_primary_strategy_for_request,
    normalize_strategy_set_entries,
    resolve_strategy_request_entries,
    resolve_strategy_param_aliases,
    summarize_comparison_stats,
)
from backend.python.lib.strategy.expression_identifiers import build_expression_safe_identifier


class MultiStrategyContractTest(unittest.TestCase):
    def build_strategy(self, long_open='False', short_open='False'):
        return StrategyPayload.model_validate({
            'long': {
                'openIf': long_open,
            },
            'short': {
                'openIf': short_open,
            },
        })

    def test_legacy_single_strategy_payload_remains_valid(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'time[0] == 1'},
            },
            'backtest': {},
        })

        self.assertEqual(payload.strategy.long.openIf, 'time[0] == 1')
        self.assertEqual(payload.strategies, [])

    def test_multi_strategy_payload_is_accepted_and_sorted_by_priority(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'strategies': [
                {
                    'id': 'slow',
                    'label': 'Slow',
                    'priority': 20,
                    'strategy': {'long': {'openIf': 'time[0] == 20'}},
                },
                {
                    'id': 'fast',
                    'label': 'Fast',
                    'priority': 5,
                    'strategy': {'long': {'openIf': 'time[0] == 5'}},
                },
            ],
            'backtest': {},
        })

        entries = normalize_strategy_set_entries(payload.strategies, payload.strategy)
        self.assertEqual([entry.id for entry in entries], ['fast', 'slow'])
        self.assertEqual(entries[0].strategy.long.openIf, 'time[0] == 5')
        self.assertEqual(entries[1].strategy.long.openIf, 'time[0] == 20')

    def test_multi_strategy_payload_preserves_optional_market_context_per_entry(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'strategies': [
                {
                    'id': 'eur-fast',
                    'label': 'EUR Fast',
                    'symbol': 'eurusd',
                    'timeframe': 'm1',
                    'strategy': {'long': {'openIf': 'time[0] == 5'}},
                },
                {
                    'id': 'gbp-slow',
                    'label': 'GBP Slow',
                    'symbol': 'gbpusd',
                    'timeframe': 'm5',
                    'strategy': {'short': {'openIf': 'time[0] == 10'}},
                },
            ],
            'backtest': {},
        })

        entries = normalize_strategy_set_entries(payload.strategies, payload.strategy)
        self.assertEqual(entries[0].symbol, 'eurusd')
        self.assertEqual(entries[0].timeframe, 'm1')
        self.assertEqual(entries[1].symbol, 'gbpusd')
        self.assertEqual(entries[1].timeframe, 'm5')

    def test_backtest_payload_accepts_portfolio_mode(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'backtest': {
                'portfolioMode': 'parallel_sleeves',
            },
        })

        backtest_params = build_backtest_params(payload.backtest)
        self.assertEqual(payload.backtest.portfolioMode, 'parallel_sleeves')
        self.assertEqual(backtest_params['portfolio_mode'], 'parallel_sleeves')

    def test_backtest_payload_defaults_to_active_broker_profile(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'backtest': {},
        })

        backtest_params = build_backtest_params(payload.backtest)
        self.assertEqual(payload.backtest.costProfile, 'broker_active')
        self.assertAlmostEqual(payload.backtest.spreadInPips, 1.0)
        self.assertEqual(backtest_params['cost_profile'], 'broker_active')

    def test_backtest_payload_applies_named_cost_profile_defaults(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'backtest': {
                'costProfile': 'forex',
            },
        })

        backtest_params = build_backtest_params(payload.backtest)
        self.assertEqual(payload.backtest.costProfile, 'forex')
        self.assertAlmostEqual(payload.backtest.spreadInPips, 1.2)
        self.assertAlmostEqual(payload.backtest.entrySlippageInPips, 0.2)
        self.assertEqual(backtest_params['cost_profile'], 'forex')

    def test_backtest_payload_keeps_legacy_manual_costs_as_custom_when_profile_is_missing(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'backtest': {
                'spreadInPips': 1.7,
                'entrySlippageInPips': 0.35,
            },
        })

        backtest_params = build_backtest_params(payload.backtest)
        self.assertEqual(payload.backtest.costProfile, 'custom')
        self.assertAlmostEqual(payload.backtest.spreadInPips, 1.7)
        self.assertAlmostEqual(payload.backtest.entrySlippageInPips, 0.35)
        self.assertEqual(backtest_params['cost_profile'], 'custom')

    def test_backtest_payload_keeps_manual_costs_over_named_profile(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'backtest': {
                'costProfile': 'oanda',
                'spreadInPips': 1.7,
                'entrySlippageInPips': 0.35,
            },
        })

        backtest_params = build_backtest_params(payload.backtest)
        self.assertEqual(payload.backtest.costProfile, 'oanda')
        self.assertAlmostEqual(payload.backtest.spreadInPips, 1.7)
        self.assertAlmostEqual(payload.backtest.entrySlippageInPips, 0.35)
        self.assertEqual(backtest_params['cost_profile'], 'oanda')

    def test_backtest_payload_preserves_broker_cost_context(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'backtest': {
                'brokerProfileId': 'clear-main',
                'brokerProfileLabel': 'CLEAR main',
                'brokerCode': 'clear',
                'brokerLabel': 'CLEAR',
                'brokerMarketDomain': 'brazil',
                'brokerCostProfile': 'clear_b3',
                'brokerDefaultAssetType': 'b3_equity',
            },
        })

        backtest_params = build_backtest_params(payload.backtest)
        self.assertEqual(backtest_params['broker_cost_context']['broker_code'], 'clear')
        self.assertEqual(backtest_params['broker_cost_context']['market_domain'], 'brazil')
        self.assertEqual(backtest_params['broker_cost_context']['broker_cost_profile'], 'clear_b3')
        self.assertEqual(backtest_params['broker_cost_context']['broker_default_asset_type'], 'b3_equity')

    def test_primary_strategy_prefers_first_enabled_entry_by_priority(self):
        fallback = self.build_strategy(long_open='fallback')
        payload = ApplyStrategyRequest.model_validate({
            'strategy': fallback.model_dump(),
            'strategies': [
                {
                    'id': 'disabled-top',
                    'priority': 0,
                    'enabled': False,
                    'strategy': {'long': {'openIf': 'disabled'}},
                },
                {
                    'id': 'winner',
                    'priority': 1,
                    'enabled': True,
                    'strategy': {'long': {'openIf': 'winner'}},
                },
            ],
            'backtest': {},
        })

        primary = get_primary_strategy_for_request(payload)
        self.assertEqual(primary.long.openIf, 'winner')

    def test_primary_strategy_falls_back_when_no_strategy_set_entries_exist(self):
        fallback = self.build_strategy(long_open='fallback')
        payload = ApplyStrategyRequest(
            strategy=fallback,
            backtest={},
        )

        primary = get_primary_strategy_for_request(payload)
        self.assertEqual(primary.long.openIf, 'fallback')

    @patch.dict('backend.python.strategy_backend.FEATURE_FLAGS', {'backtest_portfolios_v2': True}, clear=False)
    def test_explicit_portfolios_compile_into_flat_runtime_entries_with_scope_tags(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'portfolioStructureVersion': 2,
            'capitalModel': {
                'initialBalance': 10_000,
                'marginModel': 'forex_notional',
            },
            'backtest': {
                'initialVolume': 0.03,
                'portfolioMode': 'parallel_sleeves',
            },
            'portfolios': [
                {
                    'id': 'p1',
                    'label': 'Portfolio 1',
                    'pipelines': [
                        {
                            'id': 'london',
                            'label': 'London',
                            'portfolioMode': 'shared_pipe',
                            'strategyEntries': [
                                {
                                    'id': 'fixed',
                                    'label': 'Fixed',
                                    'fixedVolume': 0.05,
                                    'volumeMode': 'fixed_volume',
                                    'strategy': {'long': {'openIf': 'time[0] == 5'}},
                                },
                                {
                                    'id': 'compound',
                                    'label': 'Compound',
                                    'baseVolume': 0.02,
                                    'volumeMode': 'base_volume_compounding',
                                    'strategy': {'short': {'openIf': 'time[0] == 10'}},
                                },
                            ],
                        },
                    ],
                },
            ],
        })

        resolved = resolve_strategy_request_entries(payload)
        entries_by_id = {entry.id: entry for entry in resolved['entries']}

        self.assertEqual(resolved['portfolio_structure_version'], 2)
        self.assertEqual(len(resolved['entries']), 2)
        self.assertEqual(entries_by_id['fixed'].portfolioId, 'p1')
        self.assertEqual(entries_by_id['fixed'].pipelineId, 'london')
        self.assertEqual(entries_by_id['fixed'].fixedVolume, 0.05)
        self.assertEqual(entries_by_id['compound'].volumeMode, 'base_volume_compounding')
        self.assertTrue(entries_by_id['compound'].legacyVolumeFallbackApplied)
        self.assertEqual(resolved['portfolios'][0]['pipelines'][0]['portfolioMode'], 'shared_pipe')

    @patch.dict('backend.python.strategy_backend.FEATURE_FLAGS', {'backtest_portfolios_v2': True}, clear=False)
    def test_build_strategy_request_payload_preserves_explicit_portfolios_and_compiled_entries(self):
        payload = ApplyStrategyRequest.model_validate({
            'strategy': {
                'long': {'openIf': 'False'},
            },
            'portfolioStructureVersion': 2,
            'backtest': {
                'initialVolume': 0.04,
            },
            'portfolios': [
                {
                    'id': 'p1',
                    'label': 'Portfolio 1',
                    'pipelines': [
                        {
                            'id': 'ny',
                            'label': 'NY',
                            'sleeveNote': 'keep-me',
                            'strategyEntries': [
                                {
                                    'id': 'maxed',
                                    'label': 'Maxed',
                                    'volumeMode': 'max_affordable',
                                    'strategy': {'long': {'openIf': 'time[0] == 5'}},
                                },
                            ],
                        },
                    ],
                },
            ],
        })

        serialized = build_strategy_request_payload(payload)

        self.assertEqual(serialized['portfolioStructureVersion'], 2)
        self.assertEqual(serialized['portfolios'][0]['id'], 'p1')
        self.assertEqual(serialized['portfolios'][0]['pipelines'][0]['sleeveNote'], 'keep-me')
        self.assertEqual(serialized['strategies'][0]['portfolioId'], 'p1')
        self.assertEqual(serialized['strategies'][0]['pipelineId'], 'ny')
        self.assertEqual(serialized['strategies'][0]['volumeMode'], 'max_affordable')
        self.assertTrue(serialized['strategies'][0]['legacyVolumeFallbackApplied'])

    def test_preset_compare_entry_accepts_auxiliary_strategies(self):
        payload = PresetCompareRequest.model_validate({
            'baseline': {
                'id': 'base',
                'label': 'Baseline',
                'strategy': {'long': {'openIf': 'base'}},
                'strategies': [
                    {
                        'id': 'helper',
                        'label': 'Helper',
                        'priority': 2,
                        'strategy': {'short': {'openIf': 'helper'}},
                    },
                ],
            },
            'presets': [
                {
                    'id': 'candidate',
                    'label': 'Candidate',
                    'strategy': {'long': {'openIf': 'candidate'}},
                    'strategies': [
                        {
                            'id': 'helper-b',
                            'label': 'Helper B',
                            'priority': 1,
                            'enabled': True,
                            'strategy': {'short': {'openIf': 'helper-b'}},
                        },
                    ],
                },
            ],
            'backtest': {},
        })

        self.assertEqual(payload.baseline.strategies[0].id, 'helper')
        self.assertEqual(payload.presets[0].strategies[0].id, 'helper-b')

    def test_compact_preset_compare_payload_preserves_portfolio_shape(self):
        compact = _compact_preset_compare_request_payload({
            'baseline': {
                'id': 'base',
                'label': 'Baseline',
                'strategy': {'long': {'openIf': 'base'}},
                'strategies': [
                    {'id': 'b1', 'label': 'B1', 'priority': 3, 'enabled': True},
                    {'id': 'b2', 'label': 'B2', 'priority': 5, 'enabled': False},
                ],
            },
            'presets': [
                {
                    'id': 'single',
                    'label': 'Single',
                    'strategy': {'long': {'openIf': 'single'}},
                },
                {
                    'id': 'portfolio',
                    'label': 'Portfolio',
                    'strategy': {'long': {'openIf': 'portfolio'}},
                    'strategies': [
                        {'id': 'p1', 'label': 'P1', 'priority': 1, 'enabled': True},
                    ],
                },
            ],
            'backtest': {},
        })

        self.assertEqual(compact['baseline']['strategy_count'], 2)
        self.assertTrue(compact['baseline']['has_auxiliary_strategies'])
        self.assertEqual(compact['portfolio_preset_count'], 1)
        self.assertEqual(compact['presets'][0]['strategy_count'], 1)
        self.assertEqual(compact['presets'][1]['strategy_count'], 2)
        self.assertEqual(compact['presets'][1]['strategies'][0]['id'], 'p1')

    def test_compact_compare_summary_preserves_portfolio_metadata(self):
        compact = _compact_compare_summary({
            'status': 'ok',
            'best_preset_id': 'portfolio',
            'baseline': {
                'id': 'base',
                'label': 'Baseline',
                'summary': {'net_pnl': 10.0},
                'strategy_count': 2,
                'portfolio_event_counts': {'open': 2, 'close': 2},
                'portfolio_strategy_stats': [{'strategy_id': 'b1', 'net_pnl': 7.0}],
                'portfolio_analytics': {'max_concurrent_strategies': 2},
            },
            'comparisons': [
                {
                    'id': 'portfolio',
                    'label': 'Portfolio',
                    'summary': {'net_pnl': 12.0},
                    'strategy_count': 3,
                    'portfolio_event_counts': {'open': 3, 'close': 3, 'skip_conflict': 1},
                    'portfolio_strategy_stats': [{'strategy_id': 'p1', 'net_pnl': 8.0}],
                    'portfolio_analytics': {'pairwise': [{'left_strategy_id': 'p1', 'right_strategy_id': 'p2'}]},
                },
            ],
        })

        self.assertEqual(compact['baseline']['strategy_count'], 2)
        self.assertEqual(compact['baseline']['portfolio_event_counts']['open'], 2)
        self.assertEqual(compact['baseline']['portfolio_analytics']['max_concurrent_strategies'], 2)
        self.assertEqual(compact['comparisons'][0]['strategy_count'], 3)
        self.assertEqual(compact['comparisons'][0]['portfolio_event_counts']['skip_conflict'], 1)
        self.assertEqual(compact['comparisons'][0]['portfolio_strategy_stats'][0]['strategy_id'], 'p1')
        self.assertEqual(compact['comparisons'][0]['portfolio_analytics']['pairwise'][0]['left_strategy_id'], 'p1')

    def test_summarize_comparison_stats_preserves_portfolio_fields(self):
        summary = summarize_comparison_stats({
            'net_pnl': 12.0,
            'strategy_count': 3,
            'portfolio_event_counts': {'open': 3, 'skip_open': 1},
            'portfolio_strategy_stats': [{'strategy_id': 'p1', 'net_pnl': 8.0}],
            'portfolio_analytics': {'simultaneous_position_rate': 0.25},
        })

        self.assertEqual(summary['strategy_count'], 3)
        self.assertEqual(summary['portfolio_event_counts']['open'], 3)
        self.assertEqual(summary['portfolio_strategy_stats'][0]['strategy_id'], 'p1')
        self.assertEqual(summary['portfolio_analytics']['simultaneous_position_rate'], 0.25)

    def test_summarize_comparison_stats_keeps_single_strategy_compatible_defaults(self):
        summary = summarize_comparison_stats({
            'net_pnl': 5.0,
            'win_rate': 0.5,
            'n_trades': 2,
        })

        self.assertEqual(summary['net_pnl'], 5.0)
        self.assertEqual(summary['win_rate'], 0.5)
        self.assertEqual(summary['n_trades'], 2)
        self.assertEqual(summary['portfolio_event_counts'], {})
        self.assertEqual(summary['portfolio_strategy_stats'], [])
        self.assertEqual(summary['portfolio_analytics'], {})

    def test_build_indicator_alias_registry_supports_market_regime_and_multiline_tokens(self):
        alias_to_column, duplicate_aliases = build_indicator_alias_registry([
            {
                'name': 'MarketRegime',
                'params': [9, 21, 14, 14, 20, 2, 20, 14, 10, 3, 'hlc3', 5, 3],
                'alias': 'MarketRegime',
                'columns': [
                    'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_trend_score',
                    'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_regime_code',
                ],
                'column_details': [
                    {
                        'column_name': 'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_trend_score',
                        'line_key': 'trend_score',
                        'line_label': 'Trend score',
                        'line_suffix': 'trend_score',
                    },
                    {
                        'column_name': 'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_regime_code',
                        'line_key': 'regime_code',
                        'line_label': 'Regime code',
                        'line_suffix': 'regime_code',
                    },
                ],
            },
            {
                'name': 'ADX',
                'params': [14],
                'alias': 'adx14',
                'columns': ['ADX_14', 'ADX_14_plus_di', 'ADX_14_minus_di'],
                'column_details': [
                    {'column_name': 'ADX_14', 'line_key': 'value', 'line_label': 'ADX', 'line_suffix': ''},
                    {'column_name': 'ADX_14_plus_di', 'line_key': 'plus_di', 'line_label': '+DI', 'line_suffix': 'plus_di'},
                    {'column_name': 'ADX_14_minus_di', 'line_key': 'minus_di', 'line_label': '-DI', 'line_suffix': 'minus_di'},
                ],
            },
        ])

        self.assertEqual(duplicate_aliases, set())
        self.assertEqual(
            alias_to_column['mreg_regime_code'],
            'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_regime_code',
        )
        self.assertEqual(
            alias_to_column['mreg_trend_score'],
            'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_trend_score',
        )
        self.assertEqual(alias_to_column['adx14_value'], 'ADX_14')
        self.assertEqual(alias_to_column['adx14_plus_di'], 'ADX_14_plus_di')

    def test_resolve_strategy_param_aliases_rewrites_aliases_to_real_columns(self):
        resolved = resolve_strategy_param_aliases(
            {
                'open_long_condition': 'mreg_regime_code[0] == 2 and adx14_value[0] > 18 and bb_upper[0] > close[0]',
            },
            [
                {
                    'name': 'MarketRegime',
                    'params': [9, 21, 14, 14, 20, 2, 20, 14, 10, 3, 'hlc3', 5, 3],
                    'alias': 'MarketRegime',
                    'column_details': [
                        {
                            'column_name': 'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_trend_score',
                            'line_key': 'trend_score',
                            'line_label': 'Trend score',
                            'line_suffix': 'trend_score',
                        },
                        {
                            'column_name': 'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_regime_code',
                            'line_key': 'regime_code',
                            'line_label': 'Regime code',
                            'line_suffix': 'regime_code',
                        },
                    ],
                },
                {
                    'name': 'ADX',
                    'params': [14],
                    'alias': 'adx14',
                    'column_details': [
                        {'column_name': 'ADX_14', 'line_key': 'value', 'line_label': 'ADX', 'line_suffix': ''},
                        {'column_name': 'ADX_14_plus_di', 'line_key': 'plus_di', 'line_label': '+DI', 'line_suffix': 'plus_di'},
                        {'column_name': 'ADX_14_minus_di', 'line_key': 'minus_di', 'line_label': '-DI', 'line_suffix': 'minus_di'},
                    ],
                },
                {
                    'name': 'BollingerBands',
                    'params': ['close', 20, 2],
                    'alias': 'bb',
                    'column_details': [
                        {'column_name': 'BollingerBands_close_20_2_middle', 'line_key': 'middle', 'line_label': 'Middle', 'line_suffix': 'middle'},
                        {'column_name': 'BollingerBands_close_20_2_upper', 'line_key': 'upper', 'line_label': 'Upper', 'line_suffix': 'upper'},
                        {'column_name': 'BollingerBands_close_20_2_lower', 'line_key': 'lower', 'line_label': 'Lower', 'line_suffix': 'lower'},
                    ],
                },
            ],
        )

        self.assertEqual(
            resolved['open_long_condition'],
            'MarketRegime_9_21_14_14_20_2_20_14_10_3_hlc3_5_3_regime_code[0] == 2 and ADX_14[0] > 18 and BollingerBands_close_20_2_upper[0] > close[0]',
        )

    def test_resolve_strategy_param_aliases_ignores_duplicate_aliases_when_not_referenced(self):
        resolved = resolve_strategy_param_aliases(
            {
                'open_long_condition': 'EMA_close_9[0] > EMA_close_21[0]',
            },
            [
                {
                    'name': 'EMA',
                    'params': ['close', 9],
                    'columns': ['EMA_close_9'],
                    'column_details': [
                        {'column_name': 'EMA_close_9', 'line_key': 'value', 'line_label': 'EMA', 'line_suffix': ''},
                    ],
                },
                {
                    'name': 'EMA',
                    'params': ['close', 21],
                    'columns': ['EMA_close_21'],
                    'column_details': [
                        {'column_name': 'EMA_close_21', 'line_key': 'value', 'line_label': 'EMA', 'line_suffix': ''},
                    ],
                },
            ],
        )

        self.assertEqual(resolved['open_long_condition'], 'EMA_close_9[0] > EMA_close_21[0]')

    def test_resolve_strategy_param_aliases_rewrites_decimal_columns_to_safe_identifiers(self):
        resolved = resolve_strategy_param_aliases(
            build_strategy_params(
                StrategyPayload.model_validate({
                    'long': {
                        'openIf': 'elliott_wave_confidence[0] >= 0.45 and elliott_bull_breakout_flag[0] > 0',
                    },
                })
            ),
            [
                {
                    'name': 'ElliottWaveProxyV1',
                    'params': [14, 1.5, 3, 0.25, 0.5, 1],
                    'alias': 'elliott',
                    'columns': [
                        'ElliottWaveProxyV1_14_1.5_3_0.25_0.5_1_wave_confidence',
                        'ElliottWaveProxyV1_14_1.5_3_0.25_0.5_1_bull_breakout_flag',
                    ],
                    'column_details': [
                        {
                            'column_name': 'ElliottWaveProxyV1_14_1.5_3_0.25_0.5_1_wave_confidence',
                            'line_key': 'wave_confidence',
                            'line_label': 'Wave confidence',
                            'line_suffix': 'wave_confidence',
                        },
                        {
                            'column_name': 'ElliottWaveProxyV1_14_1.5_3_0.25_0.5_1_bull_breakout_flag',
                            'line_key': 'bull_breakout_flag',
                            'line_label': 'Bull breakout flag',
                            'line_suffix': 'bull_breakout_flag',
                        },
                    ],
                },
            ],
        )

        self.assertEqual(
            resolved['open_long_condition'],
            (
                f'{build_expression_safe_identifier("ElliottWaveProxyV1_14_1.5_3_0.25_0.5_1_wave_confidence")}[0] >= 0.45 '
                f'and {build_expression_safe_identifier("ElliottWaveProxyV1_14_1.5_3_0.25_0.5_1_bull_breakout_flag")}[0] > 0'
            ),
        )

    def test_resolve_strategy_param_aliases_rejects_referenced_duplicate_alias(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate indicator aliases found: EMA, ema'):
            resolve_strategy_param_aliases(
                {
                    'open_long_condition': 'ema[0] > 0 or EMA[0] > 0',
                },
                [
                    {
                        'name': 'EMA',
                        'params': ['close', 9],
                        'columns': ['EMA_close_9'],
                        'column_details': [
                            {'column_name': 'EMA_close_9', 'line_key': 'value', 'line_label': 'EMA', 'line_suffix': ''},
                        ],
                    },
                    {
                        'name': 'EMA',
                        'params': ['close', 21],
                        'columns': ['EMA_close_21'],
                        'column_details': [
                            {'column_name': 'EMA_close_21', 'line_key': 'value', 'line_label': 'EMA', 'line_suffix': ''},
                        ],
                    },
                ],
            )

    @patch('backend.python.strategy_backend.evaluate_comparison_entry')
    def test_execute_preset_compare_request_returns_portfolio_candidate_metadata(self, mock_evaluate):
        mock_evaluate.side_effect = [
            {
                'status': 'ok',
                'stats': {
                    'net_pnl': 10.0,
                    'expectancy_per_trade': 1.0,
                    'max_drawdown': -4.0,
                    'max_drawdown_pct': -0.4,
                    'n_trades': 4,
                    'strategy_count': 2,
                    'portfolio_event_counts': {'open': 2, 'close': 2},
                    'portfolio_strategy_stats': [{'strategy_id': 'base-helper', 'net_pnl': 3.0}],
                    'portfolio_analytics': {'max_concurrent_strategies': 2},
                },
            },
            {
                'status': 'ok',
                'stats': {
                    'net_pnl': 15.0,
                    'expectancy_per_trade': 1.5,
                    'max_drawdown': -3.0,
                    'max_drawdown_pct': -0.3,
                    'n_trades': 5,
                    'strategy_count': 3,
                    'portfolio_event_counts': {'open': 3, 'close': 3, 'skip_open': 1},
                    'portfolio_strategy_stats': [{'strategy_id': 'cand-helper', 'net_pnl': 5.0}],
                    'portfolio_analytics': {'pairwise': [{'left_strategy_id': 'cand-helper', 'right_strategy_id': 'cand-helper-2'}]},
                },
            },
        ]

        payload = PresetCompareRequest.model_validate({
            'baseline': {
                'id': 'base',
                'label': 'Baseline',
                'strategy': {'long': {'openIf': 'base'}},
                'strategies': [{'id': 'base-helper', 'label': 'Base Helper', 'strategy': {'short': {'openIf': 'helper'}}}],
            },
            'presets': [
                {
                    'id': 'cand',
                    'label': 'Candidate',
                    'strategy': {'long': {'openIf': 'cand'}},
                    'strategies': [
                        {'id': 'cand-helper', 'label': 'Candidate Helper', 'priority': 1, 'strategy': {'short': {'openIf': 'helper'}}},
                        {'id': 'cand-helper-2', 'label': 'Candidate Helper 2', 'priority': 2, 'strategy': {'long': {'openIf': 'helper2'}}},
                    ],
                },
            ],
            'backtest': {},
        })

        result = execute_preset_compare_request(payload)

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['baseline']['summary']['strategy_count'], 2)
        self.assertEqual(result['comparisons'][0]['summary']['strategy_count'], 3)
        self.assertEqual(result['comparisons'][0]['summary']['portfolio_event_counts']['skip_open'], 1)
        self.assertEqual(result['comparisons'][0]['summary']['portfolio_analytics']['pairwise'][0]['left_strategy_id'], 'cand-helper')
        self.assertEqual(result['best_preset_id'], 'cand')

    @patch('backend.python.strategy_backend.evaluate_comparison_entry')
    def test_execute_preset_compare_request_reuses_baseline_summary_override(self, mock_evaluate):
        mock_evaluate.return_value = {
            'status': 'ok',
            'stats': {
                'net_pnl': 15.0,
                'expectancy_per_trade': 1.5,
                'max_drawdown': -3.0,
                'max_drawdown_pct': -0.3,
                'n_trades': 5,
                'strategy_count': 2,
            },
        }

        payload = PresetCompareRequest.model_validate({
            'baseline': {
                'id': 'base',
                'label': 'Baseline',
                'strategy': {'long': {'openIf': 'base'}},
            },
            'presets': [
                {
                    'id': 'cand',
                    'label': 'Candidate',
                    'strategy': {'long': {'openIf': 'cand'}},
                },
            ],
            'backtest': {},
        })

        baseline_summary = {
            'net_pnl': 10.0,
            'expectancy_per_trade': 1.0,
            'max_drawdown': -4.0,
            'max_drawdown_pct': -0.4,
            'n_trades': 4,
            'strategy_count': 1,
        }
        result = execute_preset_compare_request(
            payload,
            baseline_summary_override=baseline_summary,
        )

        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['baseline']['summary']['net_pnl'], 10.0)
        self.assertEqual(result['comparisons'][0]['delta_vs_baseline']['net_pnl'], 5.0)
        self.assertEqual(mock_evaluate.call_count, 1)


if __name__ == '__main__':
    unittest.main()
