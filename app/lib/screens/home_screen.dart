import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../controllers/conversation_search_controller.dart';
import '../controllers/conversation_selection_controller.dart';
import '../controllers/live_run_controller.dart';
import '../models.dart';
import '../provider_catalog.dart';
import '../saved_run_resume.dart';
import '../services/api_client.dart';
import '../services/direct_byok_client.dart';
import '../services/direct_settings_store.dart';
import '../services/local_conversation_store.dart';
import '../services/settings_store.dart';
import '../utils/format.dart';
import '../widgets/home_dialogs.dart';
import '../widgets/turn_view.dart';
import 'app_settings_screen.dart';
import 'settings_screen.dart';
import 'usage_screen.dart';

part 'home_screen_view.dart';

const _promptTemplates = <String, String>{
  '比較': '次の選択肢を、評価軸・長所・短所・不確実性ごとに比較してください。\n\n',
  '反証': '次の案について、成立条件・反例・見落としやすいリスクを検証してください。\n\n',
  '発想': '次のテーマについて、前提の異なる複数の案を出し、それぞれの特徴を示してください。\n\n',
  '事実確認': '次の内容を、確認できる事実・推測・未確認事項に分けて検討してください。\n\n',
};

typedef ApiClientFactory = ApiClient Function(ConnectionSettings settings);
typedef DirectApiClientFactory =
    ApiClient Function(
      DirectSettings settings,
      LocalConversationRepository conversations,
    );
typedef AttachmentPicker = Future<FilePickerResult?> Function();

ApiClient _defaultApiClientFactory(ConnectionSettings settings) =>
    ApiClient(settings);

ApiClient _defaultDirectApiClientFactory(
  DirectSettings settings,
  LocalConversationRepository conversations,
) => DirectByokClient(settings: settings, conversations: conversations);

Future<FilePickerResult?> _defaultAttachmentPicker() =>
    FilePicker.platform.pickFiles(
      allowMultiple: true,
      withData: true,
      type: FileType.custom,
      allowedExtensions: const [
        'txt',
        'md',
        'markdown',
        'csv',
        'json',
        'pdf',
        'png',
        'jpg',
        'jpeg',
        'gif',
        'webp',
      ],
    );

class HomeScreen extends StatefulWidget {
  const HomeScreen({
    super.key,
    required this.repository,
    this.autoload = true,
    this.clientFactory = _defaultApiClientFactory,
    this.directRepository,
    this.localConversationRepository,
    this.directClientFactory = _defaultDirectApiClientFactory,
    this.attachmentPicker = _defaultAttachmentPicker,
    this.allowReferenceServer = !kReleaseMode || kIsWeb,
  });

  final SettingsRepository repository;
  final bool autoload;
  final ApiClientFactory clientFactory;
  final DirectSettingsRepository? directRepository;
  final LocalConversationRepository? localConversationRepository;
  final DirectApiClientFactory directClientFactory;
  final AttachmentPicker attachmentPicker;
  final bool allowReferenceServer;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  final _scaffoldKey = GlobalKey<ScaffoldState>();
  final _messageController = TextEditingController();
  final _searchController = TextEditingController();
  final _scrollController = ScrollController();
  final _messageFocusNode = FocusNode();
  final _searchFocusNode = FocusNode();
  final _selectedProviders = <String>{};
  final _exportingConversationIds = <String>{};
  final _savedRunActionIds = <String>{};
  final _regenerationActionIds = <String>{};
  final _pendingAttachments = <AttachmentRecord>[];
  final _selection = ConversationSelectionController();
  final _run = LiveRunController();
  late final _search = ConversationSearchController(
    client: () => _client,
    onChanged: _onSearchChanged,
  );

  ApiClient? _client;
  ConnectionSettings? _connection;
  DirectSettings? _directSettings;
  ServerSettings? _server;
  List<ConversationSummary> _summaries = const [];
  String _tier = 'balanced';
  String _defaultReasoningMode = 'auto';
  String? _reasoningModeOverride;
  bool _debate = false;
  bool _synthesize = true;
  bool _blind = false;
  bool _webSearch = false;
  bool _loading = false;
  bool _uploadingAttachment = false;
  bool _cancelPending = false;
  String _error = '';
  int _bootstrapEpoch = 0;
  Timer? _scrollTimer;

  ConversationRecord? get _conversation => _selection.conversation;
  LiveTurn? get _liveTurn => _run.turn;
  String? get _selectedId => _selection.selectedId;
  bool get _loadingConversation => _selection.loading;
  bool get _sending => _run.sending;
  bool get _streamDisconnected => _run.disconnected;
  bool get _showTokenUsageLedger =>
      _directSettings?.showTokenUsageLedger ?? true;
  String? get _terminalReloadConversationId =>
      _run.terminalReloadConversationId;
  List<ConversationSummary>? get _searchResults => _search.results;
  bool get _searching => _search.searching;
  String get _searchError => _search.error;
  String get _reasoningMode => _reasoningModeOverride ?? _defaultReasoningMode;
  String get _effortSelection => _reasoningModeOverride ?? 'default';

  void _insertPromptTemplate(String template) {
    final value = _messageController.value;
    final selection = value.selection.isValid
        ? value.selection
        : TextSelection.collapsed(offset: value.text.length);
    final start = selection.start.clamp(0, value.text.length);
    final end = selection.end.clamp(start, value.text.length);
    final nextText = value.text.replaceRange(start, end, template);
    _messageController.value = TextEditingValue(
      text: nextText,
      selection: TextSelection.collapsed(offset: start + template.length),
    );
    _messageFocusNode.requestFocus();
  }

