import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'direct_settings_store.dart';

/// Direct BYOKで1社へ送る、1回だけの生成要求。
/// 同一要求の自動再送は二重課金になり得るため、この層では再試行しない。
class DirectProviderRequest {
  const DirectProviderRequest({
    required this.provider,
    required this.apiKey,
    required this.model,
    required this.prompt,
    required this.system,
    required this.reasoningMode,
    required this.maxOutputTokens,
    this.webSearch = false,
    this.promptCacheKey = '',
    this.tier,
  });

  final DirectProvider provider;
  final String apiKey;
  final String model;
  final String prompt;
  final String system;
  final ReasoningMode reasoningMode;
  final int maxOutputTokens;
  final bool webSearch;
  final String promptCacheKey;

  /// 呼出元が指定できない場合は[maxOutputTokens]から既定tierを逆引きする。
  final String? tier;
}

class DirectProviderException implements Exception {
  const DirectProviderException(
    this.message, {
    this.statusCode,
    this.code = 'provider_error',
    this.stage = 'provider',
    this.httpAttempts = 0,
    this.usageMayBeIncomplete = false,
  });

  final String message;
  final int? statusCode;
  final String code;
  final String stage;
  final int httpAttempts;
  final bool usageMayBeIncomplete;

  /// URI、host、生の例外本文を含めずに永続化できる監査情報。
  Map<String, dynamic> get requestAudit => {
    'http_attempts': httpAttempts,
    'retry_count': 0,
    'outcome': switch (code) {
      'timeout' => 'timeout',
      'http_status' => 'http_error',
      'api_key_missing' || 'request_encoding' => 'request_not_sent',
      'provider_refusal' => 'refused',
      'invalid_response' || 'empty_response' => 'invalid_response',
      'provider_error' => 'provider_failure',
      _ => 'transport_failure',
    },
    'failure_code': code,
    'failure_stage': stage,
    if (statusCode != null) 'final_http_status': statusCode,
    'usage_may_be_incomplete': usageMayBeIncomplete,
  };

  @override
  String toString() => message;
}

/// 4社の公式HTTPS APIを端末から直接呼ぶ薄いクライアント。
class DirectProviderClient {
  DirectProviderClient({http.Client? client, this.timeout})
    : _client = client ?? http.Client();

  final http.Client _client;

  /// テストや埋め込み用途の明示上書き。nullなら要求内容ごとの有限policyを使う。
  final Duration? timeout;

  static const labels = <DirectProvider, String>{
    DirectProvider.claude: 'Claude',
    DirectProvider.chatgpt: 'ChatGPT',
    DirectProvider.gemini: 'Gemini',
    DirectProvider.grok: 'Grok',
  };

  /// 設定画面で、質問と添付の直接送信先を利用者へ明示するためのhost。
  static const endpointHosts = <DirectProvider, String>{
    DirectProvider.claude: 'api.anthropic.com',
    DirectProvider.chatgpt: 'api.openai.com',
    DirectProvider.gemini: 'generativelanguage.googleapis.com',
    DirectProvider.grok: 'api.x.ai',
  };

  static const defaultModels = <DirectProvider, Map<String, String>>{
    DirectProvider.claude: {
      'low': 'claude-haiku-4-5-20251001',
      'balanced': 'claude-sonnet-5',
      'high': 'claude-opus-4-8',
    },
    DirectProvider.chatgpt: {
      'low': 'gpt-5.6-luna',
      'balanced': 'gpt-5.6-terra',
      'high': 'gpt-5.6-sol',
    },
    DirectProvider.gemini: {
      'low': 'gemini-3.1-flash-lite',
      'balanced': 'gemini-3.5-flash',
      'high': 'gemini-3.5-flash',
    },
    DirectProvider.grok: {
      'low': 'grok-4.3',
      'balanced': 'grok-4.3',
      'high': 'grok-4.5',
    },
  };

  static const outputCaps = <DirectProvider, Map<String, int>>{
    DirectProvider.claude: {'low': 4096, 'balanced': 8192, 'high': 16384},
    DirectProvider.chatgpt: {'low': 4096, 'balanced': 8192, 'high': 16384},
    DirectProvider.gemini: {'low': 8192, 'balanced': 16384, 'high': 32768},
    DirectProvider.grok: {'low': 4096, 'balanced': 8192, 'high': 16384},
  };

