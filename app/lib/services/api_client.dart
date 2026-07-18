import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models.dart';

class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() => message;
}

class SseEvent {
  const SseEvent({required this.event, required this.data, this.id = ''});

  final String event;
  final Map<String, dynamic> data;
  final String id;
}

class ChatStream {
  const ChatStream({
    required this.conversationId,
    required this.requestId,
    required this.events,
    required this.idleTimeout,
  });

  final String conversationId;
  final String requestId;
  final Stream<SseEvent> events;
  final Duration idleTimeout;
}

class ApiClient {
  ApiClient(
    ConnectionSettings settings, {
    http.Client? client,
    this.sseIdleTimeout = const Duration(seconds: 90),
    this.errorBodyTimeout = const Duration(seconds: 10),
    this.errorBodyMaxBytes = 64 * 1024,
  }) : assert(sseIdleTimeout > Duration.zero),
       assert(errorBodyTimeout > Duration.zero),
       assert(errorBodyMaxBytes > 0),
       _settings = settings,
       _client = client ?? http.Client();

  final ConnectionSettings _settings;
  final http.Client _client;
  final Duration sseIdleTimeout;
  final Duration errorBodyTimeout;
  final int errorBodyMaxBytes;

  String get baseUrl {
    var value = _settings.baseUrl.trim();
    while (value.endsWith('/')) {
      value = value.substring(0, value.length - 1);
    }
    return value;
  }

  Map<String, String> get _headers => {
    'Accept': 'application/json',
    if (_settings.token.trim().isNotEmpty)
      'Authorization': 'Bearer ${_settings.token.trim()}',
  };

  Future<Map<String, dynamic>> health() => _getMap('/api/health');

  Future<ServerSettings> serverSettings() async =>
      ServerSettings.fromJson(await _getMap('/api/settings'));

  Future<ServerSettings> updateRuntimeSettings({
    required int expectedRevision,
    required Map<String, Map<String, String?>> models,
    required String synthesizerProvider,
    required Map<String, String?> synthesizerModels,
  }) async => ServerSettings.fromJson(
    await _patchMap('/api/settings/runtime', {
      'expected_revision': expectedRevision,
      'models': models,
      'synthesizer_provider': synthesizerProvider,
      'synthesizer_models': synthesizerModels,
    }),
  );

  Future<UsageTelemetrySnapshot> usageTelemetry() async =>
      UsageTelemetrySnapshot.fromJson(await _getMap('/api/telemetry'));

  Future<BudgetSnapshot> releaseBudgetReconciliation({
    required String requestId,
    required String note,
  }) async {
    final response = await _postMap(
      '/api/budget/reconciliation/$requestId/release',
      {'confirmed_no_unobserved_charge': true, 'note': note},
    );
    final finance = response['finance'];
    if (finance is! Map) {
      throw const ApiException('予算照合応答の形式が不正です');
    }
    return BudgetSnapshot.fromJson(Map<String, dynamic>.from(finance));
  }

  Future<RunPlan> planChat({
    required String message,
    String? conversationId,
    String tier = 'balanced',
    bool debate = false,
    List<String>? providers,
    bool synthesize = true,
    bool blind = false,
    bool webSearch = false,
    List<String> attachmentIds = const [],
  }) async {
    final payload = _runPayload(
      message: message,
      tier: tier,
      debate: debate,
      providers: providers,
      synthesize: synthesize,
      blind: blind,
      webSearch: webSearch,
      attachmentIds: attachmentIds,
    );
    if (conversationId != null && conversationId.isNotEmpty) {
      payload['conversation_id'] = conversationId;
    }
    return RunPlan.fromJson(await _postMap('/api/plan', payload));
  }

  Future<PolicyScanResult> scanPolicy(String text) async =>
      PolicyScanResult.fromJson(
        await _postMap('/api/policy/scan', {'text': text}),
      );

  Future<RunPlan> regenerationPlan({
    required String conversationId,
    required String turnRequestId,
    required String target,
    String? provider,
  }) async => RunPlan.fromJson(
    await _postMap(
      '/api/conversations/$conversationId/turns/$turnRequestId/regeneration-plan',
      {'target': target, 'provider': ?provider},
    ),
  );

