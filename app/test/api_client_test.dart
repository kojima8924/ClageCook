import 'dart:async';
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
        expect(request.url.path, '/api/conversations/search');
        expect(request.url.hasQuery, isFalse);
        expect(jsonDecode(request.body), {'q': '猫 と 犬', 'limit': 50});
        expect(request.headers['authorization'], 'Bearer secret');
        return _jsonResponse({
          'query': '猫 と 犬',
          'items': [
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
        expect(request.url.path, '/api/conversations/conversation-id/export');
        expect(request.url.queryParameters['format'], 'zip');
        return http.Response.bytes([0x50, 0x4b, 0x03, 0x04], 200);
      }),
    );

    final exported = await client.exportConversationArchive('conversation-id');

    expect(exported, [0x50, 0x4b, 0x03, 0x04]);
    client.close();
  });

  test('会話一覧はitems封筒を読み、読めなかった分をdefectとして残す', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        expect(request.url.path, '/api/conversations');
        return _jsonResponse({
          'items': [
            {
              'id': 'conversation-id',
              'title': '読めた会話',
              'updated_at': '2026-07-18T00:00:00Z',
              'turn_count': 1,
              'preview': '本文',
            },
          ],
          'corrupt_count': 1,
          'corrupt': [
            {'id': 'broken-id', 'file': 'broken-id.json', 'reason': 'invalid_json'},
          ],
        });
      }),
    );

    final summaries = await client.conversations();

    // 破損1件で全損に見せない。健全な会話は返す。
    expect(summaries.single.title, '読めた会話');
    expect(client.storageDefects.single.conversationId, 'broken-id');
    expect(client.storageDefects.single.reason, 'invalid_json');
    // 隔離・index再構築は端末内storage専用の操作なので出さない。
    expect(client.supportsLocalStorageRepair, isFalse);
    client.close();
  });

  test('HTTPエラーは構造化detailのmessageだけを表示する', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient(
        (_) async => _jsonResponse({
          'detail': {'code': 'conversation_not_found', 'message': '会話が見つかりません'},
        }, statusCode: 404),
      ),
    );

    await expectLater(
      client.conversation('missing-id'),
      throwsA(
        isA<ApiException>()
            .having((error) => error.message, 'message', '会話が見つかりません')
            .having((error) => error.statusCode, 'statusCode', 404),
      ),
    );
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
          'reasoning_mode': 'auto',
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

  test('chatはtext/event-stream以外の成功応答を即座に拒否する', () async {
    final body = StreamController<List<int>>();
    addTearDown(body.close);
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: _StreamingClient(
        (_) async => http.StreamedResponse(
          body.stream,
          200,
          headers: {'content-type': 'application/json'},
        ),
      ),
    );

    await expectLater(
      client.startChat(message: '質問'),
      throwsA(
        isA<ApiException>().having(
          (error) => error.message,
          'message',
          contains('text/event-stream'),
        ),
      ),
    );
    client.close();
  });

  test('chatの無応答HTTPエラー本文は上限時間で中断する', () async {
    final cancelled = Completer<void>();
    final body = StreamController<List<int>>(
      onCancel: () {
        if (!cancelled.isCompleted) cancelled.complete();
      },
    );
    addTearDown(body.close);
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      errorBodyTimeout: const Duration(milliseconds: 35),
      client: _StreamingClient(
        (_) async => http.StreamedResponse(body.stream, 503),
      ),
    );

    await expectLater(
      client
          .startChat(message: '質問')
          .timeout(const Duration(milliseconds: 300)),
      throwsA(
        isA<ApiException>()
            .having((error) => error.statusCode, 'statusCode', 503)
            .having((error) => error.message, 'message', contains('タイムアウト')),
      ),
    );
    expect(cancelled.isCompleted, isTrue);
    client.close();
  });

  test('chatの巨大なHTTPエラー本文はbyte上限で中断する', () async {
    final cancelled = Completer<void>();
    late final StreamController<List<int>> body;
    body = StreamController<List<int>>(
      onListen: () {
        scheduleMicrotask(
          () => body.add(utf8.encode('prefix-${'x' * 100}-unreachable-tail')),
        );
      },
      onCancel: () {
        if (!cancelled.isCompleted) cancelled.complete();
      },
    );
    addTearDown(body.close);
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      errorBodyTimeout: const Duration(milliseconds: 100),
      errorBodyMaxBytes: 24,
      client: _StreamingClient(
        (_) async => http.StreamedResponse(body.stream, 502),
      ),
    );

    await expectLater(
      client.startChat(message: '質問'),
      throwsA(
        isA<ApiException>()
            .having(
              (error) => error.message,
              'message',
              contains('24 bytesで打ち切り'),
            )
            .having(
              (error) => error.message,
              'bounded body',
              isNot(contains('unreachable-tail')),
            ),
      ),
    );
    expect(cancelled.isCompleted, isTrue);
    client.close();
  });

  test('ChatStreamはUIが使うSSE無通信上限を保持する', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      sseIdleTimeout: const Duration(milliseconds: 40),
      client: MockClient(
        (_) async => http.Response(
          '',
          200,
          headers: {'content-type': 'text/event-stream; charset=utf-8'},
        ),
      ),
    );

    final stream = await client.startChat(message: '質問');

    expect(stream.idleTimeout, const Duration(milliseconds: 40));
    client.close();
  });

  test('SSE keepaliveコメントをUI用activity eventとして通知する', () async {
    final body = StreamController<List<int>>();
    addTearDown(body.close);
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      sseIdleTimeout: const Duration(milliseconds: 70),
      client: _StreamingClient(
        (_) async => http.StreamedResponse(
          body.stream,
          200,
          headers: {'content-type': 'Text/Event-Stream; charset=UTF-8'},
        ),
      ),
    );

    final stream = await client.startChat(message: '質問');
    final events = stream.events.toList();
    Timer(
      const Duration(milliseconds: 35),
      () => body.add(utf8.encode(': ping 1\n\n')),
    );
    Timer(
      const Duration(milliseconds: 70),
      () => body.add(utf8.encode(': ping 2\n\n')),
    );
    Timer(const Duration(milliseconds: 105), () {
      body
        ..add(utf8.encode('id: 1\nevent: done\ndata: {}\n\n'))
        ..close();
    });

    final decoded = await events;
    expect(
      decoded.where((event) => event.event == SseDecoder.keepAliveEvent),
      hasLength(2),
    );
    expect(
      decoded
          .where((event) => event.event != SseDecoder.keepAliveEvent)
          .single
          .event,
      'done',
    );
    client.close();
  });

  test('SSE応答のHTTP EOFはdecoderを完了させる', () async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient(
        (_) async => http.Response(
          'id: 1\nevent: meta\ndata: {}\n\n',
          200,
          headers: {'content-type': 'text/event-stream'},
        ),
      ),
    );

    final stream = await client.startChat(message: '質問');

    expect((await stream.events.toList()).single.event, 'meta');
    client.close();
  });

  test('HTTPエラー本文はUnicodeコードポイント単位で安全に切り詰める', () async {
    final responseBody = '${'a' * 299}😀${'b' * 20}';
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient(
        (_) async => http.Response.bytes(utf8.encode(responseBody), 500),
      ),
    );

    await expectLater(
      client.health(),
      throwsA(
        isA<ApiException>()
            .having(
              (error) => error.message.endsWith('😀'),
              'last rune',
              isTrue,
            )
            .having(
              (error) => error.message.contains('\uFFFD'),
              'replacement character',
              isFalse,
            ),
      ),
    );
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
          'terminal_outcome': 'cancelled',
          'provider_stop_guaranteed': false,
          'warning': '外部Provider側の処理停止・課金停止は保証されません',
        });
      }),
    );

    final result = await client.cancelRun('request-id');

    expect(result.ok, isTrue);
    expect(result.cancellationRequested, isTrue);
    expect(result.cancelled, isTrue);
    expect(result.terminalOutcome, 'cancelled');
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

http.Response _jsonResponse(Object data, {int statusCode = 200}) =>
    http.Response.bytes(
      utf8.encode(jsonEncode(data)),
      statusCode,
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

class _StreamingClient extends http.BaseClient {
  _StreamingClient(this.handler);

  final Future<http.StreamedResponse> Function(http.BaseRequest request)
  handler;

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) =>
      handler(request);
}
