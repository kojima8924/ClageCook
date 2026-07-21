import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:archive/archive.dart';
import 'package:clage_cook/services/direct_byok_client.dart';
import 'package:clage_cook/services/direct_provider_client.dart';
import 'package:clage_cook/services/direct_settings_store.dart';
import 'package:clage_cook/services/local_conversation_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('Direct BYOK会議を端末内に保存しreasoning modeを固定する', () async {
    final valueStore = MemoryLocalConversationValueStore();
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: valueStore,
    );
    var providerCalls = 0;
    final httpClient = MockClient((request) async {
      providerCalls++;
      final body = jsonDecode(request.body) as Map;
      expect(body['store'], isFalse);
      expect(body['reasoning'], {'effort': 'medium'});
      return http.Response.bytes(
        utf8.encode(
          jsonEncode({
            'status': 'completed',
            'model': 'gpt-test',
            'output': [
              {
                'type': 'message',
                'content': [
                  {'type': 'output_text', 'text': '端末直結の回答'},
                ],
              },
            ],
            'usage': {'input_tokens': 8, 'output_tokens': 12},
          }),
        ),
        200,
      );
    });
    final client = DirectByokClient(
      settings: const DirectSettings(
        chatGptApiKey: 'test-openai-key',
        chatGptModelOverride: 'gpt-test',
      ),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(client: httpClient),
    );

    final plan = await client.planChat(
      message: 'お茶について教えて',
      reasoningMode: 'medium',
      providers: const ['chatgpt'],
    );
    expect(plan.allowed, isTrue);
    expect(plan.billable, isTrue);
    expect(plan.maxLiveCalls, 1);

    final stream = await client.startChat(
      message: 'お茶について教えて',
      reasoningMode: 'medium',
      providers: const ['chatgpt'],
      confirmLiveApi: true,
    );
    final events = await stream.events.toList();
    expect(
      events.map((event) => event.event),
      containsAll(['meta', 'answer', 'synthesis', 'done']),
    );
    expect(providerCalls, 1);

    final saved = await client.conversation(stream.conversationId);
    expect(saved.turns, hasLength(1));
    expect(saved.turns.single.options['reasoning_mode'], 'medium');
    expect(saved.turns.single.answers['chatgpt']?.text, '端末直結の回答');
    expect(saved.turns.single.synthesis.skipped, isTrue);
    expect((await client.conversations()).single.title, 'お茶について教えて');

    final archive = ZipDecoder().decodeBytes(
      await client.exportConversationArchive(stream.conversationId),
    );
    expect(archive.find('conversation.json'), isNotNull);
    expect(archive.find('README.txt'), isNotNull);
    client.close();
  });

  test('transport分類の安全な文言とcode・stageを回答監査へ保存する', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(grokApiKey: 'test-xai-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((_) async {
          calls++;
          throw http.ClientException(
            'Failed host lookup: private.vendor.example secret-detail',
            Uri.parse('https://private.vendor.example/secret-path'),
          );
        }),
      ),
    );

    final stream = await client.startChat(
      message: '接続診断',
      tier: 'high',
      reasoningMode: 'high',
      providers: const ['grok'],
      confirmLiveApi: true,
    );
    final events = await stream.events.toList();
    final answerEvent = events.singleWhere((event) => event.event == 'answer');
    expect(answerEvent.data['error'], contains('接続先名を解決できませんでした'));
    expect(
      answerEvent.data['error'],
      isNot(contains('private.vendor.example')),
    );
    expect(answerEvent.data['error'], isNot(contains('secret-detail')));
    expect(answerEvent.data['error_code'], 'dns');
    expect(answerEvent.data['error_stage'], 'request_transport');
    expect(answerEvent.data['usage_may_be_incomplete'], isTrue);
    expect(answerEvent.data['request_audit'], {
      'http_attempts': 1,
      'retry_count': 0,
      'outcome': 'transport_failure',
      'failure_code': 'dns',
      'failure_stage': 'request_transport',
      'usage_may_be_incomplete': true,
    });

    final saved = await client.conversation(stream.conversationId);
    final answer = saved.turns.single.answers['grok']!;
    expect(answer.error, contains('接続先名を解決できませんでした'));
    expect(answer.requestAudit['failure_code'], 'dns');
    expect(answer.requestAudit['failure_stage'], 'request_transport');
    expect(answer.usageMayBeIncomplete, isTrue);
    expect(calls, 1);
    client.close();
  });

  test('LOW tierをsettings・plan・新規会議・再生成の全経路で保持する', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    final payloads = <Map<String, dynamic>>[];
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((request) async {
          payloads.add(
            Map<String, dynamic>.from(jsonDecode(request.body) as Map),
          );
          return _openAiResponse('LOW回答${++calls}');
        }),
      ),
    );

    final settings = await client.serverSettings();
    expect(
      settings.providers.singleWhere((item) => item.name == 'chatgpt').models,
      {
        'low': 'gpt-5.6-luna',
        'balanced': 'gpt-5.6-terra',
        'high': 'gpt-5.6-sol',
      },
    );

    final plan = await client.planChat(
      message: 'LOWで実行',
      tier: 'low',
      reasoningMode: 'low',
      providers: const ['chatgpt'],
    );
    expect(plan.providers.single.model, 'gpt-5.6-luna');
    expect(plan.maxOutputTokens['max_per_call'], 4096);

    final stream = await client.startChat(
      message: 'LOWで実行',
      tier: 'low',
      reasoningMode: 'low',
      providers: const ['chatgpt'],
      confirmLiveApi: true,
    );
    await stream.events.drain<void>();
    final original = await client.conversation(stream.conversationId);
    expect(original.turns.single.options['tier'], 'low');
    expect(original.turns.single.options['reasoning_mode'], 'low');

    final regenerationPlan = await client.regenerationPlan(
      conversationId: stream.conversationId,
      turnRequestId: original.turns.single.requestId,
      target: 'answer',
      provider: 'chatgpt',
    );
    expect(regenerationPlan.providers.single.model, 'gpt-5.6-luna');
    expect(regenerationPlan.maxOutputTokens['max_per_call'], 4096);

    await client.regenerate(
      conversationId: stream.conversationId,
      turnRequestId: original.turns.single.requestId,
      target: 'answer',
      provider: 'chatgpt',
      confirmLiveApi: true,
      regenerationId: 'low-regeneration-0001',
    );

    expect(calls, 2);
    for (final payload in payloads) {
      expect(payload['model'], 'gpt-5.6-luna');
      expect(payload['max_output_tokens'], 4096);
      expect(payload['reasoning'], {'effort': 'low'});
    }
    client.close();
  });

  test('Direct BYOKは秘密候補をProviderへ送る前にブロックする', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
    );

    final scan = await client.scanPolicy(
      'OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz123456',
    );
    expect(scan.blocked, isTrue);
    expect(scan.redactedText, isNot(contains('abcdefghijklmnopqrstuvwxyz')));
    await expectLater(
      client.startChat(
        message: 'sk-proj-abcdefghijklmnopqrstuvwxyz123456',
        providers: const ['chatgpt'],
        confirmLiveApi: true,
      ),
      throwsA(isA<Exception>()),
    );
    client.close();
  });

  test('AQ形式のGoogle APIキー候補も端末内でブロックする', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    final client = DirectByokClient(
      settings: const DirectSettings(geminiApiKey: 'test-key'),
      conversations: repository,
    );

    final scan = await client.scanPolicy('AQ.abcdefghijklmnopqrstuvwxyz123456');
    expect(scan.blocked, isTrue);
    expect(scan.findings.single.ruleId, 'google_aq_api_key');
    client.close();
  });

  test('保存済みrequest IDの再送はProviderを二重実行しない', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((_) async => _openAiResponse('回答${++calls}')),
      ),
    );

    final first = await client.startChat(
      message: '一度だけ送る',
      providers: const ['chatgpt'],
      confirmLiveApi: true,
      requestId: 'direct-request-0001',
    );
    await first.events.drain<void>();
    await expectLater(
      client.startChat(
        message: '一度だけ送る',
        conversationId: first.conversationId,
        providers: const ['chatgpt'],
        confirmLiveApi: true,
        requestId: 'direct-request-0001',
      ),
      throwsA(isA<Exception>()),
    );

    expect(calls, 1);
    expect(
      (await client.conversation(first.conversationId)).turns,
      hasLength(1),
    );
    client.close();
  });

  test('同時に届いた同一request IDも予約段階で一方だけにする', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((_) async => _openAiResponse('回答${++calls}')),
      ),
    );

    final accepted = client.startChat(
      message: '同時送信',
      providers: const ['chatgpt'],
      confirmLiveApi: true,
      requestId: 'direct-request-0002',
    );
    await expectLater(
      client.startChat(
        message: '同時送信',
        providers: const ['chatgpt'],
        confirmLiveApi: true,
        requestId: 'direct-request-0002',
      ),
      throwsA(isA<Exception>()),
    );
    final stream = await accepted;
    await stream.events.drain<void>();

    expect(calls, 1);
    client.close();
  });

  test('添付本文の秘密候補・NUL・byte上限をProvider送信前に拒否する', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((_) async => _openAiResponse('回答${++calls}')),
      ),
    );
    final draft = await client.createDraftConversation();
    final secret = await client.uploadAttachment(
      conversationId: draft.id,
      name: 'secret.txt',
      bytes: Uint8List.fromList(
        utf8.encode('sk-proj-abcdefghijklmnopqrstuvwxyz123456'),
      ),
    );

    await expectLater(
      client.startChat(
        message: '添付を確認して',
        conversationId: draft.id,
        providers: const ['chatgpt'],
        attachmentIds: [secret.id],
        confirmLiveApi: true,
      ),
      throwsA(isA<Exception>()),
    );
    await expectLater(
      client.uploadAttachment(
        conversationId: draft.id,
        name: 'nul.txt',
        bytes: Uint8List.fromList([65, 0, 66]),
      ),
      throwsA(isA<Exception>()),
    );
    await expectLater(
      client.uploadAttachment(
        conversationId: draft.id,
        name: 'large.txt',
        bytes: Uint8List(512 * 1024 + 1),
      ),
      throwsA(isA<Exception>()),
    );

    expect(calls, 0);
    client.close();
  });

  test('添付のpolicy確認を再生成planと実行で同じ文面から判定する', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((_) async => _openAiResponse('回答${++calls}')),
      ),
    );
    final draft = await client.createDraftConversation();
    final attachment = await client.uploadAttachment(
      conversationId: draft.id,
      name: 'contact.txt',
      bytes: Uint8List.fromList(utf8.encode('連絡先: user@example.com')),
    );
    final stream = await client.startChat(
      message: '添付を確認して',
      conversationId: draft.id,
      providers: const ['chatgpt'],
      attachmentIds: [attachment.id],
      confirmLiveApi: true,
      confirmSensitiveData: true,
    );
    await stream.events.drain<void>();
    final original = await client.conversation(draft.id);
    final turnId = original.turns.single.requestId;

    final plan = await client.regenerationPlan(
      conversationId: draft.id,
      turnRequestId: turnId,
      target: 'answer',
      provider: 'chatgpt',
    );
    expect(plan.policy.action, 'confirm');
    expect(
      plan.policy.findings.map((finding) => finding.label),
      contains('メールアドレスらしい文字列'),
    );
    await expectLater(
      client.regenerate(
        conversationId: draft.id,
        turnRequestId: turnId,
        target: 'answer',
        provider: 'chatgpt',
        confirmLiveApi: true,
        regenerationId: 'attachment-regeneration-0001',
      ),
      throwsA(isA<Exception>()),
    );
    expect(calls, 1);

    await client.regenerate(
      conversationId: draft.id,
      turnRequestId: turnId,
      target: 'answer',
      provider: 'chatgpt',
      confirmLiveApi: true,
      confirmSensitiveData: true,
      regenerationId: 'attachment-regeneration-0002',
    );
    expect(calls, 2);
    client.close();
  });

  test('履歴込み初回inputが1 MiB超ならplanと実行直前の双方で拒否する', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    final created = await repository.create(
      firstMessage: '大きい履歴',
      conversationId: 'large-history',
    );
    await repository.save({
      ...created.value,
      'turns': [
        {
          'request_id': 'old-request',
          'message': '以前の質問',
          'clean_message': '以前の質問',
          'status': 'completed',
          'answers': {
            'chatgpt': {
              'ok': true,
              'text': List.filled(1024 * 1024, 'x').join(),
            },
          },
          'synthesis': {'ok': false, 'text': ''},
        },
      ],
    }, expectedStorageRevision: created.storageRevision);
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((_) async => _openAiResponse('回答${++calls}')),
      ),
    );

    final plan = await client.planChat(
      message: '続き',
      conversationId: created.id,
      providers: const ['chatgpt'],
    );
    expect(plan.allowed, isFalse);
    expect(plan.blockReasons, contains('input_byte_limit_exceeded'));
    expect(plan.inputEnvelope.history, greaterThan(1024 * 1024));
    await expectLater(
      client.startChat(
        message: '続き',
        conversationId: created.id,
        providers: const ['chatgpt'],
        confirmLiveApi: true,
      ),
      throwsA(isA<Exception>()),
    );
    expect(calls, 0);
    client.close();
  });

  test('同じregeneration IDは保存済み結果を無課金再生する', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var calls = 0;
    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () => DirectProviderClient(
        client: MockClient((_) async => _openAiResponse('回答${++calls}')),
      ),
    );
    final stream = await client.startChat(
      message: '再生成対象',
      providers: const ['chatgpt'],
      confirmLiveApi: true,
    );
    await stream.events.drain<void>();
    final original = await client.conversation(stream.conversationId);
    final turnId = original.turns.single.requestId;

    final regenerated = await client.regenerate(
      conversationId: stream.conversationId,
      turnRequestId: turnId,
      target: 'answer',
      provider: 'chatgpt',
      confirmLiveApi: true,
      regenerationId: 'regeneration-fixed-0001',
    );
    final replayed = await client.regenerate(
      conversationId: stream.conversationId,
      turnRequestId: turnId,
      target: 'answer',
      provider: 'chatgpt',
      confirmLiveApi: true,
      regenerationId: 'regeneration-fixed-0001',
    );

    expect(calls, 2);
    expect(regenerated.turns.single.answers['chatgpt']?.text, '回答2');
    expect(replayed.turns.single.answers['chatgpt']?.text, '回答2');
    expect(
      replayed.turns.single.attempts.map((attempt) => attempt.attemptId),
      contains('regeneration-fixed-0001'),
    );
    client.close();
  });

  test('再生成中のrename競合後もProviderを再実行せず課金済み結果を保存する', () async {
    final repository = SharedPreferencesLocalConversationRepository(
      namespace: LocalConversationNamespace.directByok,
      valueStore: MemoryLocalConversationValueStore(),
    );
    var calls = 0;
    final regenerationStarted = Completer<void>();
    final regenerationResponse = Completer<http.Response>();
    Future<http.Response> handler(http.Request _) async {
      calls++;
      if (calls == 1) return _openAiResponse('初回回答');
      regenerationStarted.complete();
      return regenerationResponse.future;
    }

    final client = DirectByokClient(
      settings: const DirectSettings(chatGptApiKey: 'test-key'),
      conversations: repository,
      providerClientFactory: () =>
          DirectProviderClient(client: MockClient(handler)),
    );
    final stream = await client.startChat(
      message: '競合対象',
      providers: const ['chatgpt'],
      confirmLiveApi: true,
    );
    await stream.events.drain<void>();
    final turnId = (await client.conversation(
      stream.conversationId,
    )).turns.single.requestId;

    final future = client.regenerate(
      conversationId: stream.conversationId,
      turnRequestId: turnId,
      target: 'answer',
      provider: 'chatgpt',
      confirmLiveApi: true,
      regenerationId: 'regeneration-conflict-0001',
    );
    await regenerationStarted.future;
    await client.renameConversation(stream.conversationId, '競合後の名前');
    regenerationResponse.complete(_openAiResponse('競合後の再生成回答'));
    final saved = await future;

    expect(calls, 2);
    expect(saved.title, '競合後の名前');
    expect(saved.turns.single.answers['chatgpt']?.text, '競合後の再生成回答');
    client.close();
  });
}

http.Response _openAiResponse(String text) => http.Response.bytes(
  utf8.encode(
    jsonEncode({
      'status': 'completed',
      'model': 'gpt-test',
      'output': [
        {
          'type': 'message',
          'content': [
            {'type': 'output_text', 'text': text},
          ],
        },
      ],
      'usage': {'input_tokens': 3, 'output_tokens': 5, 'total_tokens': 8},
    }),
  ),
  200,
);