  /// APIへ渡せるtierを3段階へ正規化する。不明値は安全な標準値へ戻す。
  static String normalizeTier(String tier) =>
      const {'low', 'balanced', 'high'}.contains(tier) ? tier : 'balanced';

  static String modelFor(
    DirectProvider provider,
    String tier, {
    String override = '',
  }) {
    final custom = override.trim();
    if (custom.isNotEmpty) return custom;
    return defaultModels[provider]![normalizeTier(tier)]!;
  }

  static int maxOutputTokensFor(DirectProvider provider, String tier) =>
      outputCaps[provider]![normalizeTier(tier)]!;

  /// Provider・品質tier・実効effort・Web検索を基にした応答待ち上限。
  ///
  /// xAIの高effortは長時間化し得るため最大15分を確保する。一方で、どの
  /// 組み合わせも無制限には待たない。コンストラクタの明示timeoutがあれば
  /// そちらを優先する。
  Duration effectiveTimeoutFor(DirectProviderRequest request) {
    final overridden = timeout;
    if (overridden != null) return overridden;

    final tier = _tierForRequest(request);
    final effort = _resolveReasoning(
      request.provider,
      request.model,
      request.reasoningMode,
    ).effective;
    var seconds = switch ((request.provider, tier)) {
      (DirectProvider.claude, 'low') => 180,
      (DirectProvider.claude, 'balanced') => 300,
      (DirectProvider.claude, _) => 480,
      (DirectProvider.chatgpt, 'low') => 180,
      (DirectProvider.chatgpt, 'balanced') => 360,
      (DirectProvider.chatgpt, _) => 600,
      (DirectProvider.gemini, 'low') => 180,
      (DirectProvider.gemini, 'balanced') => 360,
      (DirectProvider.gemini, _) => 480,
      (DirectProvider.grok, 'low') => 240,
      (DirectProvider.grok, 'balanced') => 480,
      (DirectProvider.grok, _) => 720,
    };
    if (effort == 'medium') {
      seconds = switch (request.provider) {
        DirectProvider.claude => _atLeast(seconds, 300),
        DirectProvider.chatgpt => _atLeast(seconds, 360),
        DirectProvider.gemini => _atLeast(seconds, 360),
        DirectProvider.grok => _atLeast(seconds, 480),
      };
    } else if (effort == 'high') {
      seconds = switch (request.provider) {
        DirectProvider.claude => _atLeast(seconds, 600),
        DirectProvider.chatgpt => _atLeast(seconds, 720),
        DirectProvider.gemini => _atLeast(seconds, 600),
        DirectProvider.grok => 900,
      };
    }
    if (request.webSearch) seconds += 120;
    return Duration(seconds: seconds.clamp(120, 900));
  }

  /// plan表示と実API呼び出しで同じ推論解決結果を使うための公開audit。
  static Map<String, dynamic> reasoningAuditFor(
    DirectProvider provider,
    String model,
    ReasoningMode requested,
  ) => Map.unmodifiable(
    _resolveReasoning(provider, model, requested).toJson(requested),
  );

