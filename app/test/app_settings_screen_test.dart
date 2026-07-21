import 'package:clage_cook/models.dart';
import 'package:clage_cook/screens/app_settings_screen.dart';
import 'package:clage_cook/services/direct_settings_store.dart';
import 'package:clage_cook/services/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('保存済みAPIキーを入力欄へ読み戻さずmode切替でも維持する', (tester) async {
    final direct = _MemoryDirectRepository(
      const DirectSettings(claudeApiKey: 'never-render-this-key'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
        ),
      ),
    );

    expect(find.textContaining('never-render-this-key'), findsNothing);

    await tester.tap(find.text('開発用サーバー'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('実行方式を保存'),
      500,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('実行方式を保存'));
    await tester.pumpAndSettle();

    expect(direct.value.executionMode, ExecutionMode.referenceServer);
    expect(direct.value.claudeApiKey, 'never-render-this-key');
  });

  testWidgets('Providerは高密度の概要表示で閉じ、保存済みキーを展開後も読み戻さない', (tester) async {
    const secret = 'never-render-this-key';
    final direct = _MemoryDirectRepository(
      const DirectSettings(claudeApiKey: secret),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
        ),
      ),
    );

    final scrollable = find.byType(Scrollable).first;
    final claudeCard = find.byKey(const ValueKey('direct-provider-claude'));
    await tester.scrollUntilVisible(claudeCard, 350, scrollable: scrollable);
    await tester.ensureVisible(claudeCard);
    await tester.pumpAndSettle();

    expect(find.text('キー設定済み'), findsOneWidget);
    expect(find.textContaining('接続: 未確認'), findsOneWidget);
    expect(find.textContaining('モデル: 品質別'), findsWidgets);
    expect(find.text('直接送信先: api.anthropic.com'), findsNothing);
    expect(
      find.byKey(const ValueKey('direct-provider-key-claude')),
      findsNothing,
    );

    await tester.tap(claudeCard);
    await tester.pumpAndSettle();

    expect(find.text('直接送信先: api.anthropic.com'), findsOneWidget);
    final keyFieldFinder = find.byKey(
      const ValueKey('direct-provider-key-claude'),
    );
    final keyField = tester.widget<TextField>(keyFieldFinder);
    expect(keyField.controller?.text, isEmpty);
    expect(keyField.obscureText, isTrue);
    expect(find.textContaining(secret), findsNothing);
    expect(tester.getSemantics(keyFieldFinder).value, isNot(contains(secret)));
  });

  testWidgets('既知モデルを優先選択できカスタムIDは詳細に分離する', (tester) async {
    final direct = _MemoryDirectRepository(
      const DirectSettings(
        claudeApiKey: 'configured-key',
        claudeModelOverride: 'existing-custom-model',
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
        ),
      ),
    );

    final scrollable = find.byType(Scrollable).first;
    final claudeCard = find.byKey(const ValueKey('direct-provider-claude'));
    await tester.scrollUntilVisible(claudeCard, 350, scrollable: scrollable);
    await tester.ensureVisible(claudeCard);
    await tester.pumpAndSettle();
    expect(
      find.textContaining('モデル: カスタム・existing-custom-model'),
      findsOneWidget,
    );

    await tester.tap(claudeCard);
    await tester.pumpAndSettle();
    final customFieldFinder = find.byKey(
      const ValueKey('direct-provider-custom-model-claude'),
    );
    expect(customFieldFinder, findsOneWidget);
    expect(
      tester.widget<TextField>(customFieldFinder).controller?.text,
      'existing-custom-model',
    );

    final modelDropdown = find.byKey(
      const ValueKey('direct-provider-model-claude-__custom__'),
    );
    await tester.tap(modelDropdown);
    await tester.pumpAndSettle();
    expect(find.text('品質別の既定モデル（推奨）'), findsWidgets);
    expect(find.text('詳細: カスタムmodel ID'), findsWidgets);
    await tester.tap(find.text('claude-sonnet-5（BALANCED）').last);
    await tester.pumpAndSettle();

    expect(customFieldFinder, findsNothing);
    expect(find.textContaining('モデル: 固定・claude-sonnet-5'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.text('実行方式を保存'),
      450,
      scrollable: scrollable,
    );
    await tester.tap(find.text('実行方式を保存'));
    await tester.pumpAndSettle();

    expect(direct.value.claudeModelOverride, 'claude-sonnet-5');
  });

  testWidgets('Direct BYOKはAPIキーなしで有効化しない', (tester) async {
    final direct = _MemoryDirectRepository(const DirectSettings());
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
        ),
      ),
    );

    await tester.scrollUntilVisible(
      find.text('実行方式を保存'),
      500,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('実行方式を保存'));
    await tester.pump();

    expect(find.text('Direct BYOKには1つ以上のAPIキーが必要です。'), findsOneWidget);
    expect(direct.saveCount, 0);
  });

  testWidgets('既定エフォートは設定画面でAUTOからHIGHまで選べる', (tester) async {
    final direct = _MemoryDirectRepository(
      const DirectSettings(claudeApiKey: 'configured-key'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
        ),
      ),
    );

    await tester.scrollUntilVisible(
      find.text('AUTO（モデル推奨値）'),
      400,
      scrollable: find.byType(Scrollable).first,
    );
    expect(find.text('既定の推論エフォート'), findsOneWidget);
    await tester.tap(find.text('AUTO（モデル推奨値）'));
    await tester.pumpAndSettle();
    expect(find.text('LOW'), findsOneWidget);
    expect(find.text('MEDIUM'), findsOneWidget);
    expect(find.text('HIGH'), findsOneWidget);

    await tester.tap(find.text('MEDIUM'));
    await tester.pumpAndSettle();
    await tester.scrollUntilVisible(
      find.text('実行方式を保存'),
      400,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.text('実行方式を保存'));
    await tester.pumpAndSettle();

    expect(direct.value.reasoningMode, ReasoningMode.medium);
  });

  testWidgets('トークン利用量台帳の表示設定をOFFで保存する', (tester) async {
    final direct = _MemoryDirectRepository(
      const DirectSettings(claudeApiKey: 'configured-key'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
        ),
      ),
    );

    final scrollable = find.byType(Scrollable).first;
    final toggle = find.byKey(const ValueKey('show-token-usage-ledger'));
    await tester.scrollUntilVisible(toggle, 300, scrollable: scrollable);
    expect(tester.widget<SwitchListTile>(toggle).value, isTrue);

    await tester.tap(toggle);
    await tester.pumpAndSettle();
    expect(tester.widget<SwitchListTile>(toggle).value, isFalse);
    expect(find.textContaining('OFFでもusageの取得'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('実行方式を保存'),
      500,
      scrollable: scrollable,
    );
    await tester.tap(find.text('実行方式を保存'));
    await tester.pumpAndSettle();

    expect(direct.value.showTokenUsageLedger, isFalse);
  });

  testWidgets('実API利用確認をOFFにしてもpolicy確認を維持すると明示する', (tester) async {
    final direct = _MemoryDirectRepository(
      const DirectSettings(claudeApiKey: 'configured-key'),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
        ),
      ),
    );

    final scrollable = find.byType(Scrollable).first;
    final toggle = find.byKey(const ValueKey('show-live-api-confirmation'));
    await tester.scrollUntilVisible(toggle, 300, scrollable: scrollable);
    expect(tester.widget<SwitchListTile>(toggle).value, isTrue);

    await tester.tap(toggle);
    await tester.pumpAndSettle();
    expect(tester.widget<SwitchListTile>(toggle).value, isFalse);
    expect(find.textContaining('policy上必要な確認は常に表示'), findsOneWidget);

    await tester.scrollUntilVisible(
      find.text('実行方式を保存'),
      500,
      scrollable: scrollable,
    );
    await tester.tap(find.text('実行方式を保存'));
    await tester.pumpAndSettle();

    expect(direct.value.showLiveApiConfirmation, isFalse);
  });

  testWidgets('配布版設定では開発用サーバー切替を表示しない', (tester) async {
    final direct = _MemoryDirectRepository(
      const DirectSettings(
        executionMode: ExecutionMode.referenceServer,
        claudeApiKey: 'configured-key',
      ),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: AppSettingsScreen(
          directRepository: direct,
          serverRepository: _MemoryServerRepository(),
          initialDirect: await direct.load(),
          initialServer: const ConnectionSettings(),
          allowReferenceServer: false,
        ),
      ),
    );

    expect(find.text('開発用サーバー'), findsNothing);
    expect(find.textContaining('配布版はDirect BYOK専用'), findsOneWidget);
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('direct-provider-claude')),
      400,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(find.byKey(const ValueKey('direct-provider-claude')));
    await tester.pumpAndSettle();
    expect(find.text('直接送信先: api.anthropic.com'), findsOneWidget);
  });
}

class _MemoryDirectRepository implements DirectSettingsRepository {
  _MemoryDirectRepository(this.value);

  DirectSettings value;
  int saveCount = 0;

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
  Future<void> save(DirectSettings settings) async {
    value = settings;
    saveCount++;
  }

  @override
  Future<void> setShowLiveApiConfirmation(bool enabled) async {
    value = value.copyWith(showLiveApiConfirmation: enabled);
  }
}

class _MemoryServerRepository implements SettingsRepository {
  ConnectionSettings value = const ConnectionSettings();

  @override
  Future<ConnectionSettings> load() async => value;

  @override
  Future<void> save(ConnectionSettings settings) async {
    value = settings;
  }
}
