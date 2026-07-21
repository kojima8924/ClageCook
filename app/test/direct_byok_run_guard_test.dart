import 'dart:async';
import 'dart:convert';

import 'package:clage_cook/services/api_client.dart';
import 'package:clage_cook/services/direct_byok_client.dart';
import 'package:clage_cook/services/direct_provider_client.dart';
import 'package:clage_cook/services/direct_run_guard.dart';
import 'package:clage_cook/services/direct_settings_store.dart';
import 'package:clage_cook/services/local_conversation_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test(
    'conference and regeneration each hold an Android run guard lease',
    () async {
      final transitions = <String>[];
      final guard = DirectRunGuard(
        enabled: true,
        invoker: (method, arguments) async {
          transitions.add('$method:${arguments['operation']}');
          return null;
        },
      );
      final client = _client(
        runGuard: guard,
        onProviderCall: () => transitions.add('provider'),
      );

      final stream = await client.startChat(
        message: '実行ガードを確認',
        providers: const ['chatgpt'],
        confirmLiveApi: true,
      );
      await stream.events.drain<void>();
      expect(transitions, ['start:conference', 'provider', 'stop:conference']);

      final turn = (await client.conversation(
        stream.conversationId,
      )).turns.single;
      await client.regenerate(
        conversationId: stream.conversationId,
        turnRequestId: turn.requestId,
        target: 'answer',
        provider: 'chatgpt',
        confirmLiveApi: true,
        regenerationId: 'guard-regeneration-0001',
      );

      expect(transitions, [
        'start:conference',
        'provider',
        'stop:conference',
        'start:regeneration',
        'provider',
        'stop:regeneration',
      ]);
      expect(guard.activeCount, 0);
      client.close();
    },
  );

  test(
    'Android foreground-service start failure blocks paid provider calls',
    () async {
      var providerCalls = 0;
      final guard = DirectRunGuard(
        enabled: true,
        invoker: (method, _) async {
          if (method == 'start') throw StateError('native start failed');
          return null;
        },
      );
      final client = _client(
        runGuard: guard,
        onProviderCall: () => providerCalls++,
      );

      final stream = await client.startChat(
        message: '失敗時も会議を続ける',
        providers: const ['chatgpt'],
        confirmLiveApi: true,
      );
      final events = await stream.events.toList();

      expect(events.map((event) => event.event), contains('error'));
      expect(events.map((event) => event.event), contains('done'));
      expect(
        (await client.conversation(stream.conversationId)).turns,
        hasLength(1),
      );
      expect(
        (await client.conversation(stream.conversationId)).turns.single.answers,
        isEmpty,
      );
      expect(guard.lastWarning?.phase, 'start');
      expect(guard.activeCount, 0);
      expect(providerCalls, 0);
      client.close();
    },
  );

  test('long Direct provider wait emits activity heartbeats', () async {
    final providerRelease = Completer<void>();
    final guard = DirectRunGuard(enabled: false);
    final client = _client(
      runGuard: guard,
      heartbeatInterval: const Duration(milliseconds: 5),
      beforeProviderResponse: () => providerRelease.future,
    );

    final stream = await client.startChat(
      message: '長時間応答を待つ',
      providers: const ['chatgpt'],
      confirmLiveApi: true,
    );
    final events = <SseEvent>[];
    final subscription = stream.events.listen(events.add);
    await Future<void>.delayed(const Duration(milliseconds: 40));

    expect(
      events.where((event) => event.event == SseDecoder.keepAliveEvent),
      isNotEmpty,
    );

    providerRelease.complete();
    await subscription.asFuture<void>();
    expect(events.any((event) => event.event == 'done'), isTrue);
    expect(guard.activeCount, 0);
    client.close();
  });
}

DirectByokClient _client({
  required DirectRunGuard runGuard,
  void Function()? onProviderCall,
  Future<void> Function()? beforeProviderResponse,
  Duration heartbeatInterval = const Duration(seconds: 20),
}) {
  final repository = SharedPreferencesLocalConversationRepository(
    namespace: LocalConversationNamespace.directByok,
    valueStore: MemoryLocalConversationValueStore(),
  );
  return DirectByokClient(
    settings: const DirectSettings(chatGptApiKey: 'test-key'),
    conversations: repository,
    runGuard: runGuard,
    heartbeatInterval: heartbeatInterval,
    providerClientFactory: () => DirectProviderClient(
      client: MockClient((_) async {
        onProviderCall?.call();
        await beforeProviderResponse?.call();
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'status': 'completed',
              'model': 'gpt-test',
              'output': [
                {
                  'type': 'message',
                  'content': [
                    {'type': 'output_text', 'text': 'ガード付き回答'},
                  ],
                },
              ],
              'usage': {
                'input_tokens': 3,
                'output_tokens': 5,
                'total_tokens': 8,
              },
            }),
          ),
          200,
        );
      }),
    ),
  );
}