  Future<ConversationRecord> regenerate({
    required String conversationId,
    required String turnRequestId,
    required String target,
    String? provider,
    bool confirmLiveApi = false,
    bool confirmSensitiveData = false,
    String? regenerationId,
  }) async {
    final response = await _postMap(
      '/api/conversations/$conversationId/turns/$turnRequestId/regenerate',
      {
        'target': target,
        'provider': ?provider,
        'regeneration_id': regenerationId ?? _newRequestId(),
        'confirm_live_api': confirmLiveApi,
        'confirm_sensitive_data': confirmSensitiveData,
      },
      timeout: const Duration(minutes: 16),
    );
    final conversation = response['conversation'];
    if (conversation is! Map) {
      throw const ApiException('再生成応答の会話形式が不正です');
    }
    return ConversationRecord.fromJson(Map<String, dynamic>.from(conversation));
  }

  Future<List<ConversationSummary>> conversations() async {
    final response = await _get('/api/conversations');
    final data = _decode(response);
    if (data is! List) throw const ApiException('会話一覧の形式が不正です');
    return data
        .whereType<Map>()
        .map(
          (item) =>
              ConversationSummary.fromJson(Map<String, dynamic>.from(item)),
        )
        .toList(growable: false);
  }

  Future<ConversationSearchResult> searchConversations(
    String query, {
    int limit = 30,
  }) async {
    final trimmed = query.trim();
    if (trimmed.isEmpty) {
      return const ConversationSearchResult(query: '', results: []);
    }
    final data = await _postMap('/api/search', {
      'q': trimmed,
      'limit': limit.clamp(1, 100),
    });
    return ConversationSearchResult.fromJson(data);
  }

  Future<ConversationRecord> conversation(String id) async =>
      ConversationRecord.fromJson(await _getMap('/api/conversations/$id'));

  Future<ConversationRecord> createDraftConversation() async =>
      ConversationRecord.fromJson(
        await _postMap('/api/conversations', const {}),
      );

  Future<AttachmentRecord> uploadAttachment({
    required String conversationId,
    required String name,
    required Uint8List bytes,
  }) async {
    final request = http.MultipartRequest(
      'POST',
      Uri.parse('$baseUrl/api/conversations/$conversationId/attachments'),
    );
    request.headers.addAll(_headers);
    request.files.add(
      http.MultipartFile.fromBytes('file', bytes, filename: name),
    );
    final streamed = await _client
        .send(request)
        .timeout(const Duration(minutes: 2));
    final response = await http.Response.fromStream(streamed);
    _ensureSuccess(response);
    final data = _decode(response);
    if (data is! Map) throw const ApiException('添付応答の形式が不正です');
    return AttachmentRecord.fromJson(Map<String, dynamic>.from(data));
  }

  Future<void> deleteAttachment({
    required String conversationId,
    required String attachmentId,
  }) async {
    final response = await _client
        .delete(
          Uri.parse(
            '$baseUrl/api/conversations/$conversationId/attachments/$attachmentId',
          ),
          headers: _headers,
        )
        .timeout(const Duration(seconds: 20));
    _ensureSuccess(response);
  }

  Future<ConversationRecord> forkConversationAtTurn({
    required String conversationId,
    required String turnRequestId,
  }) async => ConversationRecord.fromJson(
    await _postMap(
      '/api/conversations/$conversationId/turns/$turnRequestId/fork',
      const {},
    ),
  );

  Future<String> exportConversationJson(String id) async {
    final data = _decode(await _get('/api/conversations/$id/export'));
    if (data is! Map) throw const ApiException('エクスポートの形式が不正です');
    return const JsonEncoder.withIndent(
      '  ',
    ).convert(Map<String, dynamic>.from(data));
  }

  Future<Uint8List> exportConversationArchive(String id) async {
    final response = await _get('/api/conversations/$id/export.zip');
    return response.bodyBytes;
  }