  Future<Map<String, dynamic>> complete(
    DirectProviderRequest request, {
    int round = 1,
  }) async {
    if (request.apiKey.trim().isEmpty) {
      throw DirectProviderException(
        '${labels[request.provider]}のAPIキーが未設定です。',
        code: 'api_key_missing',
        stage: 'request_validation',
      );
    }
    final started = DateTime.now();
    final reasoning = _resolveReasoning(
      request.provider,
      request.model,
      request.reasoningMode,
    );
    late final String body;
    try {
      body = jsonEncode(_payload(request, reasoning.effective));
    } catch (_) {
      throw DirectProviderException(
        '${labels[request.provider]}へのAPI要求を安全に作成できませんでした。',
        code: 'request_encoding',
        stage: 'request_encoding',
      );
    }
    late final http.Response response;
    try {
      response = await _client
          .post(
            _endpoint(request.provider),
            headers: _headers(request.provider, request.apiKey),
            body: body,
          )
          .timeout(
            effectiveTimeoutFor(request),
            onTimeout: () => throw DirectProviderException(
              '${labels[request.provider]}への接続がタイムアウトしました。',
              code: 'timeout',
              stage: 'response_wait',
              httpAttempts: 1,
              usageMayBeIncomplete: true,
            ),
          );
    } on DirectProviderException {
      rethrow;
    } catch (error) {
      final failure = _classifyTransportFailure(error);
      throw DirectProviderException(
        _safeTransportMessage(request.provider, failure.code),
        code: failure.code,
        stage: failure.stage,
        httpAttempts: 1,
        usageMayBeIncomplete: true,
      );
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw DirectProviderException(
        _safeHttpError(request.provider, response.statusCode),
        statusCode: response.statusCode,
        code: 'http_status',
        stage: 'response_status',
        httpAttempts: 1,
        usageMayBeIncomplete: true,
      );
    }
    final data = _decodeObject(response.bodyBytes, request.provider);
    final parsed = _parse(request.provider, data);
    final text = parsed.text.trim();
    final completed = parsed.completionStatus == 'completed';
    if (text.isEmpty && completed) {
      throw DirectProviderException(
        '${labels[request.provider]}は表示可能な回答本文を返しませんでした。',
        code: 'empty_response',
        stage: 'response_parse',
        httpAttempts: 1,
        usageMayBeIncomplete: true,
      );
    }
    final usage = _normalizedUsage(data['usage']);
    final usageMayBeIncomplete = !completed || usage.isEmpty;
    final elapsed = DateTime.now().difference(started).inMilliseconds / 1000;
    return {
      'source': request.provider.name,
      // 途中回答は表示・保存するが、統合の根拠には混ぜない。
      'ok': completed && text.isNotEmpty,
      'text': text,
      'error': completed || text.isNotEmpty ? '' : '回答を完了できませんでした',
      'model': data['model']?.toString() ?? request.model,
      'elapsed_sec': double.parse(elapsed.toStringAsFixed(3)),
      'usage': usage,
      'finish_reason': parsed.finishReason,
      'completion_status': parsed.completionStatus,
      'partial': !completed && text.isNotEmpty,
      if (parsed.incompleteReason != null)
        'incomplete_reason': parsed.incompleteReason,
      'usage_may_be_incomplete': usageMayBeIncomplete,
      'request_audit': {
        'http_attempts': 1,
        'retry_count': 0,
        'outcome': 'response_received',
        'final_http_status': response.statusCode,
        'usage_may_be_incomplete': usageMayBeIncomplete,
      },
      'reasoning': reasoning.toJson(request.reasoningMode),
      'max_output_tokens': request.maxOutputTokens,
      'citations': _citations(request.provider, data),
      'web_search_requested': request.webSearch,
      'mock': false,
      'round': round,
    };
  }

  static Uri _endpoint(DirectProvider provider) => switch (provider) {
    DirectProvider.claude => Uri.https('api.anthropic.com', '/v1/messages'),
    DirectProvider.chatgpt => Uri.https('api.openai.com', '/v1/responses'),
    DirectProvider.gemini => Uri.https(
      'generativelanguage.googleapis.com',
      '/v1/interactions',
    ),
    DirectProvider.grok => Uri.https('api.x.ai', '/v1/responses'),
  };

  static Map<String, String> _headers(DirectProvider provider, String apiKey) =>
      {
        'content-type': 'application/json',
        if (provider == DirectProvider.claude) ...{
          'x-api-key': apiKey.trim(),
          'anthropic-version': '2023-06-01',
        } else if (provider == DirectProvider.gemini)
          'x-goog-api-key': apiKey.trim()
        else
          'authorization': 'Bearer ${apiKey.trim()}',
      };

  static Map<String, dynamic> _payload(
    DirectProviderRequest request,
    String? effort,
  ) => switch (request.provider) {
    DirectProvider.claude => _anthropicPayload(request, effort),
    DirectProvider.chatgpt => _openAiPayload(request, effort),
    DirectProvider.gemini => _geminiPayload(request, effort),
    DirectProvider.grok => _xAiPayload(request, effort),
  };

