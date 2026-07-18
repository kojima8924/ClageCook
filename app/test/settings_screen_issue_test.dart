import 'dart:convert';

import 'package:clage_cook/models.dart';
import 'package:clage_cook/screens/settings_screen.dart';
import 'package:clage_cook/services/api_client.dart';
import 'package:clage_cook/services/settings_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('Web版のトークン保存リスクは保存成否に関係なく警告する', () {
    expect(webTokenStorageWarning(isWeb: false), isEmpty);
    expect(webTokenStorageWarning(isWeb: true), contains('ブラウザ内ストレージ'));
    expect(webTokenStorageWarning(isWeb: true), contains('XSS'));
  });

  testWidgets('runtime設定は取得元URLと入力中URLが異なる間は送信しない', (tester) async {
    tester.view.physicalSize = const Size(1200, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final connections = <ConnectionSettings>[];
    final patchedUris = <Uri>[];
    ApiClient factory(ConnectionSettings settings) {
      connections.add(settings);
      return ApiClient(
        settings,
        client: MockClient((request) async {
          patchedUris.add(request.url);
          expect(request.method, 'PATCH');
          expect(request.url.path, '/api/settings/runtime');
          return _jsonResponse(_serverSettingsJson(revision: 8));
        }),
      );
    }

    const initial = ConnectionSettings(
      baseUrl: 'https://a.example.test',
      token: 'token-a',
    );
    await tester.pumpWidget(
      MaterialApp(
        home: SettingsScreen(
          repository: _MemorySettings(),
          initial: initial,
          initialServerSettings: _serverSettings,
          clientFactory: factory,
        ),
      ),
    );
    final urlField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.labelText == 'サーバーURL',
    );
    final tokenField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField &&
          (widget.decoration?.labelText ?? '').startsWith('Bearerトークン'),
    );

    expect(find.text('SAFE MOCK'), findsOneWidget);

    await tester.enterText(urlField, 'https://b.example.test');
    await tester.pump();

    expect(
      tester
          .widget<IconButton>(find.widgetWithIcon(IconButton, Icons.tune))
          .onPressed,
      isNull,
    );
    expect(find.text('SAFE MOCK'), findsNothing);
    expect(find.textContaining('入力中のURLまたはトークン'), findsOneWidget);
    expect(connections, isEmpty);

    await tester.enterText(urlField, 'https://a.example.test/');
    await tester.pump();
    expect(find.text('SAFE MOCK'), findsOneWidget);

    await tester.enterText(tokenField, 'token-b');
    await tester.pump();
    expect(find.text('SAFE MOCK'), findsNothing);
    expect(
      tester
          .widget<IconButton>(find.widgetWithIcon(IconButton, Icons.tune))
          .onPressed,
      isNull,
    );
    expect(find.textContaining('入力中のURLまたはトークン'), findsOneWidget);

    await tester.enterText(tokenField, 'token-a');
    await tester.pump();
    expect(find.text('SAFE MOCK'), findsOneWidget);
    expect(find.textContaining('入力中のURLまたはトークン'), findsNothing);
    final runtimeButton = find.widgetWithIcon(IconButton, Icons.tune);
    expect(tester.widget<IconButton>(runtimeButton).onPressed, isNotNull);
    await tester.ensureVisible(runtimeButton);
    await tester.tap(runtimeButton);
    await tester.pumpAndSettle();
    final dialog = find.byType(AlertDialog);
    await tester.tap(
      find.descendant(of: dialog, matching: find.text('保存')).last,
    );
    await tester.pumpAndSettle();

    expect(connections, hasLength(1));
    expect(connections.single.baseUrl, 'https://a.example.test');
    expect(connections.single.token, 'token-a');
    expect(patchedUris.single.origin, 'https://a.example.test');
    expect(find.textContaining('revision=8'), findsOneWidget);
  });
}

const _serverSettings = ServerSettings(
  mode: 'mock',
  providers: [],
  activeWorkers: [],
  synthesizer: 'mock',
  authRequired: false,
  runtimeSettings: RuntimeModelSettings(
    revision: 7,
    writable: true,
    synthesizerProvider: 'auto',
    effectiveSynthesizerModels: {
      'low': 'mock-low',
      'balanced': 'mock-balanced',
      'high': 'mock-high',
    },
    catalog: {},
  ),
);

Map<String, Object> _serverSettingsJson({required int revision}) => {
  'mode': 'mock',
  'providers': <Object>[],
  'active_workers': <Object>[],
  'synthesizer': 'mock',
  'auth_required': false,
  'live_api_enabled': false,
  'runtime_settings': {
    'revision': revision,
    'writable': true,
    'synthesizer_provider': 'auto',
    'effective_synthesizer_models': {
      'low': 'mock-low',
      'balanced': 'mock-balanced',
      'high': 'mock-high',
    },
    'catalog': <String, Object>{},
  },
};

http.Response _jsonResponse(Object data) => http.Response.bytes(
  utf8.encode(jsonEncode(data)),
  200,
  headers: {'content-type': 'application/json; charset=utf-8'},
);

class _MemorySettings implements SettingsRepository {
  @override
  Future<ConnectionSettings> load() async => const ConnectionSettings();

  @override
  Future<void> save(ConnectionSettings settings) async {}
}
