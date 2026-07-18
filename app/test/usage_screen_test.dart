import 'dart:convert';

import 'package:clage_cook/models.dart';
import 'package:clage_cook/screens/usage_screen.dart';
import 'package:clage_cook/services/api_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  testWidgets('local usage、予算、quotaを別の値として表示する', (tester) async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient(
        (request) async => http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'generated_at': '2026-07-18T00:00:00Z',
              'conversation_count': 2,
              'turn_count': 3,
              'providers': [
                {
                  'name': 'claude',
                  'label': 'Claude',
                  'configured': true,
                  'mode': 'live',
                  'usage': {
                    'today': {
                      'observed_requests': 1,
                      'usage_unknown_requests': 0,
                      'usage': {'input_tokens': 10, 'output_tokens': 5},
                    },
                    'all_time': {
                      'observed_requests': 2,
                      'usage_unknown_requests': 1,
                      'usage': {'total_tokens': 30},
                    },
                  },
                  'latest_quota_snapshot': {
                    'observed_at': '2026-07-18T00:00:00Z',
                    'dimensions': {
                      'requests': {'limit': 50, 'remaining': 49, 'reset': '1m'},
                    },
                  },
                  'capabilities': {
                    'rate_limit_response_headers': true,
                    'portal_url': 'https://example.test/usage',
                  },
                },
              ],
              'finance': {
                'configured': true,
                'unknown_cost_policy': 'block',
                'limits': {'per_run_usd': '1.000000', 'daily_usd': '5.000000'},
                'today': {
                  'day': '2026-07-18',
                  'actual_estimated_usd': '0.100000',
                  'active_reservations_usd': '0.250000',
                  'active_reservation_top_up_usd': '0.200000',
                  'committed_usd': '0.300000',
                  'remaining_usd': '4.700000',
                  'unpriced_requests': 0,
                },
                'price_table': {'loaded': true, 'version': 'test-v1'},
                'active_reservations': [{}],
                'disclaimer': '請求書ではありません。',
              },
              'admin': {
                'enabled': true,
                'generated_at': '2026-07-18T00:01:00Z',
                'window': {'lookback_days': 7},
                'cache': {'hit': false},
                'providers': [
                  {
                    'name': 'claude',
                    'label': 'Claude管理',
                    'supported': true,
                    'configured': true,
                    'status': 'partial',
                    'window': {
                      'starting_at': '2026-07-11T00:00:00Z',
                      'ending_at': '2026-07-18T12:00:00Z',
                      'requested_starting_at': '2026-07-11T15:00:00Z',
                      'requested_ending_at': '2026-07-18T12:00:00Z',
                      'alignment': 'provider_utc_day_buckets',
                      'bucket_width': '1d',
                      'exact_budget_window': false,
                      'complete_through': '2026-07-18T00:00:00Z',
                    },
                    'usage': {
                      'status': 'ok',
                      'usage': {'input_tokens': 100, 'output_tokens': 25},
                    },
                    'cost': {'status': 'error', 'error_code': 'forbidden'},
                  },
                  {
                    'name': 'grok',
                    'label': 'Grok管理',
                    'supported': true,
                    'configured': true,
                    'status': 'ok',
                    'window': {
                      'starting_at': '2026-07-11T15:00:00Z',
                      'ending_at': '2026-07-18T12:00:00Z',
                      'requested_starting_at': '2026-07-11T15:00:00Z',
                      'requested_ending_at': '2026-07-18T12:00:00Z',
                      'alignment': 'requested_budget_window',
                      'bucket_width': '1d',
                      'exact_budget_window': true,
                    },
                    'usage': {'status': 'ok', 'amount_usd': '1.25'},
                    'credit_balance': {
                      'status': 'ok',
                      'provider_reported_usd': '-10',
                      'sign_convention': 'provider_reported',
                    },
                  },
                ],
              },
              'limitations': ['ローカル集計です。'],
            }),
          ),
          200,
          headers: {'content-type': 'application/json; charset=utf-8'},
        ),
      ),
    );

    await tester.pumpWidget(MaterialApp(home: UsageScreen(client: client)));
    await tester.pumpAndSettle();

    expect(find.text('利用状況と予算'), findsOneWidget);
    expect(find.text('ローカル実績'), findsOneWidget);
    expect(find.text('ローカル予算'), findsOneWidget);
    expect(find.text('組織管理API'), findsOneWidget);
    expect(find.text(r'$1.25'), findsOneWidget);
    expect(find.text(r'$-10'), findsOneWidget);
    expect(find.textContaining('権限不足'), findsOneWidget);
    expect(
      find.textContaining('UTC日次bucketで集計され、ローカル予算期間とは一致しません'),
      findsOneWidget,
    );
    expect(
      find.text('実効期間: 2026/07/11 00:00 UTC → 2026/07/18 12:00 UTC'),
      findsOneWidget,
    );
    expect(
      find.text('完全集計済み境界（complete-through）: 2026/07/18 00:00 UTC'),
      findsOneWidget,
    );
    expect(find.textContaining('集計期間（予算期間と一致）'), findsOneWidget);
    expect(find.text(r'$4.700000'), findsOneWidget);
    expect(find.text('有効予約（総額）'), findsOneWidget);
    expect(find.text('実績未反映の追加拘束'), findsOneWidget);
    expect(find.text(r'$0.250000'), findsOneWidget);
    expect(find.text(r'$0.200000'), findsOneWidget);
    expect(find.textContaining('観測済み実績と重複せず'), findsOneWidget);
    expect(find.textContaining('請求書'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('Claude'),
      300,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('Claude'), findsOneWidget);
    expect(find.textContaining('残り 49 / 上限 50'), findsOneWidget);
    client.close();
  });

  test('Provider別windowは未知fieldを無視して型付き復元する', () {
    final provider = AdminProviderTelemetry.fromJson({
      'name': 'claude',
      'label': 'Claude',
      'status': 'ok',
      'window': {
        'starting_at': '2026-07-11T00:00:00Z',
        'ending_at': '2026-07-18T12:00:00Z',
        'requested_starting_at': '2026-07-11T15:00:00Z',
        'requested_ending_at': '2026-07-18T12:00:00Z',
        'alignment': 'provider_utc_day_buckets',
        'bucket_width': '1d',
        'exact_budget_window': false,
        'complete_through': '2026-07-18T00:00:00Z',
        'future_metadata': {'schema': 2},
      },
      'future_provider_field': true,
    });

    expect(provider.window.startingAt, '2026-07-11T00:00:00Z');
    expect(provider.window.endingAt, '2026-07-18T12:00:00Z');
    expect(provider.window.requestedStartingAt, '2026-07-11T15:00:00Z');
    expect(provider.window.requestedEndingAt, '2026-07-18T12:00:00Z');
    expect(provider.window.alignment, 'provider_utc_day_buckets');
    expect(provider.window.bucketWidth, '1d');
    expect(provider.window.exactBudgetWindow, isFalse);
    expect(provider.window.completeThrough, '2026-07-18T00:00:00Z');
    expect(provider.window.hasData, isTrue);
  });

  test('Provider別windowはMapやListを表示文字列へ変換しない', () {
    final window = AdminProviderWindow.fromJson({
      'starting_at': {'unexpected': 'value'},
      'ending_at': ['2026-07-18T12:00:00Z'],
      'requested_starting_at': 123,
      'requested_ending_at': true,
      'alignment': {'unexpected': 'alignment'},
      'bucket_width': ['1d'],
      'exact_budget_window': 'false',
      'complete_through': {'unexpected': 'boundary'},
    });

    expect(window.startingAt, isNull);
    expect(window.endingAt, isNull);
    expect(window.requestedStartingAt, isNull);
    expect(window.requestedEndingAt, isNull);
    expect(window.alignment, isNull);
    expect(window.bucketWidth, isNull);
    expect(window.exactBudgetWindow, isNull);
    expect(window.completeThrough, isNull);
    expect(window.hasData, isFalse);
  });
}
