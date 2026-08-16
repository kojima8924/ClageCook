import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:archive/archive.dart';

import '../models.dart';
import 'api_contract.dart';
import 'direct_provider_client.dart';
import 'direct_run_guard.dart';
import 'direct_settings_store.dart';
import 'local_conversation_store.dart';
import 'policy_scanner.dart';

// 2000行を超えていた1ファイルを、挙動を変えずに関心ごとへ分割したもの。
// すべて同一ライブラリのpartなので、private要素の見え方は分割前と同じ。
part 'direct/direct_attachments.dart';
part 'direct/direct_conference.dart';
part 'direct/direct_prompts.dart';
part 'direct/direct_run_plan.dart';
part 'direct/direct_support.dart';

const _directProviderOrder = <DirectProvider>[
  DirectProvider.claude,
  DirectProvider.gemini,
  DirectProvider.chatgpt,
  DirectProvider.grok,
];

const _maxDirectRunOutputTokens = 196608;
const _maxDirectWorkerInputBytes = 1024 * 1024;

typedef DirectProviderClientFactory = DirectProviderClient Function();

/// FastAPIを介さず、端末から4社APIへ直接接続する [ClageApiClient] 実装。
///
/// UIは既存の会話・再生成・分岐操作をそのまま使い、会話の正本だけを端末内
/// [LocalConversationRepository] に置く。APIキーはこのクラスから保存しない。
class DirectByokClient implements ClageApiClient {
  DirectByokClient({
    required DirectSettings settings,
    required LocalConversationRepository conversations,
    DirectProviderClientFactory? providerClientFactory,
    DirectRunGuard? runGuard,
    Duration heartbeatInterval = const Duration(seconds: 20),
  }) : // The public constructor keeps descriptive non-private parameter names.
       // ignore: prefer_initializing_formals
       _settings = settings,
       // ignore: prefer_initializing_formals
       _conversations = conversations,
       _providerClientFactory =
           providerClientFactory ?? (() => DirectProviderClient()),
       _runGuard = runGuard ?? DirectRunGuard.shared,
       _heartbeatInterval = heartbeatInterval,
       assert(heartbeatInterval > Duration.zero);

  final DirectSettings _settings;

  final LocalConversationRepository _conversations;

  final DirectProviderClientFactory _providerClientFactory;

  final DirectRunGuard _runGuard;

  final Duration _heartbeatInterval;

  final Map<String, _DirectRunState> _runs = {};

  final Set<String> _pendingRequestIds = {};

  final Map<String, String> _regenerationReservations = {};

  final Map<String, Map<String, _DirectAttachment>> _attachments = {};

  List<LocalConversationDefect> _storageDefects =
      const <LocalConversationDefect>[];

  bool _compacted = false;

  bool _closed = false;

  @override
  List<LocalConversationDefect> get storageDefects => _storageDefects;

  @override
  bool get supportsLocalStorageRepair => true;

  /// Directは実行状態をこのオブジェクト内にしか持たず、保存turnへ 'running' を
  /// 書かない。後から再接続できる相手が存在しないため常にfalse。
  @override
  bool get supportsRunReconnect => false;

  @override
  Future<int> quarantineDefectiveConversations(Iterable<String> ids) async {
    final moved = await _conversations.quarantine(ids);
    _storageDefects = const <LocalConversationDefect>[];
    return moved;
  }

  @override
  Future<int> rebuildConversationIndex() async {
    final recovered = await _conversations.rebuildManifestFromRecords();
    _storageDefects = const <LocalConversationDefect>[];
    return recovered;
  }

  /// 一覧・検索のたびに破損状況を更新する。読めた分はそのまま返す。
  LocalConversationListing _observe(LocalConversationListing listing) {
    _storageDefects = listing.defects;
    return listing;
  }

  List<DirectProvider> get _configuredProviders =>
      _directProviderOrder.where(_settings.hasKey).toList(growable: false);

