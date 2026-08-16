import 'dart:async';
import 'dart:convert';

import 'package:clage_cook/models.dart';
import 'package:clage_cook/screens/home_screen.dart';
import 'package:clage_cook/services/api_client.dart';
import 'package:clage_cook/services/settings_store.dart';
import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  testWidgets('更新中に別会話を選んでも遅い応答が表示を巻き戻さない', (tester) async {
    final backend = _HomeBackend()..delaySecondConversationA = true;
    await _pumpHome(tester, backend);
    await tester.tap(_conversationTile('会話A'));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithIcon(IconButton, Icons.refresh));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 10));
    expect(backend.delayedConversationStarted.isCompleted, isTrue);

    await tester.tap(_conversationTile('会話B'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 10));
    expect(tester.widget<ListTile>(_conversationTile('会話B')).selected, isTrue);

    backend.releaseDelayedConversation.complete();
    await tester.pumpAndSettle();

    expect(tester.widget<ListTile>(_conversationTile('会話B')).selected, isTrue);
    expect(tester.widget<ListTile>(_conversationTile('会話A')).selected, isFalse);
    expect(find.text('会話B'), findsNWidgets(2));
  });

  testWidgets('別会話の読込中は古い履歴を保持せず送信できない', (tester) async {
    final backend = _HomeBackend()..delayConversationB = true;
    await _pumpHome(tester, backend);
    await tester.tap(_conversationTile('会話A'));
    await tester.pumpAndSettle();

    await tester.tap(_conversationTile('会話B'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 10));

    expect(backend.conversationBLoadStarted.isCompleted, isTrue);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(
      tester
          .widget<IconButton>(find.widgetWithIcon(IconButton, Icons.send))
          .onPressed,
      isNull,
    );

    backend.releaseConversationBLoad.complete();
    await tester.pumpAndSettle();
    expect(tester.widget<ListTile>(_conversationTile('会話B')).selected, isTrue);
  });

  testWidgets('添付upload中は会話切替を無効化し、開始元会話にだけ追加する', (tester) async {
    final backend = _HomeBackend()..delayAttachmentUpload = true;
    final result = FilePickerResult([
      PlatformFile(
        name: 'note.txt',
        size: 3,
        bytes: Uint8List.fromList(utf8.encode('abc')),
      ),
    ]);
    await _pumpHome(tester, backend, attachmentPicker: () async => result);
    await tester.tap(_conversationTile('会話A'));
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.attach_file));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 10));
    expect(backend.attachmentUploadStarted.isCompleted, isTrue);
    expect(
      tester
          .widget<FilledButton>(find.widgetWithText(FilledButton, '新しい会話'))
          .onPressed,
      isNull,
    );
    expect(tester.widget<ListTile>(_conversationTile('会話B')).onTap, isNull);

    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyN);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pump();
    expect(tester.widget<ListTile>(_conversationTile('会話A')).selected, isTrue);

    backend.releaseAttachmentUpload.complete();
    await tester.pumpAndSettle();

    expect(tester.widget<ListTile>(_conversationTile('会話A')).selected, isTrue);
    expect(find.textContaining('note.txt'), findsOneWidget);
    expect(backend.uploadConversationIds, ['a']);
  });

  testWidgets('ローカルメモの確定ラベルは入力内容に追従する', (tester) async {
    final backend = _HomeBackend();
    await _pumpHome(tester, backend);
    await tester.tap(_conversationTile('会話A'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('ローカルメモを追加'));
    await tester.pumpAndSettle();
    final dialog = find.byType(AlertDialog);
    final field = find.descendant(of: dialog, matching: find.byType(TextField));

    expect(
      find.descendant(of: dialog, matching: find.text('クリア')),
      findsOneWidget,
    );
    await tester.enterText(field, '設計メモ');
    await tester.pump();
    expect(
      find.descendant(of: dialog, matching: find.text('保存')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: dialog, matching: find.text('クリア')),
      findsNothing,
    );

    await tester.enterText(field, '');
    await tester.pump();
    expect(
      find.descendant(of: dialog, matching: find.text('クリア')),
      findsOneWidget,
    );
    await tester.tap(find.descendant(of: dialog, matching: find.text('キャンセル')));
    await tester.pumpAndSettle();
  });

  testWidgets('preflight失敗は実エラー詳細を画面に表示する', (tester) async {
    final backend = _HomeBackend()..failPlan = true;
    await _pumpHome(tester, backend);
    final messageField = find.byWidgetPredicate(
      (widget) =>
          widget is TextField && widget.decoration?.hintText == '質問を入力…',
    );

    await tester.enterText(messageField, '送信前確認');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.textContaining('plan unavailable'), findsOneWidget);
    expect(backend.paths, isNot(contains('/api/chat')));
  });
}

