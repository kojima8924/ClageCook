import 'dart:async';
import 'dart:convert';

import 'package:clage_cook/models.dart';
import 'package:clage_cook/screens/home_screen.dart';
import 'package:clage_cook/services/api_client.dart';
import 'package:clage_cook/services/direct_byok_client.dart';
import 'package:clage_cook/services/direct_settings_store.dart';
import 'package:clage_cook/services/local_conversation_store.dart';
import 'package:clage_cook/services/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  testWidgets('送信preflight中は接続設定を開けない', (tester) async {
    final planStarted = Completer<void>();
    final releasePlan = Completer<void>();
    addTearDown(() {
      if (!releasePlan.isCompleted) releasePlan.complete();
    });
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://a.example.test'),
      client: MockClient((request) async {
        if (request.url.path == '/api/plan') {
          if (!planStarted.isCompleted) planStarted.complete();
          await releasePlan.future;
        }
        return _successResponse(request);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          clientFactory: (_) => client,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(_messageField(), '接続先を固定して実行');

    await tester.tap(find.byIcon(Icons.send));
    await tester.pump();
    expect(planStarted.isCompleted, isTrue);

    final settingsButton = tester.widget<IconButton>(
      find.widgetWithIcon(IconButton, Icons.settings_outlined),
    );
    expect(settingsButton.onPressed, isNull);

    releasePlan.complete();
    await tester.pumpAndSettle();
  });

  testWidgets('新しい保存先へのbootstrap失敗後は旧backendへ送信しない', (tester) async {
    final repository = _MemorySettings(
      const ConnectionSettings(baseUrl: 'http://a.example.test'),
    );
    var requestsToA = 0;
    var requestsToB = 0;
    ApiClient factory(ConnectionSettings settings) {
      final isA = settings.baseUrl.contains('a.example.test');
      return ApiClient(
        settings,
        client: MockClient((request) async {
          if (isA) {
            requestsToA++;
            return _successResponse(request);
          }
          requestsToB++;
          return http.Response('unavailable', 503);
        }),
      );
    }

    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(repository: repository, clientFactory: factory),
      ),
    );
    await tester.pumpAndSettle();
    final requestsAfterAConnect = requestsToA;

    await tester.tap(find.byIcon(Icons.settings_outlined));
    await tester.pumpAndSettle();
    final urlField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.labelText == 'サーバーURL',
    );
    await tester.enterText(urlField, 'http://b.example.test');
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();

    expect(requestsToB, greaterThan(0));
    expect(find.textContaining('接続を無効化しました'), findsOneWidget);
    await tester.enterText(_messageField(), '旧接続へ送らない');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pump();

    expect(requestsToA, requestsAfterAConnect);
    expect(find.textContaining('先に設定画面でバックエンドへ接続'), findsOneWidget);
  });

  testWidgets('配布版bootstrapは保存済み開発用modeをDirectへ強制する', (tester) async {
    var serverFactoryCalls = 0;
    var directFactoryCalls = 0;
    final localRepository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: _MemoryDirectSettings(
            const DirectSettings(
              executionMode: ExecutionMode.referenceServer,
              claudeApiKey: 'configured-key',
            ),
          ),
          localConversationRepository: localRepository,
          allowReferenceServer: false,
          clientFactory: (settings) {
            serverFactoryCalls++;
            return ApiClient(settings);
          },
          directClientFactory: (settings, conversations) {
            directFactoryCalls++;
            return DirectByokClient(
              settings: settings,
              conversations: conversations,
            );
          },
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(serverFactoryCalls, 0);
    expect(directFactoryCalls, 1);
    expect(find.text('DIRECT · LOCAL'), findsOneWidget);
  });
}

Finder _messageField() => find.byWidgetPredicate(
  (widget) => widget is TextField && widget.decoration?.hintText == '質問を入力…',
);

class _MemorySettings implements SettingsRepository {
  _MemorySettings([
    this.value = const ConnectionSettings(baseUrl: 'http://a.example.test'),
  ]);

  ConnectionSettings value;

  @override
  Future<ConnectionSettings> load() async => value;

  @override
  Future<void> save(ConnectionSettings settings) async => value = settings;
}

class _MemoryDirectSettings implements DirectSettingsRepository {
  _MemoryDirectSettings(this.value);

  DirectSettings value;

  @override
  Future<void> clearAllKeys() async {}

  @override
  Future<DirectSettings> load() async => value;

  @override
  Future<void> save(DirectSettings settings) async => value = settings;

  @override
  Future<void> setShowLiveApiConfirmation(bool enabled) async {
    value = value.copyWith(showLiveApiConfirmation: enabled);
  }
}

http.Response _successResponse(http.Request request) {
  switch (request.url.path) {
    case '/api/health':
      return _jsonResponse({
        'ok': true,
        'mode': 'mock',
        'synthesizer': 'synthesizer',
      });
    case '/api/settings':
      return _jsonResponse({
        'mode': 'mock',
        'live_api_enabled': false,
        'providers': [
          {
            'name': 'chatgpt',
            'label': 'ChatGPT',
            'configured': false,
            'mode': 'mock',
            'models': {'low': 'mock', 'balanced': 'mock', 'high': 'mock'},
          },
        ],
        'active_workers': ['chatgpt'],
        'synthesizer': 'synthesizer',
        'auth_required': false,
      });
    case '/api/conversations':
      return _jsonResponse(<Object>[]);
    case '/api/conversations/conversation-id':
      return _jsonResponse({
        'id': 'conversation-id',
        'title': '完了',
        'turns': <Object>[],
      });
    case '/api/policy/scan':
      return _jsonResponse({
        'action': 'allow',
        'findings': <Object>[],
        'redacted_text': '接続先を固定して実行',
        'disclaimer': '',
      });
    case '/api/plan':
      return _jsonResponse({
        'allowed': true,
        'block_reasons': <Object>[],
        'billable': false,
        'mode': 'mock',
        'providers': [
          {
            'name': 'chatgpt',
            'label': 'ChatGPT',
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
        'calls': {'answers': 1, 'debate': 0, 'synthesis': 0, 'total': 1},
        'max_output_tokens': {'total': 2400, 'live_total': 0},
        'policy': {
          'action': 'allow',
          'findings': <Object>[],
          'redacted_text': '接続先を固定して実行',
          'disclaimer': '',
        },
        'warnings': <Object>[],
      });
    case '/api/chat':
      return http.Response(
        'id: 1\nevent: done\ndata: {"failed":true}\n\n',
        200,
        headers: {
          'content-type': 'text/event-stream',
          'x-conversation-id': 'conversation-id',
          'x-request-id': 'request-id',
        },
      );
    default:
      return http.Response('not found', 404);
  }
}

http.Response _jsonResponse(Object data) => http.Response.bytes(
  utf8.encode(jsonEncode(data)),
  200,
  headers: {'content-type': 'application/json; charset=utf-8'},
);
