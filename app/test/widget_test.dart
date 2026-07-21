import 'dart:async';
import 'dart:convert';

import 'package:clage_cook/main.dart';
import 'package:clage_cook/models.dart';
import 'package:clage_cook/screens/home_screen.dart';
import 'package:clage_cook/screens/settings_screen.dart';
import 'package:clage_cook/services/api_client.dart';
import 'package:clage_cook/services/direct_settings_store.dart';
import 'package:clage_cook/services/local_conversation_store.dart';
import 'package:clage_cook/services/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

class _MemorySettings implements SettingsRepository {
  ConnectionSettings value = const ConnectionSettings();
  int saveCalls = 0;

  @override
  Future<ConnectionSettings> load() async => value;

  @override
  Future<void> save(ConnectionSettings settings) async {
    saveCalls++;
    value = settings;
  }
}

class _MemoryDirectSettings implements DirectSettingsRepository {
  _MemoryDirectSettings(this.value);

  DirectSettings value;
  bool failConfirmationPreferenceWrite = false;

  @override
  Future<void> clearAllKeys() async {
    value = value.copyWith(
      claudeApiKey: '',
      chatGptApiKey: '',
      geminiApiKey: '',
      grokApiKey: '',
    );
  }

  @override
  Future<DirectSettings> load() async => value;

  @override
  Future<void> save(DirectSettings settings) async => value = settings;

  @override
  Future<void> setShowLiveApiConfirmation(bool enabled) async {
    if (failConfirmationPreferenceWrite) {
      throw StateError('simulated preference failure');
    }
    value = value.copyWith(showLiveApiConfirmation: enabled);
  }
}

