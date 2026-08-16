import 'dart:convert';
import 'dart:typed_data';

import '../models.dart';
import 'local_conversation_store.dart';

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

/// UIが依存するAPI契約。
///
/// 実行方式(開発用reference server / 端末から各社APIを直接呼ぶDirect BYOK)は
/// この契約だけを共有し、互いの実装を継承しない。以前は Direct BYOK が
/// 具象HTTPクライアントを継承していたため、契約へendpointを1つ足すと
/// Direct側だけが未実装のまま黙ってHTTPを試みる形になっていた。
/// 契約をinterfaceにしておくと、その取りこぼしはコンパイルエラーで止まる。
abstract interface class ClageApiClient {
  /// 直近の一覧・検索で読めなかった端末内会話。
  ///
  /// 会話の正本をサーバーへ置く実行方式では常に空。端末が正本の実行方式だけが
  /// 中身を返し、UIは「破損1件で全部消えた」ように見せずに済む。
  List<LocalConversationDefect> get storageDefects;

  /// 端末内ストレージの隔離・index再構築に対応するか。
  bool get supportsLocalStorageRepair;

  /// 実行中のrunへ後からSSEで再接続できるか。
  ///
  /// runの状態をプロセス内に持つ実行方式でだけ真になる。UIは
  /// `turn.status` の文字列ではなくこの能力で再接続導線を出し分ける。
  bool get supportsRunReconnect;

  /// 予算照合(reservationの解放)に対応するか。
  bool get supportsBudgetReconciliation;

  /// 破損した会話をmanifestから外し、中身は隔離して保持する。
  Future<int> quarantineDefectiveConversations(Iterable<String> ids);

  /// 残っているrecordから会話indexを組み直す。
  Future<int> rebuildConversationIndex();

  Future<Map<String, dynamic>> health();

  Future<ServerSettings> serverSettings();

  Future<ServerSettings> updateRuntimeSettings({
    required int expectedRevision,
    required Map<String, Map<String, String?>> models,
    required String synthesizerProvider,
    required Map<String, String?> synthesizerModels,
  });

  Future<UsageTelemetrySnapshot> usageTelemetry();

  Future<BudgetSnapshot> releaseBudgetReconciliation({
    required String requestId,
    required String note,
  });

  Future<RunPlan> planChat({
    required String message,
    String? conversationId,
    String tier,
    String reasoningMode,
    bool debate,
    List<String>? providers,
    bool synthesize,
    bool blind,
    bool webSearch,
    List<String> attachmentIds,
  });

  Future<PolicyScanResult> scanPolicy(String text);

  Future<RunPlan> regenerationPlan({
    required String conversationId,
    required String turnRequestId,
    required String target,
    String? provider,
  });

  Future<ConversationRecord> regenerate({
    required String conversationId,
    required String turnRequestId,
    required String target,
    String? provider,
    bool confirmLiveApi,
    bool confirmSensitiveData,
    String? regenerationId,
  });

  Future<List<ConversationSummary>> conversations();

  Future<ConversationSearchResult> searchConversations(
    String query, {
    int limit,
  });

  Future<ConversationRecord> conversation(String id);

  Future<ConversationRecord> createDraftConversation();

  Future<AttachmentRecord> uploadAttachment({
    required String conversationId,
    required String name,
    required Uint8List bytes,
  });

  Future<void> deleteAttachment({
    required String conversationId,
    required String attachmentId,
  });

  Future<ConversationRecord> forkConversationAtTurn({
    required String conversationId,
    required String turnRequestId,
  });

  Future<String> exportConversationJson(String id);

  Future<Uint8List> exportConversationArchive(String id);

  Future<void> deleteConversation(String id);

  Future<ConversationSummary> renameConversation(String id, String title);

  Future<ConversationRecord> updateConversationMemory({
    required String id,
    required int expectedRevision,
    required String text,
  });

  Future<CancelRunResult> cancelRun(String requestId);

  Future<ChatStream> startChat({
    required String message,
    String? conversationId,
    String tier,
    String reasoningMode,
    bool debate,
    List<String>? providers,
    bool synthesize,
    bool blind,
    bool webSearch,
    bool confirmLiveApi,
    bool confirmSensitiveData,
    String? requestId,
    String? lastEventId,
    List<String> attachmentIds,
  });

  void close();
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