  Future<void> deleteConversation(String id) async {
    final response = await _client
        .delete(Uri.parse('$baseUrl/api/conversations/$id'), headers: _headers)
        .timeout(const Duration(seconds: 20));
    _ensureSuccess(response);
  }

  Future<ConversationSummary> renameConversation(
    String id,
    String title,
  ) async {
    final response = await _client
        .patch(
          Uri.parse('$baseUrl/api/conversations/$id'),
          headers: {..._headers, 'Content-Type': 'application/json'},
          body: jsonEncode({'title': title}),
        )
        .timeout(const Duration(seconds: 20));
    _ensureSuccess(response);
    final data = _decode(response);
    if (data is! Map) throw const ApiException('タイトル変更の応答が不正です');
    return ConversationSummary.fromJson(Map<String, dynamic>.from(data));
  }

  Future<ConversationRecord> updateConversationMemory({
    required String id,
    required int expectedRevision,
    required String text,
  }) async => ConversationRecord.fromJson(
    await _patchMap('/api/conversations/$id/memory', {
      'expected_revision': expectedRevision,
      'text': text,
    }),
  );

  Future<CancelRunResult> cancelRun(String requestId) async {
    final response = await _client
        .post(
          Uri.parse('$baseUrl/api/runs/$requestId/cancel'),
          headers: _headers,
        )
        .timeout(const Duration(seconds: 20));
    _ensureSuccess(response);
    final data = _decode(response);
    if (data is! Map) throw const ApiException('停止応答の形式が不正です');
    return CancelRunResult.fromJson(Map<String, dynamic>.from(data));
  }

  Future<ChatStream> startChat({
    required String message,
    String? conversationId,
    String tier = 'balanced',
    bool debate = false,
    List<String>? providers,
    bool synthesize = true,
    bool blind = false,
    bool webSearch = false,
    bool confirmLiveApi = false,
    bool confirmSensitiveData = false,
    String? requestId,
    String? lastEventId,
    List<String> attachmentIds = const [],
  }) async {
    final resolvedRequestId = requestId ?? _newRequestId();
    final request = http.Request('POST', Uri.parse('$baseUrl/api/chat'));
    request.headers.addAll({..._headers, 'Content-Type': 'application/json'});
    if (lastEventId != null && lastEventId.isNotEmpty) {
      request.headers['Last-Event-ID'] = lastEventId;
    }
    request.body = jsonEncode({
      ..._runPayload(
        message: message,
        tier: tier,
        debate: debate,
        providers: providers,
        synthesize: synthesize,
        blind: blind,
        webSearch: webSearch,
        attachmentIds: attachmentIds,
      ),
      'conversation_id': conversationId,
      'request_id': resolvedRequestId,
      'confirm_live_api': confirmLiveApi,
      'confirm_sensitive_data': confirmSensitiveData,
    });
    final response = await _client
        .send(request)
        .timeout(const Duration(seconds: 30));
    if (response.statusCode < 200 || response.statusCode >= 300) {
      final errorBody = await _readBoundedErrorBody(response.stream);
      var message = _errorMessage(errorBody.body, response.statusCode);
      if (errorBody.timedOut) {
        message = '$message（エラー応答本文の読込がタイムアウトしました）';
      }
      if (errorBody.truncated) {
        message = '$message（エラー応答本文は$errorBodyMaxBytes bytesで打ち切りました）';
      }
      if (errorBody.readError != null) {
        message = '$message（エラー応答本文を最後まで読み込めませんでした）';
      }
      throw ApiException(message, statusCode: response.statusCode);
    }
    final contentType = response.headers['content-type'];
    if (!_isEventStreamContentType(contentType)) {
      final subscription = response.stream.listen(null);
      await subscription.cancel();
      final received = contentType == null || contentType.trim().isEmpty
          ? '未指定'
          : _truncateRunes(contentType.trim(), 120);
      throw ApiException(
        'SSE応答のContent-Typeがtext/event-streamではありません: $received',
        statusCode: response.statusCode,
      );
    }
    return ChatStream(
      conversationId:
          response.headers['x-conversation-id'] ?? conversationId ?? '',
      requestId: response.headers['x-request-id'] ?? resolvedRequestId,
      events: SseDecoder.decode(response.stream, emitKeepAlive: true),
      idleTimeout: sseIdleTimeout,
    );
  }

