import 'dart:convert';

import 'package:clage_cook/models.dart';
import 'package:clage_cook/services/api_client.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('全文検索はクエリをエンコードしてConversationSummaryを復元する', () async {
    final client = ApiClient(
      const ConnectionSettings(
        baseUrl: 'http://localhost:8000/',
        token: 'secret',
      ),
      client: MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/search');
        expect(request.url.hasQuery, isFalse);
        expect(jsonDecode(request.body), {'q': '猫 と 犬', 'limit': 50});
        expect(request.headers['authorization'], 'Bearer secret');
        return _jsonResponse({
          'query': '猫 と 犬',
          'results': [
            {
              'id': 'conversation-id',
              'title': '動物会議',
              'updated_at': '2026-07-18T00:00:00Z',
              'turn_count': 2,
              'preview': '回答本文にも一致します',
            },
          ],
        });
      }),
    );

    final result = await client.searchConversations('  猫 と 犬  ', limit: 50);

    expect(result.query, '猫 と 犬');
    expect(result.results, hasLength(1));
    expect(result.results.single.title, '動物会議');
    expect(result.results.single.turnCount, 2);
    client.close();
  });

  test('空の検索語はHTTPリクエストを送らない', () async {
    final client = ApiClient(
      const ConnectionSettings(),
      client: MockClient((_) async => fail('HTTPリクエストは不要です')),
    );

    final result = await client.searchConversations('   ');

    expect(result.query, isEmpty);
    expect(result.results, isEmpty);
    client.close();
  });

  test('会話エクスポートを読みやすいJSONへ整形する', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(request.url.path, '/api/conversations/conversation-id/export');
        return _jsonResponse({
          'id': 'conversation-id',
          'title': '日本語タイトル',
          'turns': <Object>[],
        });
      }),
    );

    final exported = await client.exportConversationJson('conversation-id');

    expect(exported, contains('\n  "title": "日本語タイトル"'));
    expect(jsonDecode(exported), {
      'id': 'conversation-id',
      'title': '日本語タイトル',
      'turns': <Object>[],
    });
    client.close();
  });

  test('会話ZIPをbyte列のまま取得する', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(
          request.url.path,
          '/api/conversations/conversation-id/export.zip',
        );
        return http.Response.bytes([0x50, 0x4b, 0x03, 0x04], 200);
      }),
    );

    final exported = await client.exportConversationArchive('conversation-id');

    expect(exported, [0x50, 0x4b, 0x03, 0x04]);
    client.close();
  });

  test('Web検索ONだけを明示payloadへ含める', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(jsonDecode(request.body)['web_search'], isTrue);
        return _jsonResponse(_runPlanJson());
      }),
    );

    await client.planChat(message: '最新情報', webSearch: true);
    client.close();
  });

  test('送信前planへchatと同じ実行オプションを渡す', () async {
    final client = ApiClient(
      const ConnectionSettings(
        baseUrl: 'http://localhost:8000',
        token: 'auth-token',
      ),
      client: MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/plan');
        expect(request.headers['authorization'], 'Bearer auth-token');
        expect(jsonDecode(request.body), {
          'message': '設計を比較して',
          'conversation_id': 'conversation-id',
          'tier': 'high',
          'debate': true,
          'providers': ['claude', 'grok'],
          'synthesize': false,
          'blind': true,
        });
        return _jsonResponse(_runPlanJson());
      }),
    );

    final plan = await client.planChat(
      message: '設計を比較して',
      conversationId: 'conversation-id',
      tier: 'high',
      debate: true,
      providers: const ['claude', 'grok'],
      synthesize: false,
      blind: true,
    );

    expect(plan.allowed, isTrue);
    expect(plan.billable, isFalse);
    expect(plan.calls['total'], 2);
    client.close();
  });

  test('chatはlive・個人情報の明示確認とBLINDをサーバーへ渡す', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(request.url.path, '/api/chat');
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        expect(body['blind'], isTrue);
        expect(body['confirm_live_api'], isTrue);
        expect(body['confirm_sensitive_data'], isTrue);
        return http.Response(
          'id: 1\nevent: done\ndata: {}\n\n',
          200,
          headers: {
            'content-type': 'text/event-stream',
            'x-conversation-id': 'conversation-id',
            'x-request-id': 'request-id',
          },
        );
      }),
    );

    final stream = await client.startChat(
      message: '質問',
      providers: const ['chatgpt'],
      blind: true,
      confirmLiveApi: true,
      confirmSensitiveData: true,
    );

    expect(stream.conversationId, 'conversation-id');
    client.close();
  });

  test('policy scanはマスク済み結果だけをクライアントへ復元する', () async {
    const secret = 'sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/policy/scan');
        expect(jsonDecode(request.body), {'text': '確認 $secret'});
        return _jsonResponse({
          'version': 'local-patterns-v1',
          'action': 'block',
          'findings': [
            {
              'rule_id': 'openai_api_key',
              'label': 'OpenAI APIキーらしい文字列',
              'severity': 'block',
              'start': 3,
              'end': 43,
            },
          ],
          'redacted_text': '確認 ⟪REDACTED:openai_api_key⟫',
          'disclaimer': 'ローカル検査です。',
        });
      }),
    );

    final result = await client.scanPolicy('確認 $secret');

    expect(result.blocked, isTrue);
    expect(result.redactedText, isNot(contains(secret)));
    expect(result.findings.single.label, isNot(contains(secret)));
    client.close();
  });

  test('停止要求は外部Provider停止非保証を含む構造化応答として扱う', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(request.method, 'POST');
        expect(request.url.path, '/api/runs/request-id/cancel');
        return _jsonResponse({
          'ok': true,
          'request_id': 'request-id',
          'cancellation_requested': true,
          'cancelled': true,
          'provider_stop_guaranteed': false,
          'warning': '外部Provider側の処理停止・課金停止は保証されません',
        });
      }),
    );

    final result = await client.cancelRun('request-id');

    expect(result.ok, isTrue);
    expect(result.cancellationRequested, isTrue);
    expect(result.cancelled, isTrue);
    expect(result.providerStopGuaranteed, isFalse);
    expect(result.warning, contains('課金停止は保証されません'));
    client.close();
  });

  test('利用状況endpointを型付きsnapshotへ復元する', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(request.method, 'GET');
        expect(request.url.path, '/api/telemetry');
        return _jsonResponse({
          'generated_at': '2026-07-18T00:00:00Z',
          'providers': <Object>[],
          'finance': {'configured': false},
          'limitations': <Object>[],
        });
      }),
    );
    final snapshot = await client.usageTelemetry();
    expect(snapshot.generatedAt, isNotEmpty);
    expect(snapshot.finance.configured, isFalse);
    client.close();
  });

  test('再生成planと実行はtarget、provider、確認flagを送る', () async {
    var calls = 0;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        calls++;
        final body = jsonDecode(request.body) as Map<String, dynamic>;
        if (request.url.path.endsWith('/regeneration-plan')) {
          expect(body, {'target': 'answer', 'provider': 'claude'});
          return _jsonResponse(_runPlanJson());
        }
        expect(request.url.path, endsWith('/regenerate'));
        expect(body['target'], 'answer');
        expect(body['provider'], 'claude');
        expect(body['regeneration_id'], 'fixed-regeneration');
        expect(body['confirm_live_api'], isTrue);
        return _jsonResponse({
          'conversation': {
            'id': 'conversation-id',
            'title': '再生成済み',
            'turns': <Object>[],
          },
        });
      }),
    );
    final plan = await client.regenerationPlan(
      conversationId: 'conversation-id',
      turnRequestId: 'turn-id',
      target: 'answer',
      provider: 'claude',
    );
    final conversation = await client.regenerate(
      conversationId: 'conversation-id',
      turnRequestId: 'turn-id',
      target: 'answer',
      provider: 'claude',
      regenerationId: 'fixed-regeneration',
      confirmLiveApi: true,
    );
    expect(plan.allowed, isTrue);
    expect(conversation.title, '再生成済み');
    expect(calls, 2);
    client.close();
  });
}