Future<void> _pumpHome(
  WidgetTester tester,
  _HomeBackend backend, {
  AttachmentPicker? attachmentPicker,
}) async {
  tester.view.physicalSize = const Size(1200, 850);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      home: HomeScreen(
        repository: _MemorySettings(),
        clientFactory: (_) => backend.client,
        attachmentPicker: attachmentPicker ?? () async => null,
      ),
    ),
  );
  await tester.pumpAndSettle();
}

Finder _conversationTile(String title) =>
    find.ancestor(of: find.text(title), matching: find.byType(ListTile)).first;

class _HomeBackend {
  _HomeBackend() {
    client = ApiClient(
      const ConnectionSettings(baseUrl: 'http://localhost:8000'),
      client: MockClient(_handle),
    );
  }

  late final ApiClient client;
  bool delaySecondConversationA = false;
  bool delayConversationB = false;
  bool delayAttachmentUpload = false;
  bool failPlan = false;
  int _conversationALoads = 0;
  final delayedConversationStarted = Completer<void>();
  final releaseDelayedConversation = Completer<void>();
  final conversationBLoadStarted = Completer<void>();
  final releaseConversationBLoad = Completer<void>();
  final attachmentUploadStarted = Completer<void>();
  final releaseAttachmentUpload = Completer<void>();
  final paths = <String>[];
  final uploadConversationIds = <String>[];

  Future<http.Response> _handle(http.Request request) async {
    paths.add(request.url.path);
    switch (request.url.path) {
      case '/api/health':
        return _jsonResponse({
          'ok': true,
          'mode': 'mock',
          'synthesizer': 'mock',
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
          'synthesizer': 'mock',
          'auth_required': false,
        });
      case '/api/conversations':
        return _jsonResponse(_conversationListBody([_summary('a', '会話A'), _summary('b', '会話B')]));
      case '/api/conversations/a':
        _conversationALoads++;
        if (delaySecondConversationA && _conversationALoads == 2) {
          if (!delayedConversationStarted.isCompleted) {
            delayedConversationStarted.complete();
          }
          await releaseDelayedConversation.future;
        }
        return _jsonResponse(_conversation('a', '会話A'));
      case '/api/conversations/b':
        if (delayConversationB) {
          if (!conversationBLoadStarted.isCompleted) {
            conversationBLoadStarted.complete();
          }
          await releaseConversationBLoad.future;
        }
        return _jsonResponse(_conversation('b', '会話B'));
      case '/api/conversations/a/attachments':
        uploadConversationIds.add('a');
        if (!attachmentUploadStarted.isCompleted) {
          attachmentUploadStarted.complete();
        }
        if (delayAttachmentUpload) await releaseAttachmentUpload.future;
        return _jsonResponse({
          'id': 'attachment-1',
          'conversation_id': 'a',
          'name': 'note.txt',
          'mime_type': 'text/plain',
          'kind': 'text',
          'size_bytes': 3,
        });
      case '/api/policy/scan':
        return _jsonResponse({
          'action': 'allow',
          'findings': <Object>[],
          'redacted_text': '送信前確認',
          'disclaimer': '',
        });
      case '/api/plan':
        if (failPlan) {
          return http.Response.bytes(
            utf8.encode(jsonEncode({'detail': 'plan unavailable'})),
            503,
            headers: {'content-type': 'application/json; charset=utf-8'},
          );
        }
        return _jsonResponse(_plan());
      default:
        return http.Response('not found', 404);
    }
  }
}

Map<String, Object> _summary(String id, String title) => {
  'id': id,
  'title': title,
  'updated_at': '2026-07-19T00:00:00Z',
  'turn_count': 0,
  'preview': '$titleの概要',
};

Map<String, Object> _conversation(String id, String title) => {
  'id': id,
  'title': title,
  'turns': [
    {
      'request_id': '$id-turn',
      'message': '過去の質問',
      'clean_message': '過去の質問',
      'answers': <String, Object>{},
      'synthesis': {'ok': false, 'skipped': true},
      'options': <String, Object>{},
      'status': 'completed',
    },
  ],
  'memory': {'revision': 0, 'text': '', 'updated_at': ''},
};

Map<String, Object> _plan() => {
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
    'name': 'mock',
    'label': 'mock',
    'mode': 'mock',
    'model': 'mock',
    'enabled': false,
    'billable': false,
    'max_calls': 0,
  },
  'calls': {'answers': 1, 'debate': 0, 'synthesis': 0, 'total': 1},
  'max_output_tokens': {'total': 1, 'live_total': 0},
  'policy': {
    'action': 'allow',
    'findings': <Object>[],
    'redacted_text': '送信前確認',
    'disclaimer': '',
  },
  'warnings': <Object>[],
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

/// `GET /api/conversations` の items 封筒(0.2.0で裸の配列から変更)。
Map<String, Object> _conversationListBody(List<Object> items) => {
  'items': items,
  'corrupt_count': 0,
  'corrupt': <Object>[],
};