  Future<_BoundedErrorBody> _readBoundedErrorBody(
    Stream<List<int>> stream,
  ) async {
    final bytes = BytesBuilder(copy: false);
    final completed = Completer<void>();
    Object? readError;
    var timedOut = false;
    var truncated = false;

    void complete() {
      if (!completed.isCompleted) completed.complete();
    }

    final timer = Timer(errorBodyTimeout, () {
      timedOut = true;
      complete();
    });
    late final StreamSubscription<List<int>> subscription;
    subscription = stream.listen(
      (chunk) {
        if (completed.isCompleted) return;
        final remaining = errorBodyMaxBytes - bytes.length;
        if (remaining <= 0) {
          truncated = true;
          complete();
          return;
        }
        if (chunk.length >= remaining) {
          bytes.add(
            chunk.length == remaining ? chunk : chunk.sublist(0, remaining),
          );
          // Stop at the byte limit even when the current chunk happens to end
          // exactly on it: waiting for another chunk would make the bound
          // ineffective against a server that leaves the stream open.
          truncated = true;
          complete();
          return;
        }
        bytes.add(chunk);
      },
      onError: (Object error, StackTrace stackTrace) {
        readError = error;
        complete();
      },
      onDone: complete,
      cancelOnError: false,
    );

    try {
      await completed.future;
    } finally {
      timer.cancel();
      try {
        await subscription.cancel().timeout(errorBodyTimeout);
      } catch (_) {
        // Error reporting must not be held hostage by a broken body stream.
      }
    }

    return _BoundedErrorBody(
      body: utf8.decode(bytes.takeBytes(), allowMalformed: true),
      timedOut: timedOut,
      truncated: truncated,
      readError: readError,
    );
  }

  Future<Map<String, dynamic>> _getMap(String path) async {
    final data = _decode(await _get(path));
    if (data is! Map) throw const ApiException('サーバー応答の形式が不正です');
    return Map<String, dynamic>.from(data);
  }

