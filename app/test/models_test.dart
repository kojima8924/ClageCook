import 'package:clage_cook/models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('保存済み会話の回答と統合を復元する', () {
    final record = ConversationRecord.fromJson({
      'id': 'conversation-id',
      'title': 'テスト',
      'memory': {
        'revision': 2,
        'text': '設計メモ',
        'updated_at': '2026-07-18T00:00:00Z',
      },
      'turns': [
        {
          'request_id': 'request-id',
          'message': '質問',
          'clean_message': '質問',
          'options': {'tier': 'high'},
          'answers': {
            'claude': {
              'source': 'claude',
              'ok': true,
              'text': '回答',
              'elapsed_sec': 1.25,
              'usage': {'input_tokens': 10},
              'completion_status': 'incomplete',
              'partial': true,
              'incomplete_reason': 'max_output_tokens',
              'usage_may_be_incomplete': true,
              'web_search_requested': true,
              'citations': [
                {'url': 'https://example.com', 'title': 'Example'},
              ],
              'request_audit': {
                'http_attempts': 2,
                'retry_count': 1,
                'outcome': 'response_received',
                'usage_may_be_incomplete': true,
              },
            },
          },
          'synthesis': {'ok': true, 'text': '結論', 'source': 'claude'},
        },
      ],
    });

    expect(record.turns, hasLength(1));
    expect(record.turns.single.answers['claude']?.text, '回答');
    expect(record.turns.single.answers['claude']?.usage['input_tokens'], 10);
    expect(record.turns.single.answers['claude']?.partial, isTrue);
    expect(
      record.turns.single.answers['claude']?.incompleteReason,
      'max_output_tokens',
    );
    expect(
      record.turns.single.answers['claude']?.requestAudit['http_attempts'],
      2,
    );
    expect(record.turns.single.answers['claude']?.usageMayBeIncomplete, isTrue);
    expect(record.turns.single.answers['claude']?.webSearchRequested, isTrue);
    expect(
      record.turns.single.answers['claude']?.citations.single.url,
      'https://example.com',
    );
    expect(record.turns.single.synthesis.text, '結論');
    expect(record.memory.revision, 2);
    expect(record.memory.text, '設計メモ');
  });

  test('LiveTurnは呼び出し側のProviderリストを複製する', () {
    final providers = <String>['claude'];
    final turn = LiveTurn(
      requestId: 'request-id',
      message: '質問',
      providers: providers,
      tier: 'balanced',
      debate: false,
      synthesize: true,
    );
    providers.add('grok');

    expect(turn.providers, ['claude']);
    turn.providers.add('gemini');
    expect(turn.providers, ['claude', 'gemini']);
  });

  test('全文検索レスポンスを復元する', () {
    final result = ConversationSearchResult.fromJson({
      'query': '設計',
      'results': [
        {
          'id': 'conversation-id',
          'title': '設計会議',
          'updated_at': '2026-07-18T00:00:00Z',
          'turn_count': 3,
          'preview': '一致した統合回答',
        },
      ],
    });

    expect(result.query, '設計');
    expect(result.results.single.preview, '一致した統合回答');
  });

  test('サーバー設定はlive APIゲートを安全側の既定値で復元する', () {
    final safe = ServerSettings.fromJson({
      'mode': 'mock',
      'providers': <Object>[],
      'active_workers': <Object>[],
      'synthesizer': 'synthesizer',
      'auth_required': false,
    });
    final live = ServerSettings.fromJson({
      'mode': 'mixed',
      'live_api_enabled': true,
      'providers': <Object>[],
      'active_workers': <Object>[],
      'synthesizer': 'chatgpt',
      'auth_required': true,
    });

    expect(safe.liveApiEnabled, isFalse);
    expect(live.liveApiEnabled, isTrue);
  });

  test('policy scanはマスク済み文面と秘密値を含まない検出理由を復元する', () {
    final result = PolicyScanResult.fromJson({
      'version': 'local-patterns-v1',
      'action': 'block',
      'findings': [
        {
          'rule_id': 'openai_api_key',
          'label': 'OpenAI APIキーらしい文字列',
          'severity': 'block',
          'start': 4,
          'end': 40,
        },
      ],
      'redacted_text': 'キー ⟪REDACTED:openai_api_key⟫',
      'disclaimer': 'ローカル検査です。',
    });

    expect(result.blocked, isTrue);
    expect(result.findings.single.label, 'OpenAI APIキーらしい文字列');
    expect(result.redactedText, contains('REDACTED'));
  });

  test('実行planから課金対象と最大call/tokenを復元する', () {
    final plan = RunPlan.fromJson({
      'allowed': true,
      'block_reasons': <Object>[],
      'billable': true,
      'mode': 'mixed',
      'providers': [
        {
          'name': 'chatgpt',
          'label': 'ChatGPT',
          'mode': 'live',
          'model': 'gpt-test',
          'billable': true,
          'max_calls': 2,
        },
      ],
      'synthesizer': {
        'name': 'chatgpt',
        'label': 'ChatGPT',
        'mode': 'live',
        'model': 'gpt-test',
        'enabled': true,
        'billable': true,
        'max_calls': 1,
      },
      'calls': {'answers': 1, 'debate': 1, 'synthesis': 1, 'total': 3},
      'max_output_tokens': {'total': 7200, 'live_total': 7200},
      'retry_envelope': {
        'configured_retries_per_live_call': 2,
        'live_initial_calls': 3,
        'additional_http_attempts': 6,
        'total_provider_executions': 9,
        'max_output_tokens': 21600,
        'disclaimer': '安全側上限です。',
      },
      'input_envelope': {
        'unit': 'utf8_bytes',
        'history': 512,
        'answer_per_call': 1024,
        'answers_total': 1024,
        'debate_total': 2048,
        'synthesis': 1024,
        'total': 4096,
        'live_initial_total': 4096,
        'live_with_retries': 12288,
        'total_with_retries': 12288,
        'token_count_estimated': false,
        'disclaimer': 'token数ではありません。',
      },
      'policy': {
        'action': 'allow',
        'findings': <Object>[],
        'redacted_text': '質問',
        'disclaimer': '',
      },
      'warnings': [
        {'code': 'billable_live_api', 'message': '課金の可能性があります。'},
      ],
      'cost_estimate': {
        'available': true,
        'complete': true,
        'total_micros': 125000,
        'total_usd': '0.125000',
        'price_version': 'custom-v1',
      },
      'budget': {
        'configured': true,
        'allowed': true,
        'run_estimate_usd': '0.125000',
        'limits': {'per_run_usd': '1.000000', 'daily_usd': '5.000000'},
        'today': {
          'day': '2026-07-18',
          'active_reservation_top_up_usd': '0.075000',
          'remaining_usd': '4.000000',
          'unpriced_requests': 0,
        },
        'price_table': {'loaded': true, 'version': 'custom-v1'},
        'active_reservations': <Object>[],
      },
    });

    expect(plan.billableParticipants, hasLength(2));
    expect(plan.maxLiveCalls, 3);
    expect(plan.calls['total'], 3);
    expect(plan.maxOutputTokens['live_total'], 7200);
    expect(plan.retryEnvelope.hasRetries, isTrue);
    expect(plan.retryEnvelope.totalProviderExecutions, 9);
    expect(plan.retryEnvelope.maxOutputTokens, 21600);
    expect(plan.inputEnvelope.history, 512);
    expect(plan.inputEnvelope.liveWithRetries, 12288);
    expect(plan.inputEnvelope.tokenCountEstimated, isFalse);
    expect(plan.costEstimate.totalUsd, '0.125000');
    expect(plan.budget.configured, isTrue);
    expect(plan.budget.todayActiveReservationTopUpUsd, '0.075000');
    expect(plan.budget.todayRemainingUsd, '4.000000');
  });

  test('利用状況、quota、予算を欠損値と区別して復元する', () {
    final snapshot = UsageTelemetrySnapshot.fromJson({
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
              'usage': {'total_tokens': 12},
            },
            'all_time': {
              'observed_requests': 2,
              'usage_unknown_requests': 1,
              'usage': {'total_tokens': 20},
            },
          },
          'latest_quota_snapshot': {
            'observed_at': '2026-07-18T00:00:00Z',
            'dimensions': {
              'requests': {'limit': null, 'remaining': 7, 'reset': '1m'},
            },
          },
          'capabilities': {
            'portal_url': 'https://example.test',
            'rate_limit_response_headers': true,
          },
        },
      ],
      'finance': {
        'configured': false,
        'price_table': {'loaded': false},
        'today': {'unpriced_requests': 1},
      },
      'admin': {
        'enabled': true,
        'generated_at': '2026-07-18T00:01:00Z',
        'window': {'lookback_days': 7},
        'cache': {'hit': true},
        'providers': [
          {
            'name': 'chatgpt',
            'label': 'ChatGPT',
            'supported': true,
            'configured': true,
            'status': 'partial',
            'usage': {
              'status': 'ok',
              'usage': {'input_tokens': 100, 'requests': 2},
            },
            'cost': {'status': 'error', 'error_code': 'forbidden'},
          },
          {
            'name': 'grok',
            'label': 'Grok',
            'supported': true,
            'configured': true,
            'status': 'ok',
            'usage': {'status': 'ok', 'amount_usd': '1.25'},
            'credit_balance': {
              'status': 'ok',
              'provider_reported_usd': '-10',
              'sign_convention': 'provider_reported',
            },
          },
        ],
      },
      'limitations': ['ローカル集計'],
    });

    expect(snapshot.providers.single.today.usage['total_tokens'], 12);
    expect(
      snapshot.providers.single.latestQuota.dimensions['requests']?.limit,
      isNull,
    );
    expect(
      snapshot.providers.single.latestQuota.dimensions['requests']?.remaining,
      7,
    );
    expect(snapshot.finance.unpricedRequests, 1);
    expect(snapshot.admin.enabled, isTrue);
    expect(snapshot.admin.lookbackDays, 7);
    expect(snapshot.admin.cacheHit, isTrue);
    expect(snapshot.admin.providers.first.usage['input_tokens'], 100);
    expect(snapshot.admin.providers.first.errorCodes, ['forbidden']);
    expect(snapshot.admin.providers.last.costUsd, '1.25');
    expect(snapshot.admin.providers.last.creditBalanceUsd, '-10');
  });

  test('再生成attemptとactive pointerを復元する', () {
    final turn = TurnRecord.fromJson({
      'request_id': 'turn-id',
      'message': '質問',
      'options': <String, Object>{},
      'answers': <String, Object>{},
      'synthesis': {'ok': true, 'text': '古い統合'},
      'attempts': [
        {
          'attempt_id': 'attempt-1',
          'target': 'answer',
          'provider': 'claude',
          'status': 'completed',
          'original': false,
        },
      ],
      'active_attempts': {'answer:claude': 'attempt-1'},
      'synthesis_stale': true,
    });
    expect(turn.attempts.single.attemptId, 'attempt-1');
    expect(turn.activeAttempts['answer:claude'], 'attempt-1');
    expect(turn.synthesisStale, isTrue);
  });

  test('保存ターンと実行中ターンがローカルinsightsを保持する', () {
    final record = TurnRecord.fromJson({
      'request_id': 'request-id',
      'message': '質問',
      'clean_message': '質問',
      'answers': <String, Object>{},
      'synthesis': {'ok': false},
      'options': <String, Object>{},
      'insights': {'agreement_score': 0.75},
      'status': 'failed',
      'failed': true,
      'usage_may_be_incomplete': true,
    });
    final live = LiveTurn(
      requestId: 'request-id',
      message: '質問',
      providers: const ['claude'],
      tier: 'balanced',
      debate: false,
      synthesize: true,
    )..insights = {'agreement_score': 0.5};

    expect(record.insights?['agreement_score'], 0.75);
    expect(record.status, 'failed');
    expect(record.failed, isTrue);
    expect(record.usageMayBeIncomplete, isTrue);
    expect(live.insights?['agreement_score'], 0.5);
  });
}