void main() {
  testWidgets('起動して会議シェルが表示される', (tester) async {
    await tester.pumpWidget(
      ClageCookApp(repository: _MemorySettings(), autoload: false),
    );

    expect(find.text('Clage Cook'), findsOneWidget);
    expect(find.text('4つのAIを、1つの会議へ。'), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
    expect(find.byIcon(Icons.send), findsOneWidget);
  });

  testWidgets('モバイルのcomposerは2段の操作帯と入力欄へ収まる', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ClageCookApp(repository: _MemorySettings(), autoload: false),
    );

    expect(find.byKey(const Key('composer-quality-strip')), findsOneWidget);
    expect(find.byKey(const Key('composer-effort-strip')), findsOneWidget);
    expect(find.byKey(const Key('composer-tier-selector')), findsOneWidget);
    expect(find.byKey(const Key('composer-effort-selector')), findsOneWidget);
    expect(tester.takeException(), isNull);
    expect(
      tester.getSize(find.byKey(const Key('composer'))).height,
      lessThanOrEqualTo(180),
    );
  });

  testWidgets('キーボードから全文検索と新規会話を呼び出せる', (tester) async {
    tester.view.physicalSize = const Size(1200, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ClageCookApp(repository: _MemorySettings(), autoload: false),
    );
    final searchField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == 'すべての回答を全文検索',
    );
    final messageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == '質問を入力…',
    );

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyK);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();

    expect(tester.widget<TextField>(searchField).focusNode?.hasFocus, isTrue);

    await tester.enterText(messageField, '未送信の下書き');
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyN);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();

    expect(tester.widget<TextField>(messageField).controller?.text, isEmpty);
  });

  testWidgets('モバイル幅のドロワーから全文検索を操作できる', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ClageCookApp(repository: _MemorySettings(), autoload: false),
    );

    await tester.tap(find.byIcon(Icons.menu));
    await tester.pumpAndSettle();

    expect(find.text('新しい会話'), findsOneWidget);
    expect(find.textContaining('Ctrl/Cmd+K'), findsOneWidget);
    expect(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == 'すべての回答を全文検索',
      ),
      findsOneWidget,
    );
    final searchField = tester.widget<TextField>(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == 'すべての回答を全文検索',
      ),
    );
    expect(searchField.maxLength, 200);
    expect(searchField.maxLengthEnforcement, MaxLengthEnforcement.enforced);
  });

  testWidgets('設定画面はlive API無効をSAFE MOCKとして明示する', (tester) async {
    final repository = _MemorySettings();
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(
          repository: repository,
          initial: const ConnectionSettings(),
          initialServerSettings: const ServerSettings(
            mode: 'mock',
            providers: [
              ProviderStatus(
                name: 'chatgpt',
                label: 'ChatGPT',
                configured: true,
                mode: 'mock',
                models: {
                  'low': 'gpt-low',
                  'balanced': 'gpt-balanced',
                  'high': 'gpt-high',
                },
              ),
            ],
            activeWorkers: ['chatgpt'],
            synthesizer: 'synthesizer',
            authRequired: false,
            liveApiEnabled: false,
          ),
        ),
      ),
    );

    expect(find.text('SAFE MOCK'), findsOneWidget);
    expect(find.textContaining('APIキーが設定済みでも外部AI APIは呼び出しません'), findsOneWidget);
    expect(find.text('キー検出済み · SAFE MOCKで未使用'), findsOneWidget);
    expect(find.byIcon(Icons.lock_outline), findsWidgets);
  });

  testWidgets('認証必須serverではBearerを必須表示し空のまま保存しない', (tester) async {
    final repository = _MemorySettings()
      ..value = const ConnectionSettings(baseUrl: 'https://clage.example.test');
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(
          repository: repository,
          initial: repository.value,
          initialServerSettings: const ServerSettings(
            mode: 'live',
            providers: [],
            activeWorkers: [],
            synthesizer: 'none',
            authRequired: true,
            liveApiEnabled: true,
          ),
        ),
      ),
    );

    expect(find.text('Bearerトークン（必須）'), findsOneWidget);
    expect(find.textContaining('トークンが必要'), findsOneWidget);
    await tester.tap(find.text('保存'));
    await tester.pump();
    expect(find.text('このサーバーではBearerトークンが必須です。'), findsOneWidget);
    expect(repository.saveCalls, 0);
  });

  testWidgets('SAFE MOCKでもplanとpolicy scanの後にだけchatを開始する', (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final paths = <String>[];
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        paths.add(request.url.path);
        return _preflightResponse(request, billable: false);
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
    expect(find.text('SAFE MOCK'), findsOneWidget);

    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      'ローカル会議',
    );
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(paths, containsAll(['/api/plan', '/api/policy/scan', '/api/chat']));
    expect(paths.indexOf('/api/plan'), lessThan(paths.indexOf('/api/chat')));
    expect(
      paths.indexOf('/api/policy/scan'),
      lessThan(paths.indexOf('/api/chat')),
    );
    expect(find.text('実APIを使用します'), findsNothing);
    expect(
      tester
          .widget<TextField>(
            find.byWidgetPredicate(
              (widget) =>
                  widget is TextField &&
                  widget.decoration?.hintText == '質問を入力…',
            ),
          )
          .controller
          ?.text,
      isEmpty,
    );
  });

  testWidgets('品質・推論エフォート・Web検索を独立して送信する', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    Map<String, dynamic>? planBody;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/plan') {
          planBody = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        }
        return _preflightResponse(request, billable: false);
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

    expect(find.text('品質'), findsOneWidget);
    expect(find.text('エフォート'), findsOneWidget);
    expect(find.text('WEB OFF'), findsOneWidget);
    expect(find.text('AUTO'), findsNothing);

    final tierSelector = find.byKey(const Key('composer-tier-selector'));
    final effortSelector = find.byKey(const Key('composer-effort-selector'));
    final webToggle = find.byKey(const Key('composer-web-toggle'));
    await tester.tap(
      find.descendant(of: tierSelector, matching: find.text('LOW')),
    );
    await tester.tap(
      find.descendant(of: effortSelector, matching: find.text('HIGH')),
    );
    await tester.tap(webToggle);
    await tester.pump();
    expect(find.text('WEB ON'), findsOneWidget);

    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      '独立設定テスト',
    );
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(planBody?['tier'], 'low');
    expect(planBody?['reasoning_mode'], 'high');
    expect(planBody?['web_search'], isTrue);
  });

  testWidgets('billable planはProviderと最大量を表示して確認までchatしない', (tester) async {
    final paths = <String>[];
    Map<String, dynamic>? chatBody;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        paths.add(request.url.path);
        if (request.url.path == '/api/chat') {
          chatBody = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        }
        return _preflightResponse(request, billable: true);
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
    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      '実API会議',
    );

    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('実APIを使用します'), findsOneWidget);
    expect(find.textContaining('ChatGPT / gpt-test: 最大1回'), findsOneWidget);
    expect(find.text('課金対象APIの最大呼出回数: 1回'), findsOneWidget);
    expect(find.text('課金対象呼出の最大出力token合計: 2400'), findsOneWidget);
    expect(paths, isNot(contains('/api/chat')));

    await tester.tap(find.text('キャンセル'));
    await tester.pumpAndSettle();
    expect(paths, isNot(contains('/api/chat')));

    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認して実行'));
    await tester.pumpAndSettle();
    expect(paths.where((path) => path == '/api/chat'), hasLength(1));
    expect(chatBody?['confirm_live_api'], isTrue);
    expect(chatBody?['confirm_sensitive_data'], isFalse);
  });

  testWidgets('次回から表示しないを選ぶと設定を保存して以後の通常確認を省略する', (tester) async {
    final directRepository = _MemoryDirectSettings(
      const DirectSettings(executionMode: ExecutionMode.referenceServer),
    );
    var chatCalls = 0;
    Map<String, dynamic>? chatBody;
    ApiClient buildClient() => ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/chat') {
          chatCalls++;
          chatBody = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        }
        return _preflightResponse(request, billable: true);
      }),
    );
    final client = buildClient();
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: directRepository,
          clientFactory: (_) => client,
        ),
      ),
    );
    await tester.pumpAndSettle();
    final messageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == '質問を入力…',
    );
    await tester.enterText(messageField, '1回目');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('実APIを使用します'), findsOneWidget);
    expect(find.text('実行して次回から表示しない'), findsOneWidget);
    expect(chatCalls, 0);

    await tester.tap(find.text('実行して次回から表示しない'));
    await tester.pumpAndSettle();
    expect(directRepository.value.showLiveApiConfirmation, isFalse);
    expect(chatCalls, 1);
    expect(chatBody?['confirm_live_api'], isTrue);

    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
    final secondClient = buildClient();
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: directRepository,
          clientFactory: (_) => secondClient,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      '2回目',
    );
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();
    expect(find.text('実APIを使用します'), findsNothing);
    expect(chatCalls, 2);
    expect(chatBody?['confirm_live_api'], isTrue);
  });

  testWidgets('通常確認OFFでもpolicy確認は省略せず機密送信フラグを明示する', (tester) async {
    final directRepository = _MemoryDirectSettings(
      const DirectSettings(
        executionMode: ExecutionMode.referenceServer,
        showLiveApiConfirmation: false,
      ),
    );
    var chatCalls = 0;
    Map<String, dynamic>? chatBody;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/chat') {
          chatCalls++;
          chatBody = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        }
        return _preflightResponse(
          request,
          billable: true,
          policyConfirmation: true,
        );
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: directRepository,
          clientFactory: (_) => client,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      '個人情報を含む質問',
    );
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('個人情報らしい内容を外部送信します'), findsOneWidget);
    expect(find.textContaining('個人情報らしい文字列'), findsOneWidget);
    expect(find.text('実行して次回から表示しない'), findsNothing);
    expect(chatCalls, 0);

    await tester.tap(find.text('確認して実行'));
    await tester.pumpAndSettle();
    expect(chatCalls, 1);
    expect(chatBody?['confirm_live_api'], isTrue);
    expect(chatBody?['confirm_sensitive_data'], isTrue);
  });

  testWidgets('次回非表示の保存に失敗した場合は実APIを呼ばない', (tester) async {
    final directRepository = _MemoryDirectSettings(
      const DirectSettings(executionMode: ExecutionMode.referenceServer),
    )..failConfirmationPreferenceWrite = true;
    var chatCalls = 0;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/chat') chatCalls++;
        return _preflightResponse(request, billable: true);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: directRepository,
          clientFactory: (_) => client,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      '保存失敗時は送らない',
    );
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();
    await tester.tap(find.text('実行して次回から表示しない'));
    await tester.pumpAndSettle();

    expect(chatCalls, 0);
    expect(directRepository.value.showLiveApiConfirmation, isTrue);
    expect(find.textContaining('表示設定を保存できませんでした'), findsOneWidget);
  });

  testWidgets('Direct BYOKでも通常確認OFFならbillable送信を直接開始する', (tester) async {
    final directRepository = _MemoryDirectSettings(
      const DirectSettings(
        showLiveApiConfirmation: false,
        chatGptApiKey: 'configured-key',
      ),
    );
    final localRepository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var chatCalls = 0;
    Map<String, dynamic>? chatBody;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/chat') {
          chatCalls++;
          chatBody = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        }
        return _preflightResponse(request, billable: true);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: directRepository,
          localConversationRepository: localRepository,
          directClientFactory: (_, _) => client,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      'Direct確認省略',
    );
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('実APIを使用します'), findsNothing);
    expect(chatCalls, 1);
    expect(chatBody?['confirm_live_api'], isTrue);
    expect(chatBody?['confirm_sensitive_data'], isFalse);
  });

  testWidgets('通常確認OFFは再生成にも適用して実API確認フラグを送る', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final directRepository = _MemoryDirectSettings(
      const DirectSettings(
        executionMode: ExecutionMode.referenceServer,
        showLiveApiConfirmation: false,
      ),
    );
    Map<String, dynamic>? regenerateBody;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/conversations') {
          return _widgetJsonResponse([_completedConversationSummary()]);
        }
        if (request.url.path == '/api/conversations/conversation-id') {
          return _widgetJsonResponse(_completedConversation());
        }
        if (request.url.path.endsWith('/regeneration-plan')) {
          return _planOnlyResponse(billable: true);
        }
        if (request.url.path.endsWith('/regenerate')) {
          regenerateBody = Map<String, dynamic>.from(
            jsonDecode(request.body) as Map,
          );
          return _widgetJsonResponse({
            'conversation': _completedConversation(),
          });
        }
        return _preflightResponse(request, billable: true);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: directRepository,
          clientFactory: (_) => client,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('再生成テスト'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('ChatGPTの回答を再生成'));
    await tester.pumpAndSettle();

    expect(find.text('実APIを使用します'), findsNothing);
    expect(regenerateBody?['confirm_live_api'], isTrue);
    expect(regenerateBody?['confirm_sensitive_data'], isFalse);
  });

  testWidgets('再生成も通常確認OFF時のpolicy確認を必ず表示する', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final directRepository = _MemoryDirectSettings(
      const DirectSettings(
        executionMode: ExecutionMode.referenceServer,
        showLiveApiConfirmation: false,
      ),
    );
    Map<String, dynamic>? regenerateBody;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/conversations') {
          return _widgetJsonResponse([_completedConversationSummary()]);
        }
        if (request.url.path == '/api/conversations/conversation-id') {
          return _widgetJsonResponse(_completedConversation());
        }
        if (request.url.path.endsWith('/regeneration-plan')) {
          return _planOnlyResponse(billable: true, policyConfirmation: true);
        }
        if (request.url.path.endsWith('/regenerate')) {
          regenerateBody = Map<String, dynamic>.from(
            jsonDecode(request.body) as Map,
          );
          return _widgetJsonResponse({
            'conversation': _completedConversation(),
          });
        }
        return _preflightResponse(request, billable: true);
      }),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: HomeScreen(
          repository: _MemorySettings(),
          directRepository: directRepository,
          clientFactory: (_) => client,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('再生成テスト'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('ChatGPTの回答を再生成'));
    await tester.pump();

    expect(find.text('個人情報らしい内容を外部送信します'), findsOneWidget);
    expect(find.textContaining('個人情報らしい文字列'), findsOneWidget);
    expect(find.text('実行して次回から表示しない'), findsNothing);
    expect(regenerateBody, isNull);

    await tester.tap(find.text('確認して実行'));
    await tester.pumpAndSettle();
    expect(regenerateBody?['confirm_live_api'], isTrue);
    expect(regenerateBody?['confirm_sensitive_data'], isTrue);
  });

  testWidgets('policy blockは送信せずマスク済み文面への置換を提示する', (tester) async {
    final paths = <String>[];
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        paths.add(request.url.path);
        return _preflightResponse(request, billable: false, blocked: true);
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
    final messageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == '質問を入力…',
    );
    await tester.enterText(
      messageField,
      '確認 sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    );

    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('秘密情報らしい文字列を検出しました'), findsOneWidget);
    expect(find.text('• OpenAI APIキーらしい文字列'), findsOneWidget);
    expect(find.textContaining('⟪REDACTED:openai_api_key⟫'), findsOneWidget);
    expect(paths, isNot(contains('/api/chat')));

    await tester.tap(find.text('マスク済み文面へ置換'));
    await tester.pumpAndSettle();
    expect(
      tester.widget<TextField>(messageField).controller?.text,
      '確認 ⟪REDACTED:openai_api_key⟫',
    );
    expect(paths, isNot(contains('/api/chat')));
  });

  testWidgets('SSE切断中の停止競合で完了を停止済みと誤表示しない', (tester) async {
    var chatCalls = 0;
    var cancelCalls = 0;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/chat') {
          chatCalls++;
          return http.Response(
            'id: 1\nevent: meta\ndata: {"conversation_id":"conversation-id","backends":["chatgpt"]}\n\n',
            200,
            headers: {
              'content-type': 'text/event-stream',
              'x-conversation-id': 'conversation-id',
              'x-request-id': 'request-id',
            },
          );
        }
        if (request.url.path == '/api/runs/request-id/cancel') {
          cancelCalls++;
          return _widgetJsonResponse({
            'ok': true,
            'request_id': 'request-id',
            'cancellation_requested': true,
            'cancelled': false,
            'terminal_outcome': 'completed',
            'provider_stop_guaranteed': false,
            'warning': '外部Provider側の課金停止は保証されません',
          });
        }
        return _preflightResponse(request, billable: false);
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
    final messageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == '質問を入力…',
    );
    await tester.enterText(messageField, '切断テスト');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();
    expect(chatCalls, 1);
    expect(find.textContaining('再接続または停止'), findsOneWidget);
    expect(find.byIcon(Icons.stop), findsOneWidget);
    expect(tester.widget<TextField>(messageField).enabled, isTrue);
    await tester.enterText(messageField, '次回の下書き');
    final tierSelector = find.byKey(const Key('composer-tier-selector'));
    expect(
      tester.widget<SegmentedButton<String>>(tierSelector).onSelectionChanged,
      isNotNull,
    );
    await tester.tap(
      find.descendant(of: tierSelector, matching: find.text('HIGH')),
    );
    await tester.pump();
    expect(tester.widget<SegmentedButton<String>>(tierSelector).selected, {
      'high',
    });

    await tester.tap(find.byIcon(Icons.stop));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    expect(cancelCalls, 1);
    expect(chatCalls, 1);
    expect(find.text('完了が先に確定しました'), findsOneWidget);
    expect(find.textContaining('保存済み結果を確認できます'), findsOneWidget);
    expect(tester.widget<TextField>(messageField).controller?.text, '次回の下書き');
  });

  testWidgets('SSEが無通信のままならidle timeoutで再接続状態へ移る', (tester) async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      sseIdleTimeout: const Duration(milliseconds: 100),
      client: _SilentSseClient(),
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
    final messageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == '質問を入力…',
    );

    await tester.enterText(messageField, '無通信テスト');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.textContaining('再接続または停止'), findsOneWidget);
    expect(find.textContaining('無通信'), findsWidgets);
    expect(find.byIcon(Icons.stop), findsOneWidget);
  });

  testWidgets('done後に届いた遅延stream errorは終端結果を上書きしない', (tester) async {
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: _LateErrorAfterDoneClient(),
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
    await tester.enterText(
      find.byWidgetPredicate(
        (widget) =>
            widget is TextField && widget.decoration?.hintText == '質問を入力…',
      ),
      '終端競合テスト',
    );
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.textContaining('通信が切断されました'), findsNothing);
    expect(find.textContaining('完了通知なし'), findsNothing);
    expect(find.textContaining('再接続または停止'), findsNothing);
    expect(find.byIcon(Icons.send), findsOneWidget);
  });

  testWidgets('終端後の履歴取得失敗はSSE再接続でなく保存結果だけを再読込する', (tester) async {
    var conversationReads = 0;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/chat') {
          return http.Response(
            'id: 1\nevent: done\ndata: {"conversation":{"id":"conversation-id"}}\n\n',
            200,
            headers: {
              'content-type': 'text/event-stream',
              'x-conversation-id': 'conversation-id',
              'x-request-id': 'request-id',
            },
          );
        }
        if (request.url.path == '/api/conversations/conversation-id') {
          conversationReads++;
          if (conversationReads == 1) return http.Response('temporary', 503);
          return _widgetJsonResponse({
            'id': 'conversation-id',
            'title': '保存済み会議',
            'turns': [
              {
                'request_id': 'request-id',
                'message': '終端テスト',
                'clean_message': '終端テスト',
                'options': <String, Object>{},
                'answers': <String, Object>{},
                'synthesis': {'ok': true, 'text': '保存後の結論', 'source': 'local'},
              },
            ],
          });
        }
        return _preflightResponse(request, billable: false);
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
    final messageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == '質問を入力…',
    );
    await tester.enterText(messageField, '終端テスト');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('保存結果を再読込'), findsOneWidget);
    expect(find.textContaining('実行中ストリーム'), findsNothing);
    expect(find.byIcon(Icons.send), findsOneWidget);

    await tester.tap(find.text('保存結果を再読込'));
    await tester.pumpAndSettle();
    expect(conversationReads, 2);
    expect(find.text('保存結果を再読込'), findsNothing);
    expect(find.text('保存後の結論'), findsOneWidget);
  });

  testWidgets('再読込したrunningターンへraw requestのまま安全に再接続する', (tester) async {
    tester.view.physicalSize = const Size(1200, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    Map<String, dynamic>? resumedBody;
    var running = true;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/conversations') {
          return _widgetJsonResponse([
            {
              'id': 'conversation-id',
              'title': '復旧会議',
              'updated_at': '2026-07-18T00:00:00Z',
              'turn_count': 1,
              'preview': '継続中',
            },
          ]);
        }
        if (request.url.path == '/api/conversations/conversation-id') {
          return _widgetJsonResponse(
            _savedRunningConversation(running: running),
          );
        }
        if (request.url.path == '/api/chat') {
          resumedBody = Map<String, dynamic>.from(
            jsonDecode(request.body) as Map,
          );
          running = false;
          return http.Response(
            'id: 1\nevent: done\ndata: {"failed":true,"interrupted":true}\n\n',
            200,
            headers: {
              'content-type': 'text/event-stream',
              'x-conversation-id': 'conversation-id',
              'x-request-id': 'saved-running-request',
            },
          );
        }
        return _preflightResponse(request, billable: false);
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
    await tester.tap(find.text('復旧会議'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('実行へ再接続'));
    await tester.pumpAndSettle();

    expect(resumedBody?['request_id'], 'saved-running-request');
    expect(resumedBody?['message'], '!high\n継続中');
    expect(resumedBody?['tier'], 'balanced');
    expect(resumedBody?['providers'], isNull);
    expect(resumedBody?['confirm_live_api'], isTrue);
    expect(resumedBody?['confirm_sensitive_data'], isTrue);
    expect(find.textContaining('サーバー停止で中断'), findsOneWidget);
  });

  testWidgets('再読込したrunningターンの停止競合で失敗終端を通知する', (tester) async {
    tester.view.physicalSize = const Size(1200, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    var running = true;
    var cancelCalls = 0;
    final client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient((request) async {
        if (request.url.path == '/api/conversations') {
          return _widgetJsonResponse([
            {
              'id': 'conversation-id',
              'title': '停止会議',
              'updated_at': '2026-07-18T00:00:00Z',
              'turn_count': 1,
              'preview': '継続中',
            },
          ]);
        }
        if (request.url.path == '/api/conversations/conversation-id') {
          return _widgetJsonResponse(
            _savedRunningConversation(running: running),
          );
        }
        if (request.url.path == '/api/runs/saved-running-request/cancel') {
          cancelCalls++;
          running = false;
          return _widgetJsonResponse({
            'ok': true,
            'request_id': 'saved-running-request',
            'cancellation_requested': true,
            'cancelled': false,
            'terminal_outcome': 'failed',
            'provider_stop_guaranteed': false,
            'warning': '外部Provider側の課金停止は保証されません',
          });
        }
        return _preflightResponse(request, billable: false);
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
    await tester.tap(find.text('停止会議'));
    await tester.pumpAndSettle();

    await tester.tap(find.text('停止を要求'));
    await tester.pumpAndSettle();

    expect(cancelCalls, 1);
    expect(find.textContaining('サーバー停止で中断'), findsOneWidget);
    expect(find.text('停止を要求'), findsNothing);
    expect(find.textContaining('処理失敗が確定しました'), findsOneWidget);
  });
}

Map<String, dynamic> _savedRunningConversation({required bool running}) => {
  'id': 'conversation-id',
  'title': '保存会議',
  'turns': [
    {
      'request_id': 'saved-running-request',
      'message': '!high\n継続中',
      'clean_message': '継続中',
      'status': running ? 'running' : 'interrupted',
      'interrupted': !running,
      'failed': !running,
      'usage_may_be_incomplete': true,
      'options': {
        'tier': 'high',
        'debate': true,
        'providers': ['chatgpt'],
        'synthesize': true,
        'blind': true,
      },
      'resume_request': {
        'tier': 'balanced',
        'debate': false,
        'providers': null,
        'synthesize': true,
        'blind': false,
        'confirm_live_api': true,
        'confirm_sensitive_data': true,
      },
      'answers': <String, Object>{},
      'synthesis': running
          ? {'ok': false, 'pending': true}
          : {
              'ok': false,
              'error': '前回の会議はサーバー停止により完了しませんでした',
              'source': 'none',
              'interrupted': true,
            },
    },
  ],
};

Map<String, dynamic> _completedConversationSummary() => {
  'id': 'conversation-id',
  'title': '再生成テスト',
  'updated_at': '2026-07-21T00:00:00Z',
  'turn_count': 1,
  'preview': '再生成して',
};

Map<String, dynamic> _completedConversation() => {
  'id': 'conversation-id',
  'title': '再生成テスト',
  'turns': [
    {
      'request_id': 'completed-turn-request',
      'message': '再生成して',
      'clean_message': '再生成して',
      'status': 'completed',
      'options': {
        'tier': 'high',
        'reasoning_mode': 'high',
        'providers': ['chatgpt'],
        'synthesize': false,
      },
      'answers': {
        'chatgpt': {
          'ok': true,
          'text': '最初の回答',
          'model': 'gpt-test',
          'completion_status': 'completed',
        },
      },
      'synthesis': {'ok': false, 'text': '', 'source': 'none', 'skipped': true},
    },
  ],
};

http.Response _planOnlyResponse({
  required bool billable,
  bool policyConfirmation = false,
}) => _preflightResponse(
  http.Request('POST', Uri.parse('http://localhost:8000/api/plan')),
  billable: billable,
  policyConfirmation: policyConfirmation,
);

http.Response _preflightResponse(
  http.Request request, {
  required bool billable,
  bool blocked = false,
  bool policyConfirmation = false,
}) {
  final policyAction = blocked
      ? 'block'
      : policyConfirmation
      ? 'confirm'
      : 'allow';
  final policyFindings = blocked
      ? <Object>[
          {
            'rule_id': 'openai_api_key',
            'label': 'OpenAI APIキーらしい文字列',
            'severity': 'block',
            'start': 3,
            'end': 43,
          },
        ]
      : policyConfirmation
      ? <Object>[
          {
            'rule_id': 'personal_data',
            'label': '個人情報らしい文字列',
            'severity': 'confirm',
            'start': 0,
            'end': 4,
          },
        ]
      : <Object>[];
  switch (request.url.path) {
    case '/api/health':
      return _widgetJsonResponse({
        'ok': true,
        'version': 'test',
        'mode': billable ? 'mixed' : 'mock',
        'synthesizer': 'synthesizer',
      });
    case '/api/settings':
      return _widgetJsonResponse({
        'mode': billable ? 'mixed' : 'mock',
        'live_api_enabled': billable,
        'providers': [
          {
            'name': 'chatgpt',
            'label': 'ChatGPT',
            'configured': billable,
            'mode': billable ? 'live' : 'mock',
            'models': {
              'low': 'gpt-test',
              'balanced': 'gpt-test',
              'high': 'gpt-test',
            },
          },
        ],
        'active_workers': ['chatgpt'],
        'synthesizer': 'synthesizer',
        'auth_required': false,
        'web_search': {
          'enabled': true,
          'default': false,
          'max_uses': 3,
          'strict_total_limit': false,
        },
      });
    case '/api/conversations':
      return _widgetJsonResponse(<Object>[]);
    case '/api/policy/scan':
      return _widgetJsonResponse({
        'action': policyAction,
        'findings': policyFindings,
        'redacted_text': blocked ? '確認 ⟪REDACTED:openai_api_key⟫' : '会議',
        'disclaimer': '',
      });
    case '/api/plan':
      return _widgetJsonResponse({
        'allowed': !blocked,
        'block_reasons': blocked ? ['policy_blocked'] : <Object>[],
        'billable': billable,
        'mode': billable ? 'mixed' : 'mock',
        'providers': [
          {
            'name': 'chatgpt',
            'label': 'ChatGPT',
            'mode': billable ? 'live' : 'mock',
            'model': billable ? 'gpt-test' : 'mock',
            'billable': billable,
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
        'max_output_tokens': {'total': 2400, 'live_total': billable ? 2400 : 0},
        'policy': {
          'action': policyAction,
          'findings': policyFindings,
          'redacted_text': blocked ? '確認 ⟪REDACTED:openai_api_key⟫' : '会議',
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

http.Response _widgetJsonResponse(Object data) => http.Response.bytes(
  utf8.encode(jsonEncode(data)),
  200,
  headers: {'content-type': 'application/json; charset=utf-8'},
);

class _LateErrorAfterDoneClient extends http.BaseClient {
  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final typed = request as http.Request;
    if (request.url.path == '/api/chat') {
      final controller = StreamController<List<int>>();
      scheduleMicrotask(() {
        controller.add(
          utf8.encode(
            'id: 1\nevent: done\ndata: {"conversation":{"id":"conversation-id"}}\n\n',
          ),
        );
        controller.addError(StateError('late error after done'));
        unawaited(controller.close());
      });
      return http.StreamedResponse(
        controller.stream,
        200,
        headers: {
          'content-type': 'text/event-stream',
          'x-conversation-id': 'conversation-id',
          'x-request-id': 'late-error-request',
        },
      );
    }
    final response = _preflightResponse(typed, billable: false);
    return http.StreamedResponse(
      Stream.value(response.bodyBytes),
      response.statusCode,
      headers: response.headers,
    );
  }
}

class _SilentSseClient extends http.BaseClient {
  final _chat = StreamController<List<int>>();

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) async {
    final typed = request as http.Request;
    if (request.url.path == '/api/chat') {
      return http.StreamedResponse(
        _chat.stream,
        200,
        headers: {
          'content-type': 'text/event-stream',
          'x-conversation-id': 'conversation-id',
          'x-request-id': 'silent-request',
        },
      );
    }
    final response = _preflightResponse(typed, billable: false);
    return http.StreamedResponse(
      Stream.value(response.bodyBytes),
      response.statusCode,
      headers: response.headers,
    );
  }

  @override
  void close() {
    unawaited(_chat.close());
  }
}
