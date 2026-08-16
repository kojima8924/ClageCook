import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:http/http.dart' as http;

import '../models.dart';
import 'api_contract.dart';
import 'local_conversation_store.dart';

export 'api_contract.dart';

/// 開発用reference serverへHTTP/SSEで接続する [ClageApiClient] 実装。
class ApiClient implements ClageApiClient {
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

  List<LocalConversationDefect> _storageDefects =
      const <LocalConversationDefect>[];

  /// 直近の一覧で読めなかった会話。
  ///
  /// この実行方式では正本がserver側にあるため、`GET /api/conversations` の
  /// `corrupt` をそのまま載せ替える。件数を黙って捨てると、利用者には
  /// 「会話が消えた」ようにしか見えない(issue #18)。
  @override
  List<LocalConversationDefect> get storageDefects => _storageDefects;

  /// 端末内ストレージの隔離・index再構築に対応するか。
  ///
  /// 隔離もindex再構築も端末内storage専用の操作で、serverの正本には行わない。
  /// bannerは件数と理由だけを出し、復旧ボタンは出さない。
  @override
  bool get supportsLocalStorageRepair => false;

  /// 実行中のrunへ後からSSEで再接続できるか。
  ///
  /// runの状態をプロセス内に持つ実行方式でだけ真になる。UIは
  /// `turn.status` の文字列ではなくこの能力で再接続導線を出し分ける。
  @override
  bool get supportsRunReconnect => true;

  @override
  bool get supportsBudgetReconciliation => true;

  /// 破損した会話をmanifestから外し、中身は隔離して保持する。
  @override
  Future<int> quarantineDefectiveConversations(Iterable<String> ids) =>
      throw const ApiException('この実行環境では端末内ストレージを修復できません。');

  /// 残っているrecordから会話indexを組み直す。
  @override
  Future<int> rebuildConversationIndex() =>
      throw const ApiException('この実行環境では端末内ストレージを修復できません。');

  @override
  Future<Map<String, dynamic>> health() => _getMap('/api/health');

  @override
  Future<ServerSettings> serverSettings() async =>
      ServerSettings.fromJson(await _getMap('/api/settings'));

  @override
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

  @override
  Future<UsageTelemetrySnapshot> usageTelemetry() async =>
      UsageTelemetrySnapshot.fromJson(await _getMap('/api/telemetry'));

  @override
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

  @override
  Future<RunPlan> planChat({
    required String message,
    String? conversationId,
    String tier = 'balanced',
    String reasoningMode = 'auto',
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
      reasoningMode: reasoningMode,
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

  @override
  Future<PolicyScanResult> scanPolicy(String text) async =>
      PolicyScanResult.fromJson(
        await _postMap('/api/policy/scan', {'text': text}),
      );

  @override
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

  @override
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

  @override
  Future<List<ConversationSummary>> conversations() async {
    final data = await _getMap('/api/conversations');
    final items = data['items'];
    if (items is! List) throw const ApiException('会話一覧の形式が不正です');
    // 読めなかったファイルは部分失敗として扱う。健全な会話は返したうえで、
    // 件数と理由だけを別途UIへ渡す。
    _storageDefects = _decodeDefects(data['corrupt']);
    return items
        .whereType<Map>()
        .map(
          (item) =>
              ConversationSummary.fromJson(Map<String, dynamic>.from(item)),
        )
        .toList(growable: false);
  }

  /// `GET /api/conversations` の `corrupt` をUI共通のdefectへ載せ替える。
  ///
  /// server側の破損はファイル単位で、端末内storageのようなrevisionを持たない。
  /// どのrevisionだったか不明であることを表す0を入れる。
  static List<LocalConversationDefect> _decodeDefects(Object? corrupt) {
    if (corrupt is! List) return const <LocalConversationDefect>[];
    return corrupt
        .whereType<Map>()
        .map(
          (item) => LocalConversationDefect(
            conversationId: item['id']?.toString() ?? '',
            storageRevision: 0,
            reason: item['reason']?.toString() ?? 'unknown',
          ),
        )
        .toList(growable: false);
  }

  @override
  Future<ConversationSearchResult> searchConversations(
    String query, {
    int limit = 30,
  }) async {
    final trimmed = query.trim();
    if (trimmed.isEmpty) {
      return const ConversationSearchResult(query: '', results: []);
    }
    final data = await _postMap('/api/conversations/search', {
      'q': trimmed,
      'limit': limit.clamp(1, 100),
    });
    return ConversationSearchResult.fromJson(data);
  }

  @override
  Future<ConversationRecord> conversation(String id) async =>
      ConversationRecord.fromJson(await _getMap('/api/conversations/$id'));

  @override
  Future<ConversationRecord> createDraftConversation() async =>
      ConversationRecord.fromJson(
        await _postMap('/api/conversations', const {}),
      );

  @override
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

  @override
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

  @override
  Future<ConversationRecord> forkConversationAtTurn({
    required String conversationId,
    required String turnRequestId,
  }) async => ConversationRecord.fromJson(
    await _postMap(
      '/api/conversations/$conversationId/turns/$turnRequestId/fork',
      const {},
    ),
  );

  @override
  Future<String> exportConversationJson(String id) async {
    final data = _decode(await _get('/api/conversations/$id/export'));
    if (data is! Map) throw const ApiException('エクスポートの形式が不正です');
    return const JsonEncoder.withIndent(
      '  ',
    ).convert(Map<String, dynamic>.from(data));
  }

  @override
  Future<Uint8List> exportConversationArchive(String id) async {
    final response = await _get('/api/conversations/$id/export?format=zip');
    return response.bodyBytes;
  }

  @override
  Future<void> deleteConversation(String id) async {
    final response = await _client
        .delete(Uri.parse('$baseUrl/api/conversations/$id'), headers: _headers)
        .timeout(const Duration(seconds: 20));
    _ensureSuccess(response);
  }

  @override
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

  @override
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

  @override
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

  @override
  Future<ChatStream> startChat({
    required String message,
    String? conversationId,
    String tier = 'balanced',
    String reasoningMode = 'auto',
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
        reasoningMode: reasoningMode,
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
        // backendのHTTPエラーは必ず `{code, message, ...}`(docs/API_ERRORS.md)。
        // 生JSONを利用者へ見せず、表示用の message だけを取り出す。
        if (detail is Map) {
          final message = detail['message']?.toString().trim() ?? '';
          if (message.isNotEmpty) return message;
          final code = detail['code']?.toString().trim() ?? '';
          if (code.isNotEmpty) return 'HTTP $statusCode: $code';
        } else if (detail is String && detail.trim().isNotEmpty) {
          // 契約外の応答(古いserver・前段のproxy)でも文字列なら読める形で出す。
          return detail;
        }
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
    required String reasoningMode,
    required bool debate,
    required List<String>? providers,
    required bool synthesize,
    required bool blind,
    required bool webSearch,
    required List<String> attachmentIds,
  }) => {
    'message': message,
    'tier': tier,
    'reasoning_mode': reasoningMode,
    'debate': debate,
    'providers': providers,
    'synthesize': synthesize,
    'blind': blind,
    if (webSearch) 'web_search': true,
    if (attachmentIds.isNotEmpty) 'attachment_ids': attachmentIds,
  };

  @override
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
