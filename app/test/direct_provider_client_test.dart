import 'dart:async';
import 'dart:convert';

import 'package:clage_cook/services/direct_provider_client.dart';
import 'package:clage_cook/services/direct_settings_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

void main() {
  test('LOWの既定modelと出力capはreference backendと一致する', () {
    expect(DirectProviderClient.normalizeTier('low'), 'low');
    expect(DirectProviderClient.normalizeTier('unexpected'), 'balanced');
    expect(
      DirectProviderClient.modelFor(DirectProvider.claude, 'low'),
      'claude-haiku-4-5-20251001',
    );
    expect(
      DirectProviderClient.modelFor(DirectProvider.chatgpt, 'low'),
      'gpt-5.6-luna',
    );
    expect(
      DirectProviderClient.modelFor(DirectProvider.gemini, 'low'),
      'gemini-3.1-flash-lite',
    );
    expect(
      DirectProviderClient.modelFor(DirectProvider.grok, 'low'),
      'grok-4.3',
    );
    expect(
      DirectProviderClient.maxOutputTokensFor(DirectProvider.claude, 'low'),
      4096,
    );
    expect(
      DirectProviderClient.maxOutputTokensFor(DirectProvider.chatgpt, 'low'),
      4096,
    );
    expect(
      DirectProviderClient.maxOutputTokensFor(DirectProvider.gemini, 'low'),
      8192,
    );
    expect(
      DirectProviderClient.maxOutputTokensFor(DirectProvider.grok, 'low'),
      4096,
    );
  });

  test('AUTOは質問分類せずmodel familyの固定推奨effortへ解決する', () {
    String effective(DirectProvider provider, String model) =>
        DirectProviderClient.reasoningAuditFor(
              provider,
              model,
              ReasoningMode.auto,
            )['effective']
            as String;

    expect(effective(DirectProvider.claude, 'claude-sonnet-5'), 'high');
    expect(effective(DirectProvider.chatgpt, 'gpt-5.6-terra'), 'medium');
    expect(effective(DirectProvider.gemini, 'gemini-3.5-flash'), 'medium');
    expect(effective(DirectProvider.grok, 'grok-4.20-beta'), 'high');
    expect(effective(DirectProvider.grok, 'grok-4.3'), 'medium');
    final unsupported = DirectProviderClient.reasoningAuditFor(
      DirectProvider.claude,
      'claude-haiku-4-5-20251001',
      ReasoningMode.auto,
    );
    expect(unsupported['effective'], 'provider_default');
    expect(unsupported['source'], 'model_unsupported');
  });

  test('明示LOW effortをProvider payloadとauditの双方へ固定する', () async {
    late Map<String, dynamic> payload;
    final client = DirectProviderClient(
      client: MockClient((request) async {
        payload = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'status': 'completed',
              'model': 'gpt-5.6-luna',
              'output': [
                {
                  'type': 'message',
                  'content': [
                    {'type': 'output_text', 'text': '低effort回答'},
                  ],
                },
              ],
              'usage': {'input_tokens': 1, 'output_tokens': 2},
            }),
          ),
          200,
        );
      }),
    );

    final result = await client.complete(
      const DirectProviderRequest(
        provider: DirectProvider.chatgpt,
        apiKey: 'test-key',
        model: 'gpt-5.6-luna',
        prompt: '質問',
        system: '',
        reasoningMode: ReasoningMode.low,
        maxOutputTokens: 4096,
      ),
    );

    expect(payload['reasoning'], {'effort': 'low'});
    expect(result['reasoning'], {
      'requested': 'low',
      'effective': 'low',
      'source': 'explicit',
      'pinned': true,
      'policy_version': 1,
    });
  });

  test('effort非対応Claudeはpayloadを省略しauditをprovider defaultにする', () async {
    late Map<String, dynamic> payload;
    final client = DirectProviderClient(
      client: MockClient((request) async {
        payload = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'model': 'claude-haiku-4-5-20251001',
              'stop_reason': 'end_turn',
              'content': [
                {'type': 'text', 'text': 'Haiku回答'},
              ],
              'usage': {'input_tokens': 1, 'output_tokens': 2},
            }),
          ),
          200,
        );
      }),
    );

    final result = await client.complete(
      const DirectProviderRequest(
        provider: DirectProvider.claude,
        apiKey: 'test-key',
        model: 'claude-haiku-4-5-20251001',
        prompt: '質問',
        system: '',
        reasoningMode: ReasoningMode.high,
        maxOutputTokens: 4096,
      ),
    );

    expect(payload, isNot(contains('output_config')));
    expect(result['reasoning'], {
      'requested': 'high',
      'effective': 'provider_default',
      'source': 'model_unsupported',
      'pinned': true,
      'policy_version': 1,
    });
  });

  test('OpenAI Responses APIへstore=falseと独立reasoning modeを送る', () async {
    late Map<String, dynamic> payload;
    final client = DirectProviderClient(
      client: MockClient((request) async {
        expect(request.url.toString(), 'https://api.openai.com/v1/responses');
        expect(request.headers['authorization'], 'Bearer test-openai-key');
        payload = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        return http.Response(
          jsonEncode({
            'status': 'completed',
            'model': 'gpt-test',
            'output': [
              {
                'type': 'message',
                'content': [
                  {'type': 'output_text', 'text': '独立した回答'},
                ],
              },
            ],
            'usage': {
              'input_tokens': 10,
              'output_tokens': 20,
              'total_tokens': 30,
            },
          }),
          200,
          headers: {'content-type': 'application/json'},
        );
      }),
    );

    final result = await client.complete(
      const DirectProviderRequest(
        provider: DirectProvider.chatgpt,
        apiKey: 'test-openai-key',
        model: 'gpt-test',
        prompt: '質問',
        system: '中立に回答',
        reasoningMode: ReasoningMode.high,
        maxOutputTokens: 16384,
      ),
    );

    expect(payload['store'], isFalse);
    expect(payload['max_output_tokens'], 16384);
    expect(payload['reasoning'], {'effort': 'high'});
    expect(payload['instructions'], '中立に回答');
    expect(result['ok'], isTrue);
    expect(result['text'], '独立した回答');
    expect(result['reasoning']['requested'], 'high');
    expect(result['request_audit']['http_attempts'], 1);
  });

  test('Claudeのmax_tokens終了は途中回答として保持し統合成功扱いにしない', () async {
    final client = DirectProviderClient(
      client: MockClient((request) async {
        final payload = jsonDecode(request.body) as Map;
        expect(payload['max_tokens'], 8192);
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'model': 'claude-test',
              'stop_reason': 'max_tokens',
              'content': [
                {'type': 'text', 'text': '途中までの回答'},
              ],
              'usage': {'input_tokens': 4, 'output_tokens': 8192},
            }),
          ),
          200,
        );
      }),
    );

    final result = await client.complete(
      const DirectProviderRequest(
        provider: DirectProvider.claude,
        apiKey: 'test-anthropic-key',
        model: 'claude-test',
        prompt: '質問',
        system: '',
        reasoningMode: ReasoningMode.auto,
        maxOutputTokens: 8192,
      ),
    );

    expect(result['ok'], isFalse);
    expect(result['partial'], isTrue);
    expect(result['text'], '途中までの回答');
    expect(result['completion_status'], 'incomplete');
    expect(result['incomplete_reason'], 'max_tokens');
  });

  test('HTTP失敗を自動再試行せず生の応答本文も反射しない', () async {
    var calls = 0;
    final client = DirectProviderClient(
      client: MockClient((request) async {
        calls++;
        return http.Response('secret vendor diagnostics', 500);
      }),
    );

    await expectLater(
      client.complete(
        const DirectProviderRequest(
          provider: DirectProvider.grok,
          apiKey: 'test-xai-key',
          model: 'grok-test',
          prompt: '質問',
          system: '',
          reasoningMode: ReasoningMode.medium,
          maxOutputTokens: 8192,
        ),
      ),
      throwsA(
        isA<DirectProviderException>()
            .having((error) => error.statusCode, 'statusCode', 500)
            .having((error) => error.code, 'code', 'http_status')
            .having((error) => error.stage, 'stage', 'response_status')
            .having(
              (error) => error.message,
              'message',
              isNot(contains('secret vendor diagnostics')),
            ),
      ),
    );
    expect(calls, 1);
  });

  test('transport失敗をURIや生例外本文なしのcodeとstageへ分類する', () async {
    final cases = <({String detail, String code, String stage})>[
      (
        detail: 'Failed host lookup: private.vendor.example',
        code: 'dns',
        stage: 'request_transport',
      ),
      (
        detail: 'HandshakeException: CERTIFICATE_VERIFY_FAILED secret-cert',
        code: 'tls',
        stage: 'request_transport',
      ),
      (
        detail: 'Connection refused by private.vendor.example',
        code: 'connection_refused',
        stage: 'request_transport',
      ),
      (
        detail: 'Connection reset by peer private.vendor.example',
        code: 'connection_reset',
        stage: 'response_wait',
      ),
      (
        detail: 'HTTP request failed. Client is already closed.',
        code: 'client_closed',
        stage: 'request_transport',
      ),
    ];

    for (final testCase in cases) {
      var calls = 0;
      final client = DirectProviderClient(
        client: MockClient((_) async {
          calls++;
          throw http.ClientException(
            testCase.detail,
            Uri.parse('https://private.vendor.example/secret-path'),
          );
        }),
      );
      DirectProviderException? caught;
      try {
        await client.complete(_grokRequest());
      } on DirectProviderException catch (error) {
        caught = error;
      }

      expect(caught, isNotNull);
      expect(caught!.code, testCase.code);
      expect(caught.stage, testCase.stage);
      expect(caught.httpAttempts, 1);
      expect(caught.usageMayBeIncomplete, isTrue);
      expect(caught.requestAudit, {
        'http_attempts': 1,
        'retry_count': 0,
        'outcome': 'transport_failure',
        'failure_code': testCase.code,
        'failure_stage': testCase.stage,
        'usage_may_be_incomplete': true,
      });
      expect(caught.toString(), isNot(contains('private.vendor.example')));
      expect(caught.toString(), isNot(contains('secret')));
      expect(calls, 1);
      client.close();
    }
  });

  test('未知のtransport例外も内容を反射せずtransport_unknownに閉じる', () async {
    final client = DirectProviderClient(
      client: MockClient(
        (_) async => throw StateError(
          'secret-token at https://private.vendor.example/path',
        ),
      ),
    );
    DirectProviderException? caught;
    try {
      await client.complete(_grokRequest());
    } on DirectProviderException catch (error) {
      caught = error;
    }

    expect(caught?.code, 'transport_unknown');
    expect(caught?.stage, 'request_transport');
    expect(caught.toString(), isNot(contains('secret-token')));
    expect(caught.toString(), isNot(contains('private.vendor.example')));
    client.close();
  });

  test('明示timeoutは従来どおり優先され、自動再試行しない', () async {
    var calls = 0;
    final response = Completer<http.Response>();
    final client = DirectProviderClient(
      timeout: const Duration(milliseconds: 5),
      client: MockClient((_) {
        calls++;
        return response.future;
      }),
    );
    DirectProviderException? caught;
    try {
      await client.complete(_grokRequest());
    } on DirectProviderException catch (error) {
      caught = error;
    }

    expect(caught?.message, 'Grokへの接続がタイムアウトしました。');
    expect(caught?.code, 'timeout');
    expect(caught?.stage, 'response_wait');
    expect(caught?.requestAudit['outcome'], 'timeout');
    expect(calls, 1);
    client.close();
  });

  test('provider・tier・effort別timeoutはGrok HIGHを15分まで有限に待つ', () {
    final client = DirectProviderClient(
      client: MockClient((_) async => http.Response('', 500)),
    );
    final grokLow = client.effectiveTimeoutFor(
      _grokRequest(
        model: 'grok-4.3',
        tier: 'low',
        reasoningMode: ReasoningMode.low,
        maxOutputTokens: 4096,
      ),
    );
    final grokBalanced = client.effectiveTimeoutFor(
      _grokRequest(
        model: 'grok-4.3',
        tier: 'balanced',
        reasoningMode: ReasoningMode.medium,
        maxOutputTokens: 8192,
      ),
    );
    final grokHigh = client.effectiveTimeoutFor(
      _grokRequest(
        tier: 'high',
        reasoningMode: ReasoningMode.high,
        maxOutputTokens: 16384,
      ),
    );
    final grokHighWithWeb = client.effectiveTimeoutFor(
      _grokRequest(
        tier: 'high',
        reasoningMode: ReasoningMode.high,
        maxOutputTokens: 16384,
        webSearch: true,
      ),
    );

    expect(grokLow, const Duration(minutes: 4));
    expect(grokBalanced, const Duration(minutes: 8));
    expect(grokHigh, const Duration(minutes: 15));
    expect(grokHighWithWeb, const Duration(minutes: 15));
    expect(grokLow < grokBalanced, isTrue);
    expect(grokBalanced < grokHigh, isTrue);
    client.close();
  });

  test('usage欠損は完了応答でも請求情報不完全として記録する', () async {
    final client = DirectProviderClient(
      client: MockClient(
        (_) async => http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'status': 'completed',
              'output': [
                {
                  'type': 'message',
                  'content': [
                    {'type': 'output_text', 'text': '本文'},
                  ],
                },
              ],
            }),
          ),
          200,
        ),
      ),
    );

    final result = await client.complete(
      const DirectProviderRequest(
        provider: DirectProvider.chatgpt,
        apiKey: 'test-key',
        model: 'gpt-test',
        prompt: '質問',
        system: '',
        reasoningMode: ReasoningMode.auto,
        maxOutputTokens: 8192,
      ),
    );

    expect(result['ok'], isTrue);
    expect(result['usage_may_be_incomplete'], isTrue);
    expect(result['request_audit']['usage_may_be_incomplete'], isTrue);
  });

  test('Claude refusal本文を回答や例外へ反射しない', () async {
    final client = DirectProviderClient(
      client: MockClient(
        (_) async => http.Response(
          jsonEncode({
            'stop_reason': 'refusal',
            'content': [
              {'type': 'text', 'text': 'vendor refusal detail'},
            ],
          }),
          200,
        ),
      ),
    );

    await expectLater(
      client.complete(
        const DirectProviderRequest(
          provider: DirectProvider.claude,
          apiKey: 'test-key',
          model: 'claude-test',
          prompt: '質問',
          system: '',
          reasoningMode: ReasoningMode.auto,
          maxOutputTokens: 8192,
        ),
      ),
      throwsA(
        isA<DirectProviderException>().having(
          (error) => error.message,
          'message',
          isNot(contains('vendor refusal detail')),
        ),
      ),
    );
  });

  test('Gemini stable Interactions APIへstateless payloadを送る', () async {
    late Map<String, dynamic> payload;
    final client = DirectProviderClient(
      client: MockClient((request) async {
        expect(
          request.url.toString(),
          'https://generativelanguage.googleapis.com/v1/interactions',
        );
        expect(request.headers['x-goog-api-key'], 'gemini-test-key');
        payload = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'status': 'completed',
              'model': 'gemini-3.5-flash',
              'steps': [
                {
                  'type': 'model_output',
                  'content': [
                    {'type': 'text', 'text': 'Gemini回答'},
                  ],
                },
              ],
              'usage': {'total_input_tokens': 2, 'total_output_tokens': 3},
            }),
          ),
          200,
        );
      }),
    );

    final result = await client.complete(
      const DirectProviderRequest(
        provider: DirectProvider.gemini,
        apiKey: 'gemini-test-key',
        model: 'gemini-3.5-flash',
        prompt: '質問',
        system: 'システム',
        reasoningMode: ReasoningMode.medium,
        maxOutputTokens: 16384,
        webSearch: true,
      ),
    );

    expect(payload['store'], isFalse);
    expect(payload['system_instruction'], 'システム');
    expect(payload['generation_config'], {
      'max_output_tokens': 16384,
      'thinking_level': 'medium',
    });
    expect(payload['tools'], [
      {'type': 'google_search'},
    ]);
    expect(result['text'], 'Gemini回答');
  });

  test('xAI Responses APIはstore=false・検索turn上限・cache keyを送る', () async {
    late Map<String, dynamic> payload;
    final client = DirectProviderClient(
      client: MockClient((request) async {
        expect(request.url.toString(), 'https://api.x.ai/v1/responses');
        payload = Map<String, dynamic>.from(jsonDecode(request.body) as Map);
        return http.Response.bytes(
          utf8.encode(
            jsonEncode({
              'status': 'completed',
              'model': 'grok-4.5',
              'output': [
                {
                  'type': 'message',
                  'content': [
                    {'type': 'output_text', 'text': 'Grok回答'},
                  ],
                },
              ],
              'usage': {'input_tokens': 2, 'output_tokens': 3},
            }),
          ),
          200,
        );
      }),
    );

    await client.complete(
      const DirectProviderRequest(
        provider: DirectProvider.grok,
        apiKey: 'xai-test-key',
        model: 'grok-4.5',
        prompt: '質問',
        system: 'システム',
        reasoningMode: ReasoningMode.high,
        maxOutputTokens: 16384,
        webSearch: true,
        promptCacheKey: 'conversation-cache-key',
      ),
    );

    expect(payload['store'], isFalse);
    expect(payload['reasoning'], {'effort': 'high'});
    expect(payload['prompt_cache_key'], 'conversation-cache-key');
    expect(payload['max_turns'], 3);
    expect(payload['input'], [
      {'role': 'system', 'content': 'システム'},
      {'role': 'user', 'content': '質問'},
    ]);
  });
}

DirectProviderRequest _grokRequest({
  String model = 'grok-4.5',
  String? tier,
  ReasoningMode reasoningMode = ReasoningMode.high,
  int maxOutputTokens = 16384,
  bool webSearch = false,
}) => DirectProviderRequest(
  provider: DirectProvider.grok,
  apiKey: 'test-xai-key',
  model: model,
  prompt: '質問',
  system: '',
  reasoningMode: reasoningMode,
  maxOutputTokens: maxOutputTokens,
  webSearch: webSearch,
  tier: tier,
);