  static Map<String, dynamic> _anthropicPayload(
    DirectProviderRequest request,
    String? effort,
  ) {
    final model = request.model.toLowerCase();
    final adaptive = [
      'claude-opus-4-6',
      'claude-opus-4-7',
      'claude-opus-4-8',
      'claude-sonnet-4-6',
      'claude-sonnet-5',
    ].any(model.startsWith);
    final dynamicWebSearch = [
      'claude-fable-5',
      'claude-mythos-5',
      'claude-mythos-preview',
      'claude-opus-4-6',
      'claude-opus-4-7',
      'claude-opus-4-8',
      'claude-sonnet-4-6',
      'claude-sonnet-5',
    ].any(model.startsWith);
    return {
      'model': request.model,
      'max_tokens': request.maxOutputTokens,
      'messages': [
        {'role': 'user', 'content': request.prompt},
      ],
      if (request.system.isNotEmpty) 'system': request.system,
      if (effort != null) 'output_config': {'effort': effort},
      if (adaptive) 'thinking': {'type': 'adaptive'},
      if (request.webSearch)
        'tools': [
          {
            'type': dynamicWebSearch
                ? 'web_search_20260318'
                : 'web_search_20250305',
            'name': 'web_search',
            'max_uses': 3,
          },
        ],
    };
  }

  static Map<String, dynamic> _openAiPayload(
    DirectProviderRequest request,
    String? effort,
  ) => {
    'model': request.model,
    'input': request.prompt,
    'store': false,
    'max_output_tokens': request.maxOutputTokens,
    if (request.system.isNotEmpty) 'instructions': request.system,
    if (effort != null) 'reasoning': {'effort': effort},
    if (request.webSearch) ...{
      'tools': [
        {'type': 'web_search', 'search_context_size': 'medium'},
      ],
      'tool_choice': 'auto',
    },
  };

  static Map<String, dynamic> _geminiPayload(
    DirectProviderRequest request,
    String? effort,
  ) => {
    'model': request.model,
    'input': request.prompt,
    'store': false,
    'generation_config': {
      'max_output_tokens': request.maxOutputTokens,
      'thinking_level': ?effort,
    },
    if (request.system.isNotEmpty) 'system_instruction': request.system,
    if (request.webSearch)
      'tools': [
        {'type': 'google_search'},
      ],
  };

  static Map<String, dynamic> _xAiPayload(
    DirectProviderRequest request,
    String? effort,
  ) => {
    'model': request.model,
    'input': [
      if (request.system.isNotEmpty)
        {'role': 'system', 'content': request.system},
      {'role': 'user', 'content': request.prompt},
    ],
    'store': false,
    'max_output_tokens': request.maxOutputTokens,
    if (effort != null) 'reasoning': {'effort': effort},
    if (request.promptCacheKey.trim().isNotEmpty)
      'prompt_cache_key': request.promptCacheKey.trim(),
    if (request.webSearch) ...{
      'tools': [
        {'type': 'web_search'},
      ],
      'max_turns': 3,
    },
  };

  static Map<String, dynamic> _decodeObject(
    List<int> bytes,
    DirectProvider provider,
  ) {
    try {
      final decoded = jsonDecode(utf8.decode(bytes));
      if (decoded is Map) return Map<String, dynamic>.from(decoded);
    } on FormatException {
      // 生の応答本文を例外へ反射しない。
    }
    throw DirectProviderException(
      '${labels[provider]}の応答を安全に解析できませんでした。',
      code: 'invalid_response',
      stage: 'response_decode',
      httpAttempts: 1,
      usageMayBeIncomplete: true,
    );
  }

  static _ParsedCompletion _parse(
    DirectProvider provider,
    Map<String, dynamic> data,
  ) => switch (provider) {
    DirectProvider.claude => _parseAnthropic(data),
    DirectProvider.chatgpt || DirectProvider.grok => _parseResponses(data),
    DirectProvider.gemini => _parseGemini(data),
  };

  static _ParsedCompletion _parseAnthropic(Map<String, dynamic> data) {
    final pieces = <String>[];
    final content = data['content'];
    for (final item in content is List ? content : const []) {
      if (item is Map && item['type'] == 'text') {
        final text = item['text'];
        if (text is String && text.trim().isNotEmpty) pieces.add(text.trim());
      }
    }
    final stop = data['stop_reason']?.toString() ?? '';
    if (stop == 'refusal') {
      // 拒否時の任意本文は回答や統合根拠として扱わない。
      throw const DirectProviderException(
        'Claudeは安全上の理由で回答を拒否しました。',
        code: 'provider_refusal',
        stage: 'response_parse',
        httpAttempts: 1,
        usageMayBeIncomplete: true,
      );
    }
    final incomplete =
        {
          'max_tokens',
          'model_context_window_exceeded',
          'pause_turn',
          'tool_use',
        }.contains(stop) ||
        !{'end_turn', 'stop_sequence'}.contains(stop);
    return _ParsedCompletion(
      text: pieces.join('\n\n'),
      finishReason: stop,
      completionStatus: incomplete ? 'incomplete' : 'completed',
      incompleteReason: incomplete ? stop : null,
    );
  }