  DirectProvider? get _synthesizer {
    final requested = _settings.synthesizerProvider;
    if (requested != null && _settings.hasKey(requested)) return requested;
    for (final provider in const [
      DirectProvider.claude,
      DirectProvider.chatgpt,
      DirectProvider.gemini,
      DirectProvider.grok,
    ]) {
      if (_settings.hasKey(provider)) return provider;
    }
    return null;
  }

  @override
  Future<Map<String, dynamic>> health() async => {
    'ok': true,
    'mode': 'direct-byok',
    'storage': 'on-device',
    'run_guard': _runGuard.diagnostics,
    'configured_providers': _configuredProviders
        .map((item) => item.name)
        .toList(),
  };

  @override
  Future<ServerSettings> serverSettings() async {
    final providers = <Map<String, dynamic>>[];
    final catalog = <String, List<String>>{};
    for (final provider in _directProviderOrder) {
      final low = DirectProviderClient.modelFor(
        provider,
        'low',
        override: _settings.modelOverrideFor(provider),
      );
      final balanced = DirectProviderClient.modelFor(
        provider,
        'balanced',
        override: _settings.modelOverrideFor(provider),
      );
      final high = DirectProviderClient.modelFor(
        provider,
        'high',
        override: _settings.modelOverrideFor(provider),
      );
      providers.add({
        'name': provider.name,
        'label': DirectProviderClient.labels[provider],
        'configured': _settings.hasKey(provider),
        'mode': _settings.hasKey(provider) ? 'live' : 'disabled',
        'models': {'low': low, 'balanced': balanced, 'high': high},
      });
      catalog[provider.name] = {low, balanced, high}.toList(growable: false);
    }
    return ServerSettings.fromJson({
      'mode': 'direct-byok',
      'providers': providers,
      'active_workers': _configuredProviders.map((item) => item.name).toList(),
      'synthesizer': _synthesizer?.name ?? '',
      'auth_required': false,
      'live_api_enabled': true,
      'web_search': {
        'enabled': true,
        'default': false,
        'max_uses': 3,
        'strict_total_limit': false,
      },
      'runtime_settings': {
        'revision': 0,
        'writable': false,
        'synthesizer_provider': _synthesizer?.name ?? 'auto',
        'effective_synthesizer_models': const {},
        'catalog': catalog,
      },
    });
  }

  @override
  Future<ServerSettings> updateRuntimeSettings({
    required int expectedRevision,
    required Map<String, Map<String, String?>> models,
    required String synthesizerProvider,
    required Map<String, String?> synthesizerModels,
  }) => throw const ApiException('Direct BYOKのモデル設定は端末設定画面で変更してください。');

  @override
  Future<UsageTelemetrySnapshot> usageTelemetry() async {
    final summaries = _observe(await _conversations.list()).items;
    return UsageTelemetrySnapshot.fromJson({
      'generated_at': _now(),
      'providers': const [],
      'finance': const {},
      'admin': const {},
      'limitations': const [
        'Direct BYOKでは各社応答に含まれる実測tokenだけを会話へ保存します。残高や請求額は各社コンソールで確認してください。',
      ],
      'conversation_count': summaries.length,
      'turn_count': summaries.fold<int>(0, (sum, item) => sum + item.turnCount),
    });
  }

  /// Directは予算reservationを持たないため、照合の解放という概念がない。
  /// 空のsnapshotを返して「解放できた」ように見せず、能力として明示する。
  @override
  bool get supportsBudgetReconciliation => false;

  @override
  Future<BudgetSnapshot> releaseBudgetReconciliation({
    required String requestId,
    required String note,
  }) async => throw const ApiException('Direct BYOKには解放できる予算照合がありません。');