http.Response _jsonResponse(Object data) => http.Response.bytes(
  utf8.encode(jsonEncode(data)),
  200,
  headers: {'content-type': 'application/json; charset=utf-8'},
);

Map<String, Object> _runPlanJson() => {
  'allowed': true,
  'block_reasons': <Object>[],
  'billable': false,
  'mode': 'mock',
  'providers': [
    {
      'name': 'claude',
      'label': 'Claude',
      'mode': 'mock',
      'model': 'mock',
      'billable': false,
      'max_calls': 1,
    },
    {
      'name': 'grok',
      'label': 'Grok',
      'mode': 'mock',
      'model': 'mock',
      'billable': false,
      'max_calls': 1,
    },
  ],
  'synthesizer': {
    'name': 'synthesizer',
    'label': 'Local mock synthesizer',
    'mode': 'mock',
    'model': 'mock',
    'enabled': false,
    'billable': false,
    'max_calls': 0,
  },
  'calls': {'answers': 2, 'debate': 0, 'synthesis': 0, 'total': 2},
  'max_output_tokens': {'total': 4800, 'live_total': 0},
  'policy': {
    'action': 'allow',
    'findings': <Object>[],
    'redacted_text': '設計を比較して',
    'disclaimer': '',
  },
  'warnings': <Object>[],
};