  static _ParsedCompletion _parseResponses(Map<String, dynamic> data) {
    final pieces = <String>[];
    final output = data['output'];
    for (final item in output is List ? output : const []) {
      if (item is! Map || item['type'] != 'message') continue;
      final content = item['content'];
      if (content is! List) continue;
      for (final block in content) {
        if (block is Map && block['type'] == 'output_text') {
          final text = block['text'];
          if (text is String && text.trim().isNotEmpty) pieces.add(text.trim());
        }
      }
    }
    final rawStatus = data['status']?.toString().toLowerCase() ?? 'completed';
    final details = data['incomplete_details'];
    return _ParsedCompletion(
      text: pieces.join('\n\n'),
      finishReason: rawStatus,
      completionStatus: rawStatus,
      incompleteReason: _incompleteReason(details),
    );
  }

  static _ParsedCompletion _parseGemini(Map<String, dynamic> data) {
    final pieces = <String>[];
    final steps = data['steps'];
    for (final step in steps is List ? steps : const []) {
      if (step is! Map || step['type'] != 'model_output') continue;
      final content = step['content'];
      if (content is! List) continue;
      for (final block in content) {
        if (block is Map && block['type'] == 'text') {
          final text = block['text'];
          if (text is String && text.trim().isNotEmpty) pieces.add(text.trim());
        }
      }
    }
    final status = data['status']?.toString().toLowerCase() ?? 'completed';
    final details = data['incomplete_details'];
    return _ParsedCompletion(
      text: pieces.join('\n\n'),
      finishReason: status,
      completionStatus: status,
      incompleteReason: _incompleteReason(details),
    );
  }

  static String? _incompleteReason(dynamic details) {
    if (details is Map) {
      final reason = details['reason']?.toString().trim() ?? '';
      return reason.isEmpty ? null : reason;
    }
    final reason = details?.toString().trim() ?? '';
    return reason.isEmpty ? null : reason;
  }

  static Map<String, int> _normalizedUsage(dynamic raw) {
    if (raw is! Map) return const {};
    const aliases = <String, String>{
      'input_tokens': 'input_tokens',
      'prompt_tokens': 'input_tokens',
      'total_input_tokens': 'input_tokens',
      'output_tokens': 'output_tokens',
      'completion_tokens': 'output_tokens',
      'total_output_tokens': 'output_tokens',
      'total_tokens': 'total_tokens',
      'cache_creation_input_tokens': 'cache_creation_input_tokens',
      'cache_read_input_tokens': 'cached_input_tokens',
      'total_cached_tokens': 'cached_input_tokens',
      'total_thought_tokens': 'reasoning_tokens',
      'total_tool_use_tokens': 'tool_tokens',
      'num_sources_used': 'sources_used',
      'num_server_side_tools_used': 'server_side_tools_used',
    };
    final result = <String, int>{};
    for (final entry in aliases.entries) {
      final value = raw[entry.key];
      if (value is int && value >= 0) result[entry.value] = value;
    }
    final details =
        raw['output_tokens_details'] ?? raw['completion_tokens_details'];
    if (details is Map && details['reasoning_tokens'] is int) {
      final value = details['reasoning_tokens'] as int;
      if (value >= 0) result['reasoning_tokens'] = value;
    }
    result.putIfAbsent(
      'total_tokens',
      () => (result['input_tokens'] ?? 0) + (result['output_tokens'] ?? 0),
    );
    return result;
  }