  @override
  Future<PolicyScanResult> scanPolicy(String text) async =>
      PolicyScanResult.fromJson(scanPolicyText(text));

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
    final cleanMessage = message.trim();
    final selected = _resolveProviders(providers);
    final document = conversationId == null || conversationId.isEmpty
        ? null
        : await _requiredConversation(conversationId);
    if (document == null && attachmentIds.isNotEmpty) {
      throw const ApiException('新しい会話の添付を確認できないため、実API計画を作成しません。');
    }
    final attachments = document == null
        ? const <_DirectAttachment>[]
        : _requiredAttachments(document.id, attachmentIds);
    final attachmentContext = _attachmentContext(attachments);
    final modelMessage = '$cleanMessage$attachmentContext';
    final conversation =
        document?.value ??
        const <String, dynamic>{
          'memory': <String, dynamic>{'text': ''},
          'turns': <dynamic>[],
        };
    final input = _inputSnapshot(
      conversation: conversation,
      message: cleanMessage,
      attachmentContext: attachmentContext,
    );
    return RunPlan.fromJson(
      _planJson(
        message: modelMessage,
        tier: tier,
        reasoningMode: reasoningMode,
        debate: debate,
        providers: selected,
        synthesize: synthesize,
        blind: blind,
        webSearch: webSearch,
        input: input,
      ),
    );
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
    // Directは実行状態をこのオブジェクト内にしか持たず再接続を提供しない
    // (supportsRunReconnect == false)。Last-Event-IDによる再開は行わない。
    String? lastEventId,
    List<String> attachmentIds = const [],
  }) async {
    if (_closed) throw const ApiException('Direct BYOKクライアントは終了済みです。');
    final cleanMessage = message.trim();
    if (cleanMessage.isEmpty) throw const ApiException('質問を入力してください。');
    final resolvedRequestId = (requestId ?? _newId('direct-run')).trim();
    if (resolvedRequestId.runes.length < 8 ||
        resolvedRequestId.runes.length > 80) {
      throw const ApiException('request IDの形式が不正です。');
    }
    if (_runs.containsKey(resolvedRequestId) ||
        !_pendingRequestIds.add(resolvedRequestId)) {
      throw const ApiException('端末内実行中の会議へ再接続する必要はありません。');
    }
    try {
      if (!confirmLiveApi) {
        throw const ApiException('実API呼び出しの確認が必要です。');
      }
      final messagePolicy = scanPolicyText(cleanMessage);
      _requirePolicyConfirmation(
        messagePolicy,
        confirmSensitiveData: confirmSensitiveData,
      );
      final selected = _resolveProviders(providers);
      if (selected.isEmpty) {
        throw const ApiException('APIキーを設定したAIを1つ以上選んでください。');
      }
      final document = conversationId == null || conversationId.isEmpty
          ? await _conversations.create(firstMessage: cleanMessage)
          : await _requiredConversation(conversationId);
      if (_mapList(
        document.value['turns'],
      ).any((turn) => turn['request_id'] == resolvedRequestId)) {
        throw const ApiException('このrequest IDの会議は保存済みです。再実行しません。');
      }
      final attachmentSnapshot = _requiredAttachments(
        document.id,
        attachmentIds,
      );
      final attachmentContext = _attachmentContext(attachmentSnapshot);
      _requirePolicyConfirmation(
        scanPolicyText('$cleanMessage$attachmentContext'),
        confirmSensitiveData: confirmSensitiveData,
      );
      final resolvedTier = DirectProviderClient.normalizeTier(tier);
      final resolvedReasoning = _parseReasoning(reasoningMode);
      final input = _inputSnapshot(
        conversation: document.value,
        message: cleanMessage,
        attachmentContext: attachmentContext,
      );
      final executionPlan = _planJson(
        message: '$cleanMessage$attachmentContext',
        tier: resolvedTier,
        reasoningMode: resolvedReasoning.name,
        debate: debate,
        providers: selected,
        synthesize: synthesize,
        blind: blind,
        webSearch: webSearch,
        input: input,
      );
      final blockReasons =
          (executionPlan['block_reasons'] as List?) ?? const [];
      if (blockReasons.contains('input_byte_limit_exceeded')) {
        throw const ApiException('履歴・メモ・添付を含む初回入力が1 MiBを超えるため送信しません。');
      }
      if (blockReasons.contains('output_token_limit_exceeded')) {
        throw const ApiException('会議全体の最大出力が安全上限を超えるため送信しません。');
      }
      final controller = StreamController<SseEvent>();
      final state = _DirectRunState(
        requestId: resolvedRequestId,
        conversationId: document.id,
        controller: controller,
      );
      _runs[resolvedRequestId] = state;
      _pendingRequestIds.remove(resolvedRequestId);
      unawaited(
        _executeRun(
          state,
          initialDocument: document,
          message: cleanMessage,
          tier: resolvedTier,
          reasoningMode: resolvedReasoning,
          providers: selected,
          debate: debate,
          synthesize: synthesize,
          blind: blind,
          webSearch: webSearch,
          attachments: attachmentSnapshot,
        ),
      );
      return ChatStream(
        conversationId: document.id,
        requestId: resolvedRequestId,
        events: controller.stream,
        idleTimeout: const Duration(minutes: 4),
      );
    } catch (_) {
      _pendingRequestIds.remove(resolvedRequestId);
      rethrow;
    }
  }

  Future<void> _persistTurn(
    _DirectRunState state, {
    required String message,
    required String tier,
    required ReasoningMode reasoningMode,
    required List<DirectProvider> providers,
    required bool debate,
    required bool synthesize,
    required bool blind,
    required bool webSearch,
    required Map<String, Map<String, dynamic>> answers,
    required Map<String, dynamic> synthesis,
    required List<String> attachmentIds,
    required List<_DirectAttachment> attachments,
    required String status,
    required bool cancelled,
  }) async {
    for (var attempt = 0; attempt < 2; attempt++) {
      final current = await _requiredConversation(state.conversationId);
      final conversation = _cloneMap(current.value);
      final turns = _mapList(conversation['turns']);
      if (turns.any((turn) => turn['request_id'] == state.requestId)) return;
      turns.add({
        'request_id': state.requestId,
        'created_at': _now(),
        'message': message,
        'clean_message': message,
        'options': {
          'tier': tier,
          'reasoning_mode': reasoningMode.name,
          'debate': debate,
          'providers': providers.map((item) => item.name).toList(),
          'synthesize': synthesize,
          'blind': blind,
          'web_search': webSearch,
        },
        // 同意(confirm_live_api / confirm_sensitive_data)は保存しない。
        // 保存された同意をそのまま再送すると、再実行時に課金確認を迂回できて
        // しまう。再開経路は毎回 plan → 確認ダイアログ → startChat を通す。
        'resume_request': {
          'tier': tier,
          'reasoning_mode': reasoningMode.name,
          'debate': debate,
          'providers': providers.map((item) => item.name).toList(),
          'synthesize': synthesize,
          'blind': blind,
          'web_search': webSearch,
          'attachment_ids': attachmentIds,
        },
        'answers': _cloneMapMap(answers),
        'synthesis': _cloneMap(synthesis),
        // 同じ事実をstatus/interrupted/failedへ重複して書かない。書き手が
        // 片方だけ更新して表示が食い違う経路を、保存schemaの側で塞ぐ。
        // cancelledは「利用者が止めた」で、通信断のinterruptedとは意味が違う。
        'status': status,
        'cancelled': cancelled,
        'usage_may_be_incomplete': cancelled,
        'attachments': _attachmentReferences(state.conversationId, attachments),
      });
      conversation['turns'] = turns;
      conversation['updated_at'] = _now();
      final title = conversation['title']?.toString().trim() ?? '';
      if (title.isEmpty || title == '新しい会話') {
        conversation['title'] = _titleFromMessage(message);
      }
      try {
        await _conversations.save(
          conversation,
          expectedStorageRevision: current.storageRevision,
        );
        return;
      } on LocalConversationConflict {
        if (attempt == 1) rethrow;
      }
    }
  }

  @override
  Future<List<ConversationSummary>> conversations() async {
    final listing = _observe(await _conversations.list());
    // 起動後の最初の一覧取得で、crash orphanを1度だけ掃除する。
    if (!_compacted && !listing.hasDefects) {
      _compacted = true;
      try {
        await _conversations.compact();
      } catch (_) {
        // 一覧表示を掃除の失敗で壊さない。次の削除時に再試行する。
      }
    }
    return listing.items.map(_summary).toList(growable: false);
  }

  @override
  Future<ConversationSearchResult> searchConversations(
    String query, {
    int limit = 30,
  }) async => ConversationSearchResult(
    query: query.trim(),
    results: _observe(
      await _conversations.search(query, limit: limit),
    ).items.map(_summary).toList(growable: false),
  );

  @override
  Future<ConversationRecord> conversation(String id) async =>
      ConversationRecord.fromJson((await _requiredConversation(id)).value);

  @override
  Future<ConversationRecord> createDraftConversation() async =>
      ConversationRecord.fromJson((await _conversations.create()).value);

  @override
  Future<void> deleteConversation(String id) async {
    final current = await _requiredConversation(id);
    await _conversations.delete(
      id,
      expectedStorageRevision: current.storageRevision,
    );
    _attachments.remove(id);
    try {
      await _conversations.compact();
    } catch (_) {
      // 削除自体は成功している。物理的な後片付けの失敗で失敗扱いにしない。
    }
  }

  @override
  Future<ConversationSummary> renameConversation(
    String id,
    String title,
  ) async {
    final current = await _requiredConversation(id);
    final updated = await _conversations.rename(
      id,
      title,
      expectedStorageRevision: current.storageRevision,
    );
    return _summary(LocalConversationSummary.fromDocument(updated));
  }

  @override
  Future<ConversationRecord> updateConversationMemory({
    required String id,
    required int expectedRevision,
    required String text,
  }) async => ConversationRecord.fromJson(
    (await _conversations.updateMemory(
      conversationId: id,
      expectedMemoryRevision: expectedRevision,
      text: text,
    )).value,
  );

  @override
  Future<ConversationRecord> forkConversationAtTurn({
    required String conversationId,
    required String turnRequestId,
  }) async {
    final current = await _requiredConversation(conversationId);
    final turns = _mapList(current.value['turns']);
    final index = turns.indexWhere(
      (turn) => turn['request_id'] == turnRequestId,
    );
    if (index < 0) throw const ApiException('分岐対象のターンが見つかりません。');
    return ConversationRecord.fromJson(
      (await _conversations.fork(
        conversationId: conversationId,
        beforeTurnIndex: index,
        parentTurnRequestId: turnRequestId,
        expectedStorageRevision: current.storageRevision,
      )).value,
    );
  }

  @override
  Future<String> exportConversationJson(String id) =>
      _conversations.exportJson(id);

  @override
  Future<Uint8List> exportConversationArchive(String id) async {
    final conversationJson = await _conversations.exportJson(id);
    final archive = Archive()
      ..add(ArchiveFile.string('conversation.json', conversationJson))
      ..add(
        ArchiveFile.string(
          'README.txt',
          'Clage Cook Direct BYOK conversation export\r\n'
              '\r\n'
              'conversation.json contains the locally stored conversation.\r\n'
              'API keys and transient attachment bytes are never included.\r\n',
        ),
      );
    return ZipEncoder().encodeBytes(archive);
  }

  @override
  Future<AttachmentRecord> uploadAttachment({
    required String conversationId,
    required String name,
    required Uint8List bytes,
  }) async {
    await _requiredConversation(conversationId);
    if (bytes.length > _maxDirectAttachmentBytes) {
      throw const ApiException('Direct BYOKの添付は1件512 KiBまでです。');
    }
    if (name.contains('\u0000')) {
      throw const ApiException('NULを含む添付名は使用できません。');
    }
    final extension = name.contains('.')
        ? name.split('.').last.toLowerCase()
        : '';
    if (!{'txt', 'md', 'markdown', 'csv', 'json'}.contains(extension)) {
      throw const ApiException(
        'Direct BYOKの初期版はテキスト・Markdown・CSV・JSON添付に対応しています。',
      );
    }
    late final String text;
    try {
      text = utf8.decode(bytes);
    } on FormatException {
      throw const ApiException('添付をUTF-8テキストとして読み取れませんでした。');
    }
    if (text.contains('\u0000')) {
      throw const ApiException('NULを含む添付本文は送信しません。');
    }
    final existing = _attachments[conversationId]?.values ?? const [];
    if (existing.length >= _maxDirectAttachments) {
      throw const ApiException('Direct BYOKの添付は1会話につき最大8件です。');
    }
    final nextTotal = existing.fold<int>(
      bytes.length,
      (total, attachment) => total + attachment.sizeBytes,
    );
    if (nextTotal > _maxDirectAttachmentTotalBytes) {
      throw const ApiException('Direct BYOKの添付合計は512 KiBまでです。');
    }
    final attachment = _DirectAttachment(
      id: _newId('attachment'),
      name: name,
      text: text,
      sizeBytes: bytes.length,
      createdAt: _now(),
    );
    _attachments.putIfAbsent(conversationId, () => {})[attachment.id] =
        attachment;
    return attachment.record(conversationId);
  }

  @override
  Future<void> deleteAttachment({
    required String conversationId,
    required String attachmentId,
  }) async {
    _attachments[conversationId]?.remove(attachmentId);
  }

  @override
  Future<CancelRunResult> cancelRun(String requestId) async {
    final run = _runs[requestId];
    if (run == null) {
      return CancelRunResult(
        ok: true,
        requestId: requestId,
        alreadyDone: true,
        terminalOutcome: 'not_running',
        providerStopGuaranteed: false,
        warning: '実行はすでに終了しています。',
      );
    }
    run.cancel();
    return CancelRunResult(
      ok: true,
      requestId: requestId,
      cancellationRequested: true,
      cancelled: true,
      terminalOutcome: 'cancellation_requested',
      providerStopGuaranteed: false,
      warning: '端末側のHTTP接続を閉じましたが、Provider側の処理・課金停止は保証されません。',
    );
  }

  @override
  Future<RunPlan> regenerationPlan({
    required String conversationId,
    required String turnRequestId,
    required String target,
    String? provider,
  }) async {
    final document = await _requiredConversation(conversationId);
    final turn = _turnById(document.value, turnRequestId);
    final tier = DirectProviderClient.normalizeTier(
      turn['options'] is Map ? turn['options']['tier']?.toString() ?? '' : '',
    );
    final source = target == 'answer'
        ? _providerByName(provider ?? '')
        : _synthesizer;
    final allowed = source != null && _settings.hasKey(source);
    final message =
        turn['clean_message']?.toString() ?? turn['message']?.toString() ?? '';
    // 添付本文が失われていても例外で行き止まりにしない。理由を説明できる
    // planとして返し、UIが「なぜ送れないか」を提示できるようにする。
    final attachmentSnapshot = _attachmentSnapshotOrNull(
      conversationId,
      _turnAttachmentIds(turn),
    );
    return RunPlan.fromJson(
      _singleCallPlan(
        allowed: allowed,
        provider: source,
        tier: tier,
        attachmentSnapshotMissing: attachmentSnapshot == null,
        policy: scanPolicyText(
          '$message${_attachmentContext(attachmentSnapshot ?? const [])}',
        ),
      ),
    );
  }

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
    if (_closed) throw const ApiException('Direct BYOKクライアントは終了済みです。');
    if (!confirmLiveApi) throw const ApiException('再生成の実API確認が必要です。');
    if (target != 'answer' && target != 'synthesis') {
      throw const ApiException('再生成対象が不正です。');
    }
    final resolvedRegenerationId = (regenerationId ?? _newId('regen')).trim();
    if (resolvedRegenerationId.runes.length < 8 ||
        resolvedRegenerationId.runes.length > 80) {
      throw const ApiException('regeneration IDの形式が不正です。');
    }
    final reservation = jsonEncode({
      'conversation_id': conversationId,
      'turn_request_id': turnRequestId,
      'target': target,
      'provider': provider ?? '',
    });
    final activeReservation = _regenerationReservations[resolvedRegenerationId];
    if (_runs.containsKey(resolvedRegenerationId) ||
        activeReservation != null) {
      if (activeReservation != null && activeReservation != reservation) {
        throw const ApiException('regeneration IDが異なる要求で使用中です。');
      }
      throw const ApiException('同じ再生成要求を処理中です。再課金を避けるため実行しません。');
    }
    _regenerationReservations[resolvedRegenerationId] = reservation;

    _DirectRunState? run;
    DirectRunGuardLease? runGuardLease;
    try {
      final document = await _requiredConversation(conversationId);
      final conversation = _cloneMap(document.value);
      final turns = _mapList(conversation['turns']);
      final turnIndex = turns.indexWhere(
        (item) => item['request_id'] == turnRequestId,
      );
      if (turnIndex < 0) throw const ApiException('再生成対象が見つかりません。');
      final turn = turns[turnIndex];
      if (turn['status'] != 'completed') {
        throw const ApiException('完了済みターンだけを再生成できます。');
      }
      final options = turn['options'] is Map
          ? Map<String, dynamic>.from(turn['options'] as Map)
          : <String, dynamic>{};
      final tier = DirectProviderClient.normalizeTier(
        options['tier']?.toString() ?? '',
      );
      final reasoning = _parseReasoning(
        options['reasoning_mode']?.toString() ?? 'auto',
      );
      final message =
          turn['clean_message']?.toString() ??
          turn['message']?.toString() ??
          '';
      final source = target == 'answer'
          ? _providerByName(provider ?? '') ??
                (throw const ApiException('再生成するAIが不正です。'))
          : _synthesizer ?? (throw const ApiException('統合に使えるAPIキーがありません。'));
      if (!_settings.hasKey(source)) {
        throw const ApiException('再生成に使うAPIキーが設定されていません。');
      }
      final existing = _findAttempt(turn, resolvedRegenerationId);
      if (existing != null) {
        if (!_sameRegeneration(existing, target, source.name)) {
          throw const ApiException('regeneration IDが異なる要求で使用済みです。');
        }
        return ConversationRecord.fromJson(document.value);
      }

      final attachmentIds = _turnAttachmentIds(turn);
      final attachmentSnapshot = _requiredAttachments(
        conversationId,
        attachmentIds,
      );
      final modelMessage = '$message${_attachmentContext(attachmentSnapshot)}';
      _requirePolicyConfirmation(
        scanPolicyText(modelMessage),
        confirmSensitiveData: confirmSensitiveData,
      );

      run = _DirectRunState(
        requestId: resolvedRegenerationId,
        conversationId: conversationId,
        // 再生成APIはSSEを公開しない。未購読single-subscription controllerの
        // close待ちで永続停止しないようbroadcast controllerを使う。
        controller: StreamController<SseEvent>.broadcast(),
      );
      _runs[resolvedRegenerationId] = run;
      runGuardLease = await _runGuard.acquire(
        jobId: resolvedRegenerationId,
        operation: DirectRunOperation.regeneration,
      );
      late final Map<String, dynamic> result;
      if (target == 'answer') {
        final previousTurns = _cloneMap(conversation)
          ..['turns'] = turns.take(turnIndex).toList();
        result = await _callProvider(
          run,
          provider: source,
          prompt: _workerPrompt(previousTurns, modelMessage),
          system: '$_workerSystem 前回回答を参照せず、同じ質問へ新しい独立回答を作ってください。',
          tier: tier,
          reasoningMode: reasoning,
          round: 1,
          webSearch: options['web_search'] == true,
        );
      } else {
        final completed = _completedAnswers(turn);
        if (completed.isEmpty) {
          throw const ApiException('完了回答がないため統合できません。');
        }
        result = await _callProvider(
          run,
          provider: source,
          prompt: _synthesisPrompt(
            modelMessage,
            completed,
            options['blind'] == true,
          ),
          system: _synthesisSystem,
          tier: tier,
          reasoningMode: reasoning,
          round: 1,
        );
        result.remove('round');
        result['skipped'] = false;
      }
      if (run.cancelled) {
        result
          ..['ok'] = false
          ..['completion_status'] = 'cancelled'
          ..['partial'] = result['text']?.toString().trim().isNotEmpty == true
          ..['error'] = '再生成を停止しました'
          ..['usage_may_be_incomplete'] = true;
      }
      return await _persistRegeneration(
        conversationId: conversationId,
        turnRequestId: turnRequestId,
        regenerationId: resolvedRegenerationId,
        target: target,
        provider: source.name,
        result: result,
      );
    } finally {
      run?.closeProviderClients();
      await runGuardLease?.release();
      if (run != null) await run.closeStream();
      _runs.remove(resolvedRegenerationId);
      _regenerationReservations.remove(resolvedRegenerationId);
    }
  }

  Future<ConversationRecord> _persistRegeneration({
    required String conversationId,
    required String turnRequestId,
    required String regenerationId,
    required String target,
    required String provider,
    required Map<String, dynamic> result,
  }) async {
    // Provider呼び出しは既に一度だけ完了している。以降は保存だけを再試行し、
    // renameや別ターン保存との競合で課金済み結果を失わないようにする。
    for (var attempt = 0; attempt < 4; attempt++) {
      final current = await _requiredConversation(conversationId);
      final conversation = _cloneMap(current.value);
      final turns = _mapList(conversation['turns']);
      final turnIndex = turns.indexWhere(
        (item) => item['request_id'] == turnRequestId,
      );
      if (turnIndex < 0) throw const ApiException('再生成対象が見つかりません。');
      final turn = turns[turnIndex];
      final existing = _findAttempt(turn, regenerationId);
      if (existing != null) {
        if (!_sameRegeneration(existing, target, provider)) {
          throw const ApiException('regeneration IDが異なる要求で使用済みです。');
        }
        return ConversationRecord.fromJson(current.value);
      }

      if (target == 'answer') {
        final answers = turn['answers'] is Map
            ? Map<String, dynamic>.from(turn['answers'] as Map)
            : <String, dynamic>{};
        _recordAttempt(
          turn,
          'answer:$provider',
          target,
          provider,
          answers[provider],
          result,
          attemptId: regenerationId,
        );
        answers[provider] = _cloneMap(result);
        turn['answers'] = answers;
        turn['synthesis_stale'] = true;
      } else {
        _recordAttempt(
          turn,
          'synthesis',
          target,
          provider,
          turn['synthesis'],
          result,
          attemptId: regenerationId,
        );
        turn['synthesis'] = _cloneMap(result);
        turn['synthesis_stale'] = false;
      }
      turns[turnIndex] = turn;
      conversation['turns'] = turns;
      conversation['updated_at'] = _now();
      try {
        final saved = await _conversations.save(
          conversation,
          expectedStorageRevision: current.storageRevision,
        );
        return ConversationRecord.fromJson(saved.value);
      } on LocalConversationConflict {
        if (attempt == 3) rethrow;
      }
    }
    throw const ApiException('課金済みの再生成結果をローカルへ保存できませんでした。');
  }

  Future<LocalConversationDocument> _requiredConversation(String id) async {
    final value = await _conversations.read(id);
    if (value == null) throw const ApiException('会話が見つかりません。');
    return value;
  }

  @override
  void close() {
    if (_closed) return;
    _closed = true;
    for (final run in _runs.values.toList()) {
      run.cancel();
    }
    _runs.clear();
    _pendingRequestIds.clear();
    _regenerationReservations.clear();
  }
}