  Future<Map<String, dynamic>> _postMap(
    String path,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 20),
  }) async {
    final response = await _client
        .post(
          Uri.parse('$baseUrl$path'),
          headers: {..._headers, 'Content-Type': 'application/json'},
          body: jsonEncode(body),
        )
        .timeout(timeout);
    _ensureSuccess(response);
    final data = _decode(response);
    if (data is! Map) throw const ApiException('サーバー応答の形式が不正です');
    return Map<String, dynamic>.from(data);
  }

  Future<Map<String, dynamic>> _patchMap(
    String path,
    Map<String, dynamic> body, {
    Duration timeout = const Duration(seconds: 20),
  }) async {
    final response = await _client
        .patch(
          Uri.parse('$baseUrl$path'),
          headers: {..._headers, 'Content-Type': 'application/json'},
          body: jsonEncode(body),
        )
        .timeout(timeout);
    _ensureSuccess(response);
    final data = _decode(response);
    if (data is! Map) throw const ApiException('サーバー応答の形式が不正です');
    return Map<String, dynamic>.from(data);
  }

  Future<http.Response> _get(String path) async {
    return _getUri(Uri.parse('$baseUrl$path'));
  }

  Future<http.Response> _getUri(Uri uri) async {
    final response = await _client
        .get(uri, headers: _headers)
        .timeout(const Duration(seconds: 20));
    _ensureSuccess(response);
    return response;
  }

  static dynamic _decode(http.Response response) {
    try {
      return jsonDecode(utf8.decode(response.bodyBytes));
    } on FormatException {
      throw const ApiException('サーバー応答をJSONとして解析できません');
    }
  }

  static void _ensureSuccess(http.Response response) {
    if (response.statusCode >= 200 && response.statusCode < 300) return;
    throw ApiException(
      _errorMessage(utf8.decode(response.bodyBytes), response.statusCode),
      statusCode: response.statusCode,
    );
  }

  static String _errorMessage(String body, int statusCode) {
    try {
      final data = jsonDecode(body);
      if (data is Map && data['detail'] != null) {
        final detail = data['detail'];
        if (detail is String) return detail;
        return jsonEncode(detail);
      }
    } on FormatException {
      // Fall back to a bounded plain-text response below.
    }
    final clean = body.replaceAll(RegExp(r'\s+'), ' ').trim();
    return clean.isEmpty
        ? 'HTTP $statusCode'
        : 'HTTP $statusCode: ${_truncateRunes(clean, 300)}';
  }

  static bool _isEventStreamContentType(String? value) =>
      value?.split(';').first.trim().toLowerCase() == 'text/event-stream';

  static String _truncateRunes(String value, int maxLength) =>
      String.fromCharCodes(value.runes.take(maxLength));

  static String _newRequestId() {
    final random = Random.secure();
    final time = DateTime.now().microsecondsSinceEpoch.toRadixString(16);
    final suffix = List.generate(
      3,
      (_) => random.nextInt(0x7fffffff).toRadixString(16).padLeft(8, '0'),
    ).join();
    return 'flutter-$time-$suffix';
  }

  static Map<String, dynamic> _runPayload({
    required String message,
    required String tier,
    required bool debate,
    required List<String>? providers,
    required bool synthesize,
    required bool blind,
    required bool webSearch,
    required List<String> attachmentIds,
  }) => {
    'message': message,
    'tier': tier,
    'debate': debate,
    'providers': providers,
    'synthesize': synthesize,
    'blind': blind,
    if (webSearch) 'web_search': true,
    if (attachmentIds.isNotEmpty) 'attachment_ids': attachmentIds,
  };

  void close() => _client.close();
}

class _BoundedErrorBody {
  const _BoundedErrorBody({
    required this.body,
    required this.timedOut,
    required this.truncated,
    required this.readError,
  });

  final String body;
  final bool timedOut;
  final bool truncated;
  final Object? readError;
}

class SseDecoder {
  static const keepAliveEvent = '_keepalive';

  static Stream<SseEvent> decode(
    Stream<List<int>> input, {
    bool emitKeepAlive = false,
  }) async* {
    var event = 'message';
    var lastEventId = '';
    var dataLines = <String>[];

    await for (final line
        in input.transform(utf8.decoder).transform(const LineSplitter())) {
      if (line.isEmpty) {
        if (dataLines.isNotEmpty) {
          yield _event(event, lastEventId, dataLines);
        }
        event = 'message';
        dataLines = <String>[];
        continue;
      }
      if (line.startsWith(':')) {
        if (emitKeepAlive) {
          yield const SseEvent(event: keepAliveEvent, data: {});
        }
        continue;
      }
      final separator = line.indexOf(':');
      final field = separator < 0 ? line : line.substring(0, separator);
      var value = separator < 0 ? '' : line.substring(separator + 1);
      if (value.startsWith(' ')) value = value.substring(1);
      switch (field) {
        case 'event':
          event = value;
          break;
        case 'id':
          // Per the SSE specification the last event ID persists across
          // events, an empty id resets it, and values containing NUL are
          // ignored.
          if (!value.contains('\u0000')) lastEventId = value;
          break;
        case 'data':
          dataLines.add(value);
          break;
      }
    }
    if (dataLines.isNotEmpty) yield _event(event, lastEventId, dataLines);
  }

  static SseEvent _event(String event, String id, List<String> lines) {
    final raw = lines.join('\n');
    try {
      final decoded = jsonDecode(raw);
      return SseEvent(
        event: event.isEmpty ? 'message' : event,
        id: id,
        data: decoded is Map
            ? Map<String, dynamic>.from(decoded)
            : {'value': decoded},
      );
    } on FormatException {
      return SseEvent(
        event: event.isEmpty ? 'message' : event,
        id: id,
        data: {'raw': raw},
      );
    }
  }
}