  static List<Map<String, dynamic>> _citations(
    DirectProvider provider,
    Map<String, dynamic> data,
  ) {
    final candidates = <dynamic>[];
    if (provider == DirectProvider.claude) {
      final content = data['content'];
      for (final block in content is List ? content : const []) {
        if (block is Map && block['citations'] is List) {
          candidates.addAll(block['citations'] as List);
        }
      }
    } else {
      final rawContainers = provider == DirectProvider.gemini
          ? data['steps']
          : data['output'];
      for (final container
          in rawContainers is List ? rawContainers : const []) {
        if (container is! Map || container['content'] is! List) continue;
        for (final block in container['content'] as List) {
          if (block is Map && block['annotations'] is List) {
            candidates.addAll(block['annotations'] as List);
          }
        }
      }
    }
    if (data['citations'] is List) candidates.addAll(data['citations'] as List);
    final result = <Map<String, dynamic>>[];
    final seen = <String>{};
    for (final item in candidates) {
      if (item is! Map) continue;
      final url = (item['url'] ?? item['source_url'])?.toString().trim() ?? '';
      final uri = Uri.tryParse(url);
      if (uri == null ||
          !{'http', 'https'}.contains(uri.scheme) ||
          uri.host.isEmpty ||
          !seen.add(url)) {
        continue;
      }
      result.add({
        'url': url,
        'title':
            (item['title'] ?? item['source_title'])?.toString() ?? uri.host,
      });
      if (result.length >= 40) break;
    }
    return result;
  }

  static _ReasoningResolution _resolveReasoning(
    DirectProvider provider,
    String model,
    ReasoningMode requested,
  ) {
    final normalized = model.trim().toLowerCase();
    final knownModel = switch (provider) {
      DirectProvider.claude
          when normalized.startsWith('claude-haiku-') ||
              normalized.startsWith('claude-sonnet-') ||
              normalized.startsWith('claude-opus-') ||
              normalized.startsWith('claude-fable-') ||
              normalized.startsWith('claude-mythos-') =>
        true,
      DirectProvider.gemini when normalized.startsWith('gemini-') => true,
      DirectProvider.chatgpt
          when normalized.startsWith('gpt-') ||
              normalized.startsWith('o1') ||
              normalized.startsWith('o3') ||
              normalized.startsWith('o4') =>
        true,
      DirectProvider.grok when normalized.startsWith('grok-') => true,
      _ => false,
    };
    if (!knownModel) {
      return const _ReasoningResolution(
        effective: null,
        source: 'unknown_model',
        pinned: false,
      );
    }

    final supportsEffort = switch (provider) {
      DirectProvider.claude => [
        'claude-fable-5',
        'claude-mythos-5',
        'claude-mythos-preview',
        'claude-opus-4-5',
        'claude-opus-4-6',
        'claude-opus-4-7',
        'claude-opus-4-8',
        'claude-sonnet-4-6',
        'claude-sonnet-5',
      ].any(normalized.startsWith),
      _ => true,
    };
    if (!supportsEffort) {
      return const _ReasoningResolution(
        effective: null,
        source: 'model_unsupported',
        pinned: true,
      );
    }

    if (requested != ReasoningMode.auto) {
      return _ReasoningResolution(
        effective: requested.name,
        source: 'explicit',
        pinned: true,
      );
    }

    final effective = switch (provider) {
      DirectProvider.claude => 'high',
      DirectProvider.gemini => 'medium',
      DirectProvider.chatgpt => 'medium',
      DirectProvider.grok
          when normalized.startsWith('grok-4.5') ||
              normalized.startsWith('grok-4.20') =>
        'high',
      DirectProvider.grok => 'medium',
    };
    return _ReasoningResolution(
      effective: effective,
      source: 'model_policy',
      pinned: true,
    );
  }

  static String _safeHttpError(DirectProvider provider, int statusCode) {
    final label = labels[provider]!;
    final reason = switch (statusCode) {
      400 => 'API要求が受理されませんでした',
      401 => 'API認証に失敗しました',
      403 => 'API利用が許可されていません',
      404 => 'APIまたはモデルが見つかりません',
      408 => 'API要求がタイムアウトしました',
      409 => 'API要求が競合しました',
      413 => 'API要求が大きすぎます',
      422 => 'API要求を処理できません',
      429 => 'API利用上限に達しました',
      >= 500 => 'API側で一時的な障害が発生しました',
      _ => 'API呼び出しに失敗しました',
    };
    return '$label: $reason (HTTP $statusCode)';
  }

  static int _atLeast(int value, int minimum) =>
      value < minimum ? minimum : value;