  @override
  void initState() {
    super.initState();
    _searchController.addListener(_scheduleSearch);
    if (widget.autoload) unawaited(_bootstrap());
  }

  @override
  void dispose() {
    _bootstrapEpoch++;
    _selection.invalidate();
    unawaited(_run.dispose());
    _search.dispose();
    _client?.close();
    _scrollTimer?.cancel();
    _messageController.dispose();
    _searchController
      ..removeListener(_scheduleSearch)
      ..dispose();
    _messageFocusNode.dispose();
    _searchFocusNode.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  // 検索の実体はcontrollers/conversation_search_controller.dartへ移設。
  void _scheduleSearch({bool immediate = false}) =>
      _search.schedule(_searchController.text, immediate: immediate);

  void _onSearchChanged() {
    if (mounted) setState(() {});
  }

  /// part側(home_screen_view.dart)のUI builderから状態を更新するための橋渡し。
  /// extensionはStateのサブクラスではなくsetState直接呼び出しがprotected警告になるため。
  void _rebuild(VoidCallback fn) => setState(fn);

  Future<void> _bootstrap() async {
    if (_sending || _liveTurn != null) {
      if (mounted) {
        setState(() {
          _error = '会議の実行中は接続先を切り替えられません。先に停止または完了してください。';
        });
      }
      return;
    }
    final epoch = ++_bootstrapEpoch;
    setState(() {
      _loading = true;
      _error = '';
    });
    ApiClient? candidate;
    ConnectionSettings? attemptedConnection;
    DirectSettings? attemptedDirectSettings;
    try {
      final connection = await widget.repository.load();
      attemptedConnection = connection;
      final loadedDirectSettings = await widget.directRepository?.load();
      final directSettings =
          loadedDirectSettings != null &&
              !widget.allowReferenceServer &&
              loadedDirectSettings.executionMode ==
                  ExecutionMode.referenceServer
          ? loadedDirectSettings.copyWith(
              executionMode: ExecutionMode.directByok,
            )
          : loadedDirectSettings;
      attemptedDirectSettings = directSettings;
      if (directSettings?.executionMode == ExecutionMode.directByok) {
        final conversations = widget.localConversationRepository;
        if (conversations == null) {
          throw StateError('Direct BYOKの端末内会話ストレージが初期化されていません。');
        }
        candidate = widget.directClientFactory(directSettings!, conversations);
      } else {
        candidate = widget.clientFactory(connection);
      }
      await candidate.health();
      final results = await Future.wait<Object>([
        candidate.serverSettings(),
        candidate.conversations(),
      ]);
      final server = results[0] as ServerSettings;
      final summaries = results[1] as List<ConversationSummary>;
      ConversationRecord? selected;
      var selectedId = _selectedId;
      if (selectedId != null &&
          summaries.any((item) => item.id == selectedId)) {
        selected = await candidate.conversation(selectedId);
      } else {
        selectedId = null;
      }
      if (!mounted || epoch != _bootstrapEpoch) {
        candidate.close();
        return;
      }
      _client?.close();
      _client = candidate;
      candidate = null;
      final available = server.activeWorkers.toSet();
      final selectedProviders = _selectedProviders.intersection(available);
      if (selectedProviders.isEmpty) selectedProviders.addAll(available);
      setState(() {
        _connection = connection;
        _directSettings = directSettings;
        _server = server;
        _summaries = summaries;
        _selection.restore(selectedId: selectedId, conversation: selected);
        _selectedProviders
          ..clear()
          ..addAll(selectedProviders);
        if (directSettings != null) {
          _defaultReasoningMode = directSettings.reasoningMode.name;
        }
        if (!server.webSearch.enabled) _webSearch = false;
        _loading = false;
      });
      if (_searchController.text.trim().isNotEmpty) {
        _scheduleSearch(immediate: true);
      }
    } catch (error) {
      candidate?.close();
      if (!mounted || epoch != _bootstrapEpoch) return;
      // 保存済み接続先の切替に失敗した後、旧originへ黙って送信し続けない。
      // 現在の接続を明示的に破棄し、次の操作をすべてfail-closedにする。
      _client?.close();
      _client = null;
      _selection.clear();
      _run.reset();
      _search.invalidate();
      setState(() {
        _connection = attemptedConnection;
        _directSettings = attemptedDirectSettings;
        _server = null;
        _summaries = const [];
        _selectedProviders.clear();
        _loading = false;
        _error = '実行環境を初期化できないため接続を無効化しました: $error';
      });
    }
  }

  Future<void> _refresh() async {
    final client = _client;
    if (client == null) {
      await _bootstrap();
      return;
    }
    if (_loading) return;
    final token = _selection.beginOperation();
    final selectedId = token.selectedId;
    setState(() => _loading = true);
    try {
      final summaries = await client.conversations();
      final conversation = selectedId == null
          ? null
          : await client.conversation(selectedId);
      if (!mounted) return;
      if (!_selection.isCurrent(token)) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _summaries = summaries;
        _selection.commit(token, conversation: conversation);
        if (_terminalReloadConversationId == selectedId) {
          _run.clearTerminalReload();
        }
        _loading = false;
        _error = '';
      });
      if (_searchController.text.trim().isNotEmpty) {
        _scheduleSearch(immediate: true);
      }
    } catch (error) {
      if (!mounted) return;
      if (!_selection.isCurrent(token)) {
        setState(() => _loading = false);
        return;
      }
      setState(() {
        _selection.finish(token);
        _loading = false;
        _error = '更新に失敗しました: $error';
      });
    }
  }

  Future<void> _selectConversation(String id) async {
    if (_uploadingAttachment) {
      setState(() => _error = '添付のアップロード完了後に会話を切り替えてください。');
      return;
    }
    if (_selectedId == id && _conversation != null) return;
    final client = _client;
    if (client == null) return;
    late final ConversationSelectionToken token;
    setState(() {
      token = _selection.beginSelection(id);
      _pendingAttachments.clear();
      _error = '';
    });
    try {
      final conversation = await client.conversation(id);
      if (!mounted || !_selection.isCurrent(token)) return;
      setState(() {
        _selection.commit(token, conversation: conversation);
      });
      _scrollToEnd();
    } catch (error) {
      if (!mounted || !_selection.isCurrent(token)) return;
      setState(() {
        _selection.finish(token);
        _error = '会話の読み込みに失敗しました: $error';
      });
    }
  }

  void _newConversation() {
    if (_uploadingAttachment) {
      setState(() => _error = '添付のアップロード完了後に新しい会話を開いてください。');
      return;
    }
    setState(() {
      _selection.clear();
      _pendingAttachments.clear();
      _error = '';
    });
    _messageController.clear();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _messageFocusNode.requestFocus();
    });
  }

  void _focusSearch() {
    if (MediaQuery.sizeOf(context).width < 920) {
      _scaffoldKey.currentState?.openDrawer();
    }
    void focus() {
      if (!mounted || !_searchFocusNode.canRequestFocus) return;
      _searchFocusNode.requestFocus();
      _searchController.selection = TextSelection(
        baseOffset: 0,
        extentOffset: _searchController.text.length,
      );
    }

    WidgetsBinding.instance.addPostFrameCallback((_) => focus());
    if (MediaQuery.sizeOf(context).width < 920) {
      Future<void>.delayed(const Duration(milliseconds: 300), focus);
    }
  }

  Future<void> _copyConversationJson(String id, String title) async {
    final client = _client;
    if (client == null || _exportingConversationIds.contains(id)) return;
    setState(() => _exportingConversationIds.add(id));
    final messenger = ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text('「$title」のJSONを取得しています…'),
          duration: const Duration(seconds: 30),
        ),
      );
    try {
      final json = await client.exportConversationJson(id);
      await Clipboard.setData(ClipboardData(text: json));
      if (!mounted) return;
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(
          SnackBar(content: Text('「$title」のJSONをクリップボードへコピーしました。')),
        );
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = 'JSONエクスポートに失敗しました: $error');
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(
          SnackBar(
            content: Text('JSONをコピーできませんでした: $error'),
            backgroundColor: Theme.of(context).colorScheme.error,
          ),
        );
    } finally {
      if (mounted) {
        setState(() => _exportingConversationIds.remove(id));
      }
    }
  }

  Future<void> _saveConversationArchive(String id, String title) async {
    final client = _client;
    if (client == null || _exportingConversationIds.contains(id)) return;
    setState(() => _exportingConversationIds.add(id));
    try {
      final bytes = await client.exportConversationArchive(id);
      final shortId = id.length >= 8 ? id.substring(0, 8) : id;
      final saved = await FilePicker.platform.saveFile(
        dialogTitle: '会話アーカイブを保存',
        fileName: 'clage-cook-$shortId.zip',
        type: FileType.custom,
        allowedExtensions: const ['zip'],
        bytes: bytes,
        lockParentWindow: true,
      );
      if (!mounted || saved == null) return;
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text('「$title」のZIPを保存しました。')));
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = 'ZIPエクスポートに失敗しました: $error');
    } finally {
      if (mounted) setState(() => _exportingConversationIds.remove(id));
    }
  }

  Future<void> _editConversationMemory() async {
    final client = _client;
    final conversation = _conversation;
    if (client == null || conversation == null || _sending) return;
    final value = await showDialog<String>(
      context: context,
      builder: (context) =>
          MemoryEditorDialog(initialText: conversation.memory.text),
    );
    if (value == null || !mounted) return;
    try {
      final updated = await client.updateConversationMemory(
        id: conversation.id,
        expectedRevision: conversation.memory.revision,
        text: value,
      );
      if (!mounted || _selectedId != updated.id) return;
      setState(() {
        _selection.replaceConversationIfSelected(updated);
        _error = '';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = 'ローカルメモを保存できませんでした: $error');
    }
  }

  Future<void> _deleteConversation(ConversationSummary summary) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('会話を削除'),
        content: Text('「${summary.title}」を完全に削除しますか？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('キャンセル'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('削除'),
          ),
        ],
      ),
    );
    if (confirmed != true || _client == null) return;
    try {
      await _client!.deleteConversation(summary.id);
      if (!mounted) return;
      if (_selectedId == summary.id) _newConversation();
      await _refresh();
    } catch (error) {
      if (mounted) setState(() => _error = '削除に失敗しました: $error');
    }
  }

  Future<void> _renameConversation(ConversationSummary summary) async {
    final controller = TextEditingController(text: summary.title);
    final title = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('タイトルを変更'),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 120,
          decoration: const InputDecoration(border: OutlineInputBorder()),
          onSubmitted: (value) => Navigator.pop(context, value.trim()),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('キャンセル'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('保存'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (title == null || title.isEmpty || _client == null) return;
    try {
      await _client!.renameConversation(summary.id, title);
      await _refresh();
    } catch (error) {
      if (mounted) setState(() => _error = '変更に失敗しました: $error');
    }
  }

  Future<void> _openSettings() async {
    if (_sending || _uploadingAttachment || _liveTurn != null) {
      setState(() {
        _error = _uploadingAttachment
            ? '添付のアップロード完了後に接続先を変更してください。'
            : '会議の実行中は接続先を変更できません。先に停止または完了してください。';
      });
      return;
    }
    final initial = _connection ?? await widget.repository.load();
    if (!mounted) return;
    final directRepository = widget.directRepository;
    final initialDirect = _directSettings ?? await directRepository?.load();
    if (!mounted) return;
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => directRepository != null && initialDirect != null
            ? AppSettingsScreen(
                directRepository: directRepository,
                serverRepository: widget.repository,
                initialDirect: initialDirect,
                initialServer: initial,
                initialServerSettings:
                    initialDirect.executionMode == ExecutionMode.referenceServer
                    ? _server
                    : null,
                allowReferenceServer: widget.allowReferenceServer,
              )
            : SettingsScreen(
                repository: widget.repository,
                initial: initial,
                initialServerSettings: _server,
              ),
      ),
    );
    if (changed == true) await _bootstrap();
  }

  Future<void> _openUsage() async {
    final client = _client;
    if (client == null) {
      setState(() => _error = '先に実行方式を設定してください。');
      return;
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute(builder: (_) => UsageScreen(client: client)),
    );
  }

  Future<void> _showPolicyBlocked(PolicyScanResult policy) async {
    // ダイアログ本体はwidgets/home_dialogs.dartへ移設。置換後の反映処理のみここに残す。
    final redacted = policy.redactedText.trim();
    final replace = await showPolicyBlockedDialog(context, policy);
    if (replace == true && mounted) {
      setState(() {
        _messageController
          ..text = redacted
          ..selection = TextSelection.collapsed(offset: redacted.length);
        _error = '';
      });
      _messageFocusNode.requestFocus();
    }
  }

  bool _requiresBillableConfirmation(RunPlan plan, PolicyScanResult policy) =>
      plan.billable &&
      (_directSettings?.showLiveApiConfirmation != false ||
          policy.action == 'confirm');

  Future<bool> _approveBillableRun(
    RunPlan plan,
    PolicyScanResult policy,
  ) async {
    if (!_requiresBillableConfirmation(plan, policy)) return true;
    // ダイアログ本体はwidgets/home_dialogs.dartへ移設。設定保存フローのみここに残す。
    final action = await showBillableRunConfirmationDialog(
      context,
      plan: plan,
      policy: policy,
      allowDisableFuture:
          policy.action != 'confirm' &&
          widget.directRepository != null &&
          _directSettings != null,
    );
    if (!mounted || action == BillableConfirmationAction.cancel) return false;
    if (action == BillableConfirmationAction.disableFuture) {
      return _disableFutureLiveApiConfirmation();
    }
    return true;
  }

  PolicyScanResult _effectivePolicy(
    PolicyScanResult standalone,
    PolicyScanResult planned,
  ) {
    if (standalone.blocked) return standalone;
    if (planned.blocked) return planned;
    if (planned.action == 'confirm') return planned;
    if (standalone.action == 'confirm') return standalone;
    return planned;
  }

  Future<bool> _disableFutureLiveApiConfirmation() async {
    final repository = widget.directRepository;
    final settings = _directSettings;
    if (repository == null || settings == null) {
      setState(() {
        _error = 'この実行環境では確認表示の設定を保存できません。設定画面から変更してください。';
      });
      return false;
    }
    final updated = settings.copyWith(showLiveApiConfirmation: false);
    try {
      await repository.setShowLiveApiConfirmation(false);
      if (!mounted) return false;
      setState(() {
        _directSettings = updated;
        _error = '';
      });
      return true;
    } catch (error) {
      if (mounted) {
        setState(() => _error = '実API確認の表示設定を保存できませんでした: $error');
      }
      return false;
    }
  }

  Future<void> _send() async {
    final client = _client;
    final rawDraft = _messageController.text;
    final message = rawDraft.trim();
    if (_sending ||
        _loadingConversation ||
        _uploadingAttachment ||
        _liveTurn != null ||
        message.isEmpty) {
      return;
    }
    if (client == null || _server == null) {
      setState(() => _error = '先に設定画面でバックエンドへ接続するか、Direct BYOKを設定してください。');
      return;
    }
    final providers = providerOrder
        .where(_selectedProviders.contains)
        .toList(growable: false);
    final conversationId = _selectedId;
    final tier = _tier;
    final reasoningMode = _reasoningMode;
    final debate = _debate;
    final synthesize = _synthesize;
    final blind = _blind;
    final webSearch = _webSearch;
    final attachmentIds = _pendingAttachments
        .map((item) => item.id)
        .toList(growable: false);
    if (providers.isEmpty) {
      setState(() => _error = '参加するAIを1つ以上選んでください。');
      return;
    }
    setState(() {
      _run.beginRequest();
      _error = '';
    });
    var startingChat = false;
    var confirmedLiveApi = false;
    var confirmedSensitiveData = false;
    try {
      final checks = await Future.wait<Object>([
        client.planChat(
          message: message,
          conversationId: conversationId,
          tier: tier,
          reasoningMode: reasoningMode,
          debate: debate,
          providers: providers,
          synthesize: synthesize,
          blind: blind,
          webSearch: webSearch,
          attachmentIds: attachmentIds,
        ),
        client.scanPolicy(message),
      ]);
      if (!mounted) return;
      final plan = checks[0] as RunPlan;
      final scan = checks[1] as PolicyScanResult;
      final effectivePolicy = _effectivePolicy(scan, plan.policy);
      final blockedPolicy = effectivePolicy.blocked ? effectivePolicy : null;
      if (blockedPolicy != null) {
        setState(_run.endRequest);
        await _showPolicyBlocked(blockedPolicy);
        return;
      }
      if (!plan.allowed) {
        final reasons = plan.warnings
            .map((warning) => warning.message)
            .where((message) => message.isNotEmpty)
            .toSet()
            .join(' ');
        setState(() {
          _run.endRequest();
          _error = reasons.isEmpty ? '現在の設定ではこの会議を開始できません。' : reasons;
        });
        return;
      }
      if (plan.billable) {
        final confirmed = await _approveBillableRun(plan, effectivePolicy);
        if (!mounted) return;
        if (!confirmed) {
          setState(_run.endRequest);
          return;
        }
        confirmedLiveApi = true;
        confirmedSensitiveData = effectivePolicy.action == 'confirm';
      }
      startingChat = true;
      final stream = await client.startChat(
        message: message,
        conversationId: conversationId,
        tier: tier,
        reasoningMode: reasoningMode,
        debate: debate,
        providers: providers,
        synthesize: synthesize,
        blind: blind,
        webSearch: webSearch,
        attachmentIds: attachmentIds,
        confirmLiveApi: confirmedLiveApi,
        confirmSensitiveData: confirmedSensitiveData,
      );
      if (!mounted) return;
      final live = LiveTurn(
        requestId: stream.requestId,
        message: message,
        providers: providers,
        tier: tier,
        reasoningMode: reasoningMode,
        debate: debate,
        synthesize: synthesize,
        blind: blind,
        webSearch: webSearch,
        confirmedLiveApi: confirmedLiveApi,
        confirmedSensitiveData: confirmedSensitiveData,
        conversationId: stream.conversationId,
        attachmentIds: attachmentIds,
      );
      // HTTP受理後にだけ下書きを消す。controller更新をsetStateの外で通知し、
      // Flutter Webのsemantics inputにも確実に反映させる。
      if (_messageController.text == rawDraft) {
        _messageController.clear();
      }
      setState(() {
        _pendingAttachments.clear();
        _run.attach(live);
        _selection.selectId(stream.conversationId);
      });
      _scrollToEnd();
      await _consumeStream(stream, live);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        final current = _run.turn;
        if (current != null && startingChat) {
          _run.disconnect(current, '会議を開始できません: $error');
        } else {
          _run.endRequest();
        }
        _error = startingChat
            ? '会議を開始できませんでした: $error'
            : '送信前の安全確認に失敗しました: $error';
      });
    }
  }

  Future<void> _pickAttachments() async {
    final client = _client;
    if (client == null ||
        _server == null ||
        _uploadingAttachment ||
        _sending ||
        _liveTurn != null) {
      return;
    }
    final picked = await widget.attachmentPicker();
    if (picked == null || picked.files.isEmpty || !mounted) return;
    final token = _selection.beginOperation();
    final selectedId = token.selectedId;
    final startingConversation = _conversation;
    setState(() {
      _uploadingAttachment = true;
      _error = '';
    });
    try {
      var conversation = startingConversation;
      if (selectedId != null && conversation?.id != selectedId) {
        conversation = await client.conversation(selectedId);
      }
      conversation ??= await client.createDraftConversation();
      final uploaded = <AttachmentRecord>[];
      for (final file in picked.files) {
        final bytes = file.bytes;
        if (bytes == null) {
          throw const ApiException('選択したファイルを読み取れませんでした');
        }
        uploaded.add(
          await client.uploadAttachment(
            conversationId: conversation.id,
            name: file.name,
            bytes: bytes,
          ),
        );
      }
      final summaries = await client.conversations();
      if (!mounted) return;
      if (!identical(_client, client) || !_selection.isCurrent(token)) {
        setState(() {
          _error = '会話または接続先が変更されたため、アップロード済み添付を現在の会話には追加しませんでした。';
        });
        return;
      }
      setState(() {
        _selection.commit(
          token,
          conversation: conversation,
          selectConversation: true,
        );
        _summaries = summaries;
        _pendingAttachments.addAll(uploaded);
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _selection.finish(token);
        _error = '添付をアップロードできませんでした: $error';
      });
    } finally {
      if (mounted) setState(() => _uploadingAttachment = false);
    }
  }

  Future<void> _removePendingAttachment(AttachmentRecord attachment) async {
    final client = _client;
    final conversationId = _selectedId;
    if (client == null || conversationId == null || _uploadingAttachment) {
      return;
    }
    setState(() => _uploadingAttachment = true);
    try {
      await client.deleteAttachment(
        conversationId: conversationId,
        attachmentId: attachment.id,
      );
      if (!mounted) return;
      setState(
        () =>
            _pendingAttachments.removeWhere((item) => item.id == attachment.id),
      );
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '添付を削除できませんでした: $error');
    } finally {
      if (mounted) setState(() => _uploadingAttachment = false);
    }
  }

  Future<void> _resumeLiveTurn() async {
    final live = _liveTurn;
    final client = _client;
    if (live == null || client == null || _sending) return;
    setState(() {
      _run.beginReconnect(live);
    });
    try {
      final stream = await client.startChat(
        message: live.message,
        conversationId: live.conversationId,
        requestId: live.requestId,
        lastEventId: live.lastEventId,
        tier: live.tier,
        reasoningMode: live.reasoningMode,
        debate: live.debate,
        providers: live.providers,
        synthesize: live.synthesize,
        blind: live.blind,
        webSearch: live.webSearch,
        confirmLiveApi: live.confirmedLiveApi,
        confirmSensitiveData: live.confirmedSensitiveData,
        attachmentIds: live.attachmentIds,
      );
      await _consumeStream(stream, live);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _run.disconnect(live, '再接続に失敗しました: $error');
      });
    }
  }

  Future<void> _cancelLiveTurn() async {
    final live = _liveTurn;
    final client = _client;
    if (live == null || client == null || _cancelPending) return;
    setState(() {
      _cancelPending = true;
      live.phase = '停止をリクエスト中';
    });
    try {
      final result = await client.cancelRun(live.requestId);
      if (!mounted || _liveTurn?.requestId != live.requestId) return;
      setState(() {
        if (result.terminalOutcome.isNotEmpty) live.error = '';
        live.phase = cancelRunStatus(result);
      });
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(cancelRunNotice(result))));
    } catch (error) {
      if (!mounted || _liveTurn?.requestId != live.requestId) return;
      setState(() {
        _error = '停止リクエストに失敗しました: $error';
      });
    } finally {
      if (mounted && _cancelPending) {
        setState(() => _cancelPending = false);
      }
    }
  }

  Future<void> _reconnectSavedTurn(TurnRecord turn) async {
    final client = _client;
    final conversationId = _conversation?.id ?? _selectedId;
    if (client == null ||
        conversationId == null ||
        turn.status != 'running' ||
        _sending ||
        _liveTurn != null ||
        _savedRunActionIds.contains(turn.requestId)) {
      return;
    }
    final resume = turn.resumeRequest;
    // tier/reasoning_mode/providers/attachment_idsのバリデーション・正規化は
    // saved_run_resume.dartの純関数へ移設(挙動はunitテストで固定)。
    final params = SavedRunResume.fromTurn(turn);
    setState(() {
      _savedRunActionIds.add(turn.requestId);
      _run.beginRequest();
      _error = '';
    });
    try {
      final stream = await client.startChat(
        message: turn.message.isNotEmpty ? turn.message : turn.cleanMessage,
        conversationId: conversationId,
        requestId: turn.requestId,
        tier: params.requestTier,
        reasoningMode: params.requestReasoningMode,
        debate: resume['debate'] == true,
        providers: params.requestedProviders,
        synthesize: resume['synthesize'] != false,
        blind: resume['blind'] == true,
        webSearch: resume['web_search'] == true,
        confirmLiveApi: resume['confirm_live_api'] == true,
        confirmSensitiveData: resume['confirm_sensitive_data'] == true,
        attachmentIds: params.attachmentIds,
      );
      if (!mounted) return;
      final live = LiveTurn(
        requestId: stream.requestId,
        message: turn.message.isNotEmpty ? turn.message : turn.cleanMessage,
        providers: params.displayProviders,
        tier: params.effectiveTier,
        reasoningMode: params.requestReasoningMode,
        debate: turn.options['debate'] == true,
        synthesize: turn.options['synthesize'] != false,
        blind: turn.options['blind'] == true,
        webSearch: turn.options['web_search'] == true,
        confirmedLiveApi: resume['confirm_live_api'] == true,
        confirmedSensitiveData: resume['confirm_sensitive_data'] == true,
        conversationId: stream.conversationId.isNotEmpty
            ? stream.conversationId
            : conversationId,
        attachmentIds: params.attachmentIds,
      );
      setState(() {
        _savedRunActionIds.remove(turn.requestId);
        _run.attach(live);
        _selection.selectId(live.conversationId);
      });
      _scrollToEnd();
      await _consumeStream(stream, live);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _savedRunActionIds.remove(turn.requestId);
        final current = _run.turn;
        final message = '保存された実行へ再接続できませんでした: $error';
        if (current?.requestId == turn.requestId) {
          _run.disconnect(current!, message);
        } else {
          _run.endRequest();
        }
        _error = message;
      });
    }
  }

  Future<void> _cancelSavedTurn(TurnRecord turn) async {
    final client = _client;
    if (client == null ||
        turn.status != 'running' ||
        _savedRunActionIds.contains(turn.requestId)) {
      return;
    }
    setState(() {
      _savedRunActionIds.add(turn.requestId);
      _error = '';
    });
    try {
      final result = await client.cancelRun(turn.requestId);
      if (!mounted) return;
      await _refresh();
      if (!mounted) return;
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(cancelRunNotice(result))));
    } catch (error) {
      if (!mounted) return;
      await _refresh();
      if (!mounted) return;
      final missing = error is ApiException && error.statusCode == 404;
      setState(() {
        _error = missing
            ? 'このrunは現在のサーバープロセスに見つかりません。履歴を更新しました。'
                  '外部Provider側の停止・課金停止は保証されません。'
            : '保存されたrunの停止要求に失敗しました: $error';
      });
    } finally {
      if (mounted) {
        setState(() => _savedRunActionIds.remove(turn.requestId));
      }
    }
  }

  Future<void> _forkEditTurn(TurnRecord turn) async {
    final client = _client;
    final conversation = _conversation;
    if (client == null ||
        conversation == null ||
        turn.status != 'completed' ||
        _sending ||
        _liveTurn != null) {
      return;
    }
    final controller = TextEditingController(
      text: turn.message.isNotEmpty ? turn.message : turn.cleanMessage,
    );
    final edited = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('発言を編集して分岐'),
        content: SizedBox(
          width: 640,
          child: TextField(
            controller: controller,
            minLines: 4,
            maxLines: 14,
            autofocus: true,
            decoration: const InputDecoration(
              border: OutlineInputBorder(),
              helperText: '元の会話は変更せず、このターン直前の履歴から新しい会話を作ります。',
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('キャンセル'),
          ),
          FilledButton.icon(
            onPressed: () {
              final value = controller.text.trim();
              if (value.isNotEmpty) Navigator.pop(context, value);
            },
            icon: const Icon(Icons.call_split),
            label: const Text('分岐を作成'),
          ),
        ],
      ),
    );
    controller.dispose();
    if (edited == null || !mounted) return;
    final token = _selection.beginOperation(loading: true);
    setState(() {
      _error = '';
    });
    try {
      final branch = await client.forkConversationAtTurn(
        conversationId: conversation.id,
        turnRequestId: turn.requestId,
      );
      final summaries = await client.conversations();
      if (!mounted || !_selection.isCurrent(token)) return;
      _messageController.text = edited;
      _messageController.selection = TextSelection.collapsed(
        offset: _messageController.text.length,
      );
      setState(() {
        _selection.commit(
          token,
          conversation: branch,
          selectConversation: true,
        );
        _summaries = summaries;
        _pendingAttachments.clear();
      });
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _messageFocusNode.requestFocus();
      });
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('元の履歴を保った編集分岐を作成しました。内容を確認して送信してください。')),
      );
    } catch (error) {
      if (!mounted || !_selection.isCurrent(token)) return;
      setState(() {
        _selection.finish(token);
        _error = '編集分岐を作成できませんでした: $error';
      });
    }
  }

  Future<void> _regenerateSavedTurn(
    TurnRecord turn, {
    required String target,
    String? provider,
  }) async {
    final client = _client;
    final conversationId = _conversation?.id;
    if (client == null || conversationId == null) {
      setState(() => _error = '再生成する会話を読み込めません。');
      return;
    }
    final actionId = '${turn.requestId}:$target:${provider ?? 'synthesis'}';
    if (_regenerationActionIds.contains(actionId)) return;
    setState(() {
      _regenerationActionIds.add(actionId);
      _error = '';
    });
    try {
      final plan = await client.regenerationPlan(
        conversationId: conversationId,
        turnRequestId: turn.requestId,
        target: target,
        provider: provider,
      );
      if (!plan.allowed) {
        final reason = plan.warnings
            .map((warning) => warning.message)
            .where((message) => message.isNotEmpty)
            .join(' ');
        throw ApiException(reason.isEmpty ? '安全条件により再生成できません。' : reason);
      }
      final confirmed = await _approveBillableRun(plan, plan.policy);
      if (!mounted) return;
      if (!confirmed) return;
      final conversation = await client.regenerate(
        conversationId: conversationId,
        turnRequestId: turn.requestId,
        target: target,
        provider: provider,
        confirmLiveApi: plan.billable,
        confirmSensitiveData: plan.billable && plan.policy.action == 'confirm',
      );
      final summaries = await client.conversations();
      if (!mounted || _conversation?.id != conversationId) return;
      setState(() {
        _selection.replaceConversationIfSelected(conversation);
        _summaries = summaries;
        _error = '';
      });
      _scrollToEnd();
    } catch (error) {
      if (!mounted) return;
      setState(() => _error = '再生成に失敗しました: $error');
    } finally {
      if (mounted) {
        setState(() => _regenerationActionIds.remove(actionId));
      }
    }
  }

  Future<void> _consumeStream(ChatStream stream, LiveTurn live) async {
    final session = LiveStreamSession(idleTimeout: stream.idleTimeout);
    StreamSubscription<SseEvent>? subscription;
    try {
      subscription = stream.events.listen(
        (event) {
          if (session.sawDone) return;
          session.recordActivity();
          if (!mounted || !_run.isCurrent(live.requestId)) {
            session.markDetached();
            return;
          }
          if (event.event == SseDecoder.keepAliveEvent) return;
          if (event.id.isNotEmpty) live.lastEventId = event.id;
          if (event.event == 'done') {
            session.markDone(event.data);
            return;
          }
          _handleEvent(event, live);
        },
        onError: (Object error, StackTrace stackTrace) {
          session.markError(error);
        },
        onDone: session.markEof,
        cancelOnError: false,
      );
      _run.ownStream(live, session, subscription);
      await session.completed;
    } finally {
      if (subscription == null) {
        session.dispose();
      } else {
        await _run.releaseStream(session);
      }
    }
    if (session.detached) return;
    if (session.failure != null) {
      if (!mounted || !_run.isCurrent(live.requestId)) return;
      setState(() {
        _run.disconnect(
          live,
          '通信が切断されました。サーバー側の会議は継続中の可能性があります。 (${session.failure})',
        );
      });
      return;
    }
    if (!mounted || !_run.isCurrent(live.requestId)) return;
    if (!session.sawDone) {
      setState(() {
        _run.disconnect(live, 'ストリームが完了通知なしで終了しました。');
      });
      return;
    }
    if (session.failed) {
      final message = session.cancelled
          ? '会議をキャンセルしました。外部Provider側の停止・課金停止は保証されません。'
          : (live.error.isEmpty ? '会議の実行に失敗しました。' : live.error);
      await _finishLiveTurn(live);
      if (mounted) {
        ScaffoldMessenger.of(context)
          ..hideCurrentSnackBar()
          ..showSnackBar(SnackBar(content: Text(message)));
      }
      return;
    }
    await _finishLiveTurn(live);
  }

  void _handleEvent(SseEvent event, LiveTurn live) {
    setState(() {
      switch (event.event) {
        case 'meta':
          live.conversationId =
              event.data['conversation_id']?.toString() ?? live.conversationId;
          if (_selectedId == null || _selectedId!.isEmpty) {
            _selection.selectId(live.conversationId);
          }
          final backends = event.data['backends'];
          if (backends is List) {
            live.providers
              ..clear()
              ..addAll(backends.map((item) => item.toString()));
          }
          live.phase = '各AIが回答しています';
          break;
        case 'answer':
          final answer = AnswerRecord.fromJson(event.data);
          if (answer.source.isNotEmpty) live.answers[answer.source] = answer;
          live.phase = live.answers.length < live.providers.length
              ? '回答 ${live.answers.length}/${live.providers.length}'
              : (live.debate ? '相互批評または統合の準備中' : '統合の準備中');
          break;
        case 'phase':
          final name = event.data['name']?.toString();
          final status = event.data['status']?.toString();
          if (name == 'debate') {
            live.phase = status == 'completed' ? '相互批評が完了しました' : '相互批評中';
          } else if (name == 'synthesis') {
            live.phase = '統合回答を作成中';
          }
          break;
        case 'synthesis':
          live.synthesis = SynthesisRecord.fromJson(event.data);
          live.phase = '完了処理中';
          break;
        case 'insights':
          live.insights = Map<String, dynamic>.from(event.data);
          break;
        case 'error':
          live.error = event.data['message']?.toString() ?? '会議に失敗しました。';
          break;
      }
    });
    _scrollToEnd();
  }

  Future<void> _finishLiveTurn(LiveTurn live) async {
    final client = _client;
    if (client == null) return;
    try {
      final results = await Future.wait<Object>([
        client.conversations(),
        client.conversation(live.conversationId),
      ]);
      if (!mounted || !_run.isCurrent(live.requestId)) return;
      final summaries = results[0] as List<ConversationSummary>;
      final conversation = results[1] as ConversationRecord;
      setState(() {
        _summaries = summaries;
        _selection.replaceConversationIfSelected(conversation);
        _run.finish();
      });
      _scrollToEnd();
    } catch (error) {
      if (!mounted || !_run.isCurrent(live.requestId)) return;
      setState(() {
        _selection.restore(selectedId: live.conversationId);
        _run.requireTerminalReload(live.conversationId);
        _error = '会議は終了しましたが、保存済み会話の取得に失敗しました: $error';
      });
    }
  }

  Future<void> _reloadTerminalConversation() async {
    final id = _terminalReloadConversationId;
    final client = _client;
    if (id == null || client == null || _loadingConversation) return;
    late final ConversationSelectionToken token;
    setState(() {
      token = _selection.beginSelection(id);
      _error = '';
    });
    try {
      final results = await Future.wait<Object>([
        client.conversations(),
        client.conversation(id),
      ]);
      if (!mounted ||
          !_run.isTerminalReloadCurrent(id) ||
          !_selection.isCurrent(token)) {
        return;
      }
      setState(() {
        _summaries = results[0] as List<ConversationSummary>;
        _selection.commit(
          token,
          conversation: results[1] as ConversationRecord,
        );
        _run.clearTerminalReload();
      });
      _scrollToEnd();
    } catch (error) {
      if (!mounted ||
          !_run.isTerminalReloadCurrent(id) ||
          !_selection.isCurrent(token)) {
        return;
      }
      setState(() {
        _selection.finish(token);
        _error = '保存済み会話の再読み込みに失敗しました: $error';
      });
    }
  }

  void _scrollToEnd() {
    void scroll() {
      if (!mounted || !_scrollController.hasClients) return;
      unawaited(
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        ),
      );
    }

    WidgetsBinding.instance.addPostFrameCallback((_) => scroll());
    _scrollTimer?.cancel();
    _scrollTimer = Timer(const Duration(milliseconds: 350), scroll);
  }

  @override
  Widget build(BuildContext context) {
    return CallbackShortcuts(
      bindings: <ShortcutActivator, VoidCallback>{
        const SingleActivator(LogicalKeyboardKey.keyK, control: true):
            _focusSearch,
        const SingleActivator(LogicalKeyboardKey.keyK, meta: true):
            _focusSearch,
        const SingleActivator(LogicalKeyboardKey.keyN, control: true):
            _newConversation,
        const SingleActivator(LogicalKeyboardKey.keyN, meta: true):
            _newConversation,
      },
      child: Focus(
        autofocus: true,
        child: LayoutBuilder(
          builder: (context, constraints) {
            final wide = constraints.maxWidth >= 920;
            final compact = constraints.maxWidth < 620;
            return Scaffold(
              key: _scaffoldKey,
              appBar: _appBar(wide, compact),
              drawer: wide
                  ? null
                  : Drawer(child: SafeArea(child: _sidebar(true))),
              body: Row(
                children: [
                  if (wide) SizedBox(width: 320, child: _sidebar(false)),
                  if (wide) const VerticalDivider(width: 1),
                  Expanded(child: _chatPane()),
                ],
              ),
            );
          },
        ),
      ),
    );
  }
}