  static String _tierForRequest(DirectProviderRequest request) {
    final explicit = request.tier?.trim();
    if (explicit != null && explicit.isNotEmpty) return normalizeTier(explicit);
    for (final entry in outputCaps[request.provider]!.entries) {
      if (entry.value == request.maxOutputTokens) return entry.key;
    }
    return 'balanced';
  }

  static _TransportFailure _classifyTransportFailure(Object error) {
    if (error is TimeoutException) {
      return const _TransportFailure('timeout', 'response_wait');
    }

    // 分類のためだけに参照し、この文字列自体は表示・保存・ログ出力しない。
    final type = error.runtimeType.toString().toLowerCase();
    final detail =
        (error is http.ClientException ? error.message : error.toString())
            .toLowerCase();
    final signature = '$type $detail';
    bool containsAny(Iterable<String> values) => values.any(signature.contains);

    if (containsAny(const [
      'already closed',
      'client is closed',
      'client_closed',
      'requestabortedexception',
    ])) {
      return const _TransportFailure('client_closed', 'request_transport');
    }
    if (containsAny(const [
      'handshakeexception',
      'certificate_verify_failed',
      'certificate verify failed',
      'certificate has expired',
      'tls',
      'ssl',
    ])) {
      return const _TransportFailure('tls', 'request_transport');
    }
    if (containsAny(const [
      'failed host lookup',
      'name or service not known',
      'nodename nor servname',
      'name_not_resolved',
      'name resolution',
      'dns',
    ])) {
      return const _TransportFailure('dns', 'request_transport');
    }
    if (containsAny(const [
      'connection refused',
      'actively refused',
      'econnrefused',
    ])) {
      return const _TransportFailure('connection_refused', 'request_transport');
    }
    if (containsAny(const [
      'connection reset',
      'connection aborted',
      'software caused connection abort',
      'broken pipe',
      'connection closed before',
      'connection closed while',
      'econnreset',
    ])) {
      return const _TransportFailure('connection_reset', 'response_wait');
    }
    if (containsAny(const [
      'network is unreachable',
      'no route to host',
      'network unreachable',
      'enetunreach',
    ])) {
      return const _TransportFailure(
        'network_unreachable',
        'request_transport',
      );
    }
    if (containsAny(const ['timed out', 'timeout', 'etimedout'])) {
      return const _TransportFailure('timeout', 'response_wait');
    }
    if (containsAny(const [
      'jsonunsupportedobjecterror',
      'converting object to an encodable object failed',
      'request encoding',
    ])) {
      return const _TransportFailure('request_encoding', 'request_encoding');
    }
    return const _TransportFailure('transport_unknown', 'request_transport');
  }

  static String _safeTransportMessage(DirectProvider provider, String code) {
    final label = labels[provider]!;
    return switch (code) {
      'dns' => '$labelの接続先名を解決できませんでした。ネットワークまたはDNSを確認してください。',
      'tls' => '$labelとの暗号化接続を確立できませんでした。端末の日時とネットワークを確認してください。',
      'connection_refused' => '$labelへの接続が拒否されました。ネットワークを確認してください。',
      'connection_reset' => '$labelとの接続が途中で切断されました。',
      'network_unreachable' => '$labelへネットワーク接続できませんでした。',
      'client_closed' => '$labelへの接続処理は既に終了しています。',
      'timeout' => '$labelへの接続がタイムアウトしました。',
      'request_encoding' => '$labelへのAPI要求を安全に作成できませんでした。',
      _ => '$labelへの安全な接続に失敗しました。',
    };
  }

  void close() => _client.close();
}

class _TransportFailure {
  const _TransportFailure(this.code, this.stage);

  final String code;
  final String stage;
}

class _ParsedCompletion {
  const _ParsedCompletion({
    required this.text,
    required this.finishReason,
    required this.completionStatus,
    this.incompleteReason,
  });

  final String text;
  final String finishReason;
  final String completionStatus;
  final String? incompleteReason;
}

class _ReasoningResolution {
  const _ReasoningResolution({
    required this.effective,
    required this.source,
    required this.pinned,
  });

  final String? effective;
  final String source;
  final bool pinned;

  Map<String, dynamic> toJson(ReasoningMode requested) => {
    'requested': requested.name,
    'effective': effective ?? 'provider_default',
    'source': source,
    'pinned': pinned,
    'policy_version': 1,
  };
}
