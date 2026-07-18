import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../controllers/conversation_selection_controller.dart';
import '../controllers/live_run_controller.dart';
import '../models.dart';
import '../services/api_client.dart';
import '../services/settings_store.dart';
import '../widgets/turn_view.dart';
import 'settings_screen.dart';
import 'usage_screen.dart';

const _providerOrder = ['claude', 'gemini', 'chatgpt', 'grok'];
const _providerLabels = {
  'claude': 'Claude',
  'gemini': 'Gemini',
  'chatgpt': 'ChatGPT',
  'grok': 'Grok',
};

typedef ApiClientFactory = ApiClient Function(ConnectionSettings settings);
typedef AttachmentPicker = Future<FilePickerResult?> Function();

ApiClient _defaultApiClientFactory(ConnectionSettings settings) =>
    ApiClient(settings);

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
    this.attachmentPicker = _defaultAttachmentPicker,
  });

  final SettingsRepository repository;
  final bool autoload;
  final ApiClientFactory clientFactory;
  final AttachmentPicker attachmentPicker;

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

  ApiClient? _client;
  ConnectionSettings? _connection;
  ServerSettings? _server;
  List<ConversationSummary> _summaries = const [];
  List<ConversationSummary>? _searchResults;
  String _tier = 'balanced';
  bool _debate = false;
  bool _synthesize = true;
  bool _blind = false;
  bool _webSearch = false;
  bool _loading = false;
  bool _uploadingAttachment = false;
  bool _searching = false;
  String _error = '';
  String _searchError = '';
  int _bootstrapEpoch = 0;
  int _searchEpoch = 0;
  Timer? _scrollTimer;
  Timer? _searchTimer;

  ConversationRecord? get _conversation => _selection.conversation;
  LiveTurn? get _liveTurn => _run.turn;
  String? get _selectedId => _selection.selectedId;
  bool get _loadingConversation => _selection.loading;
  bool get _sending => _run.sending;
  bool get _streamDisconnected => _run.disconnected;
  String? get _terminalReloadConversationId =>
      _run.terminalReloadConversationId;

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
    _searchEpoch++;
    _client?.close();
    _scrollTimer?.cancel();
    _searchTimer?.cancel();
    _messageController.dispose();
    _searchController
      ..removeListener(_scheduleSearch)
      ..dispose();
    _messageFocusNode.dispose();
    _searchFocusNode.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scheduleSearch({bool immediate = false}) {
    _searchTimer?.cancel();
    final query = _searchController.text.trim();
    final epoch = ++_searchEpoch;
    if (query.isEmpty) {
      if (!mounted) return;
      setState(() {
        _searchResults = null;
        _searching = false;
        _searchError = '';
      });
      return;
    }
    if (!mounted) return;
    setState(() {
      _searchResults = null;
      _searching = true;
      _searchError = '';
    });
    if (immediate) {
      unawaited(_runSearch(query, epoch));
    } else {
      _searchTimer = Timer(
        const Duration(milliseconds: 350),
        () => unawaited(_runSearch(query, epoch)),
      );
    }
  }

  Future<void> _runSearch(String query, int epoch) async {
    final client = _client;
    if (client == null) {
      if (!mounted || epoch != _searchEpoch) return;
      setState(() {
        _searchResults = const [];
        _searching = false;
        _searchError = 'バックエンドへ接続すると回答本文まで全文検索できます。';
      });
      return;
    }
    try {
      final result = await client.searchConversations(query, limit: 50);
      if (!mounted || epoch != _searchEpoch) return;
      setState(() {
        _searchResults = result.results;
        _searching = false;
        _searchError = '';
      });
    } catch (error) {
      if (!mounted || epoch != _searchEpoch) return;
      setState(() {
        _searchResults = const [];
        _searching = false;
        _searchError = '全文検索に失敗しました: $error';
      });
    }
  }

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
    try {
      final connection = await widget.repository.load();
      attemptedConnection = connection;
      candidate = widget.clientFactory(connection);
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
        _server = server;
        _summaries = summaries;
        _selection.restore(selectedId: selectedId, conversation: selected);
        _selectedProviders
          ..clear()
          ..addAll(selectedProviders);
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
      _searchEpoch++;
      _searchTimer?.cancel();
      setState(() {
        _connection = attemptedConnection;
        _server = null;
        _summaries = const [];
        _searchResults = null;
        _selectedProviders.clear();
        _loading = false;
        _searching = false;
        _error = 'バックエンドに接続できないため接続を無効化しました: $error';
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
          _MemoryEditorDialog(initialText: conversation.memory.text),
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
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => SettingsScreen(
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
      setState(() => _error = '先にバックエンドへ接続してください。');
      return;
    }
    await Navigator.of(context).push<void>(
      MaterialPageRoute(builder: (_) => UsageScreen(client: client)),
    );
  }

  Future<void> _showPolicyBlocked(PolicyScanResult policy) async {
    final labels = policy.findings
        .where((finding) => finding.severity == 'block')
        .map((finding) => finding.label)
        .toSet()
        .toList(growable: false);
    final redacted = policy.redactedText.trim();
    final replace = await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        icon: const Icon(Icons.gpp_bad_outlined),
        title: const Text('秘密情報らしい文字列を検出しました'),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              const Text('この文面は外部AIへ送信されません。検出理由:'),
              const SizedBox(height: 8),
              if (labels.isEmpty)
                const Text('• 秘密情報らしい文字列')
              else
                for (final label in labels) Text('• $label'),
              const SizedBox(height: 16),
              const Text(
                'マスク済み文面',
                style: TextStyle(fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 6),
              DecoratedBox(
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.surfaceContainerHighest,
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: SelectableText(
                    redacted.isEmpty ? '⟪REDACTED⟫' : redacted,
                  ),
                ),
              ),
              if (policy.disclaimer.isNotEmpty) ...[
                const SizedBox(height: 10),
                Text(
                  policy.disclaimer,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('送信せず戻る'),
          ),
          FilledButton.icon(
            onPressed: redacted.isEmpty
                ? null
                : () => Navigator.pop(context, true),
            icon: const Icon(Icons.find_replace_outlined),
            label: const Text('マスク済み文面へ置換'),
          ),
        ],
      ),
    );
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

  Future<bool> _confirmBillableRun(
    RunPlan plan,
    PolicyScanResult policy,
  ) async {
    final participants = plan.billableParticipants;
    final liveTokens = plan.maxOutputTokens['live_total'] ?? 0;
    final allCalls = plan.calls['total'] ?? 0;
    final retryEnvelope = plan.retryEnvelope;
    final personalDataLabels = policy.findings
        .where((finding) => finding.severity == 'confirm')
        .map((finding) => finding.label)
        .toSet()
        .toList(growable: false);
    return await showDialog<bool>(
          context: context,
          barrierDismissible: false,
          builder: (context) => AlertDialog(
            icon: const Icon(Icons.payments_outlined),
            title: const Text('実APIを使用します'),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('以下の外部API呼び出しは、各社との契約に応じて課金される可能性があります。'),
                  const SizedBox(height: 14),
                  for (final participant in participants)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 6),
                      child: Text(
                        '• ${participant.label} / ${participant.model}: '
                        '最大${participant.maxCalls}回',
                      ),
                    ),
                  const Divider(height: 24),
                  Text('課金対象APIの最大呼出回数: ${plan.maxLiveCalls}回'),
                  Text('会議全体の最大Provider呼出回数: $allCalls回'),
                  Text('課金対象呼出の最大出力token合計: $liveTokens'),
                  if (plan.inputEnvelope.liveWithRetries > 0)
                    Text(
                      '課金対象呼出の入力送信量（再試行込み）: '
                      '${_formatBytes(plan.inputEnvelope.liveWithRetries)}',
                    ),
                  const SizedBox(height: 8),
                  if (plan.costEstimate.available &&
                      plan.costEstimate.totalUsd != null) ...[
                    Text(
                      '利用者設定価格による最大見積: '
                      '\$${plan.costEstimate.totalUsd}',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                    if (plan.costEstimate.priceVersion != null)
                      Text(
                        '価格版: ${plan.costEstimate.priceVersion}',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                    if (plan.budget.configured) ...[
                      if (plan.budget.perRunLimitUsd != null)
                        Text('1会議上限: \$${plan.budget.perRunLimitUsd}'),
                      if (plan.budget.todayRemainingUsd != null)
                        Text('実行前の日次残り: \$${plan.budget.todayRemainingUsd}'),
                    ],
                    const SizedBox(height: 6),
                    const Text(
                      '金額は明示価格表と安全側token上限による推定で、請求書やcredit残高ではありません。',
                    ),
                  ] else
                    const Text(
                      '入力token数・料金・思考tokenはProvider仕様で変わるため、'
                      'この確認は金額上限を保証しません。',
                    ),
                  if (plan.inputEnvelope.disclaimer.isNotEmpty)
                    Text(
                      plan.inputEnvelope.disclaimer,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  if (retryEnvelope.hasRetries) ...[
                    const SizedBox(height: 10),
                    Text(
                      '再試行込み最大Provider実行回数: '
                      '${retryEnvelope.totalProviderExecutions}回 '
                      '（追加${retryEnvelope.additionalHttpAttempts}回）',
                    ),
                    Text(
                      '再試行込み最大出力token合計: '
                      '${retryEnvelope.maxOutputTokens}',
                    ),
                    if (retryEnvelope.disclaimer.isNotEmpty)
                      Text(
                        retryEnvelope.disclaimer,
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                  ],
                  if (personalDataLabels.isNotEmpty) ...[
                    const SizedBox(height: 14),
                    Text(
                      '送信前に確認してください: ${personalDataLabels.join('、')}',
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('キャンセル'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('確認して実行'),
              ),
            ],
          ),
        ) ??
        false;
  }

  Future<void> _send() async {
    final client = _client;
    final message = _messageController.text.trim();
    if (_sending ||
        _loadingConversation ||
        _uploadingAttachment ||
        _liveTurn != null ||
        message.isEmpty) {
      return;
    }
    if (client == null || _server == null) {
      setState(() => _error = '先に設定画面でバックエンドへ接続してください。');
      return;
    }
    final providers = _providerOrder
        .where(_selectedProviders.contains)
        .toList(growable: false);
    final conversationId = _selectedId;
    final tier = _tier;
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
      final effectivePolicy = scan.action != 'allow' ? scan : plan.policy;
      final blockedPolicy = scan.blocked
          ? scan
          : (plan.policy.blocked ? plan.policy : null);
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
        final confirmed = await _confirmBillableRun(plan, effectivePolicy);
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
      _messageController.clear();
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

  String _formatBytes(int bytes) {
    if (bytes < 1024) return '$bytes B';
    final kib = bytes / 1024;
    if (kib < 1024) return '${kib.toStringAsFixed(kib >= 100 ? 0 : 1)} KiB';
    final mib = kib / 1024;
    return '${mib.toStringAsFixed(mib >= 100 ? 0 : 2)} MiB';
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
    if (live == null || client == null) return;
    setState(() => live.phase = '停止をリクエスト中');
    try {
      final result = await client.cancelRun(live.requestId);
      if (!mounted || _liveTurn?.requestId != live.requestId) return;
      setState(() {
        if (result.terminalOutcome.isNotEmpty) live.error = '';
        live.phase = _cancelRunStatus(result);
      });
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(_cancelRunNotice(result))));
    } catch (error) {
      if (!mounted || _liveTurn?.requestId != live.requestId) return;
      setState(() {
        _error = '停止リクエストに失敗しました: $error';
      });
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
    final rawRequestTier = resume['tier']?.toString() ?? 'balanced';
    final requestTier = {'low', 'balanced', 'high'}.contains(rawRequestTier)
        ? rawRequestTier
        : 'balanced';
    final rawRequestedProviders = resume['providers'];
    final requestedProviders = rawRequestedProviders is List
        ? _providerOrder
              .where(
                rawRequestedProviders
                    .map((item) => item.toString())
                    .toSet()
                    .contains,
              )
              .toList(growable: false)
        : null;
    final attachmentIds = resume['attachment_ids'] is List
        ? (resume['attachment_ids'] as List)
              .map((item) => item.toString())
              .toList(growable: false)
        : const <String>[];
    final displayProviders = _providerOrder
        .where(turn.providers.toSet().contains)
        .toList(growable: false);
    final rawEffectiveTier = turn.options['tier']?.toString() ?? requestTier;
    final effectiveTier = {'low', 'balanced', 'high'}.contains(rawEffectiveTier)
        ? rawEffectiveTier
        : requestTier;
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
        tier: requestTier,
        debate: resume['debate'] == true,
        providers: requestedProviders,
        synthesize: resume['synthesize'] != false,
        blind: resume['blind'] == true,
        webSearch: resume['web_search'] == true,
        confirmLiveApi: resume['confirm_live_api'] == true,
        confirmSensitiveData: resume['confirm_sensitive_data'] == true,
        attachmentIds: attachmentIds,
      );
      if (!mounted) return;
      final live = LiveTurn(
        requestId: stream.requestId,
        message: turn.message.isNotEmpty ? turn.message : turn.cleanMessage,
        providers: displayProviders,
        tier: effectiveTier,
        debate: turn.options['debate'] == true,
        synthesize: turn.options['synthesize'] != false,
        blind: turn.options['blind'] == true,
        webSearch: turn.options['web_search'] == true,
        confirmedLiveApi: resume['confirm_live_api'] == true,
        confirmedSensitiveData: resume['confirm_sensitive_data'] == true,
        conversationId: stream.conversationId.isNotEmpty
            ? stream.conversationId
            : conversationId,
        attachmentIds: attachmentIds,
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
        ..showSnackBar(SnackBar(content: Text(_cancelRunNotice(result))));
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
      var confirmed = true;
      if (plan.billable) {
        confirmed = await _confirmBillableRun(plan, plan.policy);
      }
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

  PreferredSizeWidget _appBar(bool wide, bool compact) {
    final safeMock = _server != null && !_server!.liveApiEnabled;
    final settingsLocked =
        _sending || _uploadingAttachment || _liveTurn != null;
    return AppBar(
      titleSpacing: wide ? 20 : null,
      title: safeMock && compact
          ? const Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('Clage Cook'),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.lock_outline, size: 12),
                    SizedBox(width: 3),
                    Text('SAFE MOCK', style: TextStyle(fontSize: 10)),
                  ],
                ),
              ],
            )
          : const Text('Clage Cook'),
      actions: [
        if (_server != null && !compact)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 10),
            child: Tooltip(
              message: safeMock
                  ? '外部API呼び出しはサーバー側で施錠されています'
                  : '実APIの利用が許可されています',
              child: Chip(
                avatar: Icon(
                  safeMock
                      ? Icons.lock_outline
                      : _server!.mode == 'mock'
                      ? Icons.science_outlined
                      : Icons.cloud_done_outlined,
                  size: 17,
                ),
                label: Text(
                  safeMock ? 'SAFE MOCK' : _server!.mode.toUpperCase(),
                ),
                visualDensity: VisualDensity.compact,
              ),
            ),
          ),
        IconButton(
          tooltip: '利用状況と予算',
          onPressed: _openUsage,
          icon: const Icon(Icons.monitor_heart_outlined),
        ),
        IconButton(
          tooltip: '更新',
          onPressed: _loading || _uploadingAttachment ? null : _refresh,
          icon: const Icon(Icons.refresh),
        ),
        IconButton(
          tooltip: '接続とBYOK設定',
          onPressed: settingsLocked ? null : _openSettings,
          icon: const Icon(Icons.settings_outlined),
        ),
        const SizedBox(width: 4),
      ],
    );
  }

  Widget _sidebar(bool inDrawer) {
    final query = _searchController.text.trim();
    final summaries = query.isEmpty
        ? _summaries
        : _searchResults ?? const <ConversationSummary>[];
    return Column(
      children: [
        Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            children: [
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: _uploadingAttachment
                      ? null
                      : () {
                          if (inDrawer) Navigator.of(context).pop();
                          _newConversation();
                        },
                  icon: const Icon(Icons.add_comment_outlined),
                  label: const Text('新しい会話'),
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: _searchController,
                focusNode: _searchFocusNode,
                textInputAction: TextInputAction.search,
                maxLength: 200,
                maxLengthEnforcement: MaxLengthEnforcement.enforced,
                decoration: InputDecoration(
                  hintText: 'すべての回答を全文検索',
                  helperText: 'Ctrl/Cmd+K · 最大200文字',
                  prefixIcon: const Icon(Icons.search),
                  suffixIcon: query.isEmpty
                      ? null
                      : IconButton(
                          tooltip: 'クリア',
                          onPressed: _searchController.clear,
                          icon: const Icon(Icons.close),
                        ),
                  border: const OutlineInputBorder(),
                  isDense: true,
                ),
              ),
              if (_searching)
                const Padding(
                  padding: EdgeInsets.only(top: 8),
                  child: LinearProgressIndicator(minHeight: 2),
                ),
              if (_searchError.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        Icons.warning_amber_rounded,
                        size: 18,
                        color: Theme.of(context).colorScheme.error,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          _searchError,
                          style: Theme.of(context).textTheme.bodySmall,
                        ),
                      ),
                      IconButton(
                        tooltip: '再検索',
                        visualDensity: VisualDensity.compact,
                        onPressed: () => _scheduleSearch(immediate: true),
                        icon: const Icon(Icons.refresh, size: 18),
                      ),
                    ],
                  ),
                ),
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child: summaries.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.all(24),
                    child: Semantics(
                      liveRegion: true,
                      child: Text(
                        query.isEmpty
                            ? 'まだ会話がありません'
                            : _searching
                            ? '会話を検索しています…'
                            : '回答本文を含め、一致する会話がありません',
                        textAlign: TextAlign.center,
                      ),
                    ),
                  ),
                )
              : ListView.builder(
                  itemCount: summaries.length,
                  itemBuilder: (context, index) {
                    final item = summaries[index];
                    return ListTile(
                      selected: item.id == _selectedId,
                      leading: const Icon(Icons.forum_outlined),
                      title: Text(
                        item.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text(
                        '${item.turnCount}ターン · ${_formatDate(item.updatedAt)}\n${item.preview}',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      isThreeLine: true,
                      onTap: _uploadingAttachment
                          ? null
                          : () {
                              if (inDrawer) Navigator.of(context).pop();
                              unawaited(_selectConversation(item.id));
                            },
                      trailing: PopupMenuButton<String>(
                        tooltip: '会話メニュー',
                        enabled: !_uploadingAttachment,
                        onSelected: (action) {
                          if (inDrawer) Navigator.of(context).pop();
                          if (action == 'rename') {
                            unawaited(_renameConversation(item));
                          } else if (action == 'export') {
                            unawaited(
                              _copyConversationJson(item.id, item.title),
                            );
                          } else if (action == 'archive') {
                            unawaited(
                              _saveConversationArchive(item.id, item.title),
                            );
                          } else if (action == 'delete') {
                            unawaited(_deleteConversation(item));
                          }
                        },
                        itemBuilder: (_) => [
                          const PopupMenuItem(
                            value: 'rename',
                            child: ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.edit_outlined),
                              title: Text('タイトル変更'),
                            ),
                          ),
                          PopupMenuItem(
                            value: 'export',
                            enabled: !_exportingConversationIds.contains(
                              item.id,
                            ),
                            child: const ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.content_copy_outlined),
                              title: Text('JSONをコピー'),
                            ),
                          ),
                          PopupMenuItem(
                            value: 'archive',
                            enabled: !_exportingConversationIds.contains(
                              item.id,
                            ),
                            child: const ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.archive_outlined),
                              title: Text('ZIPを保存'),
                            ),
                          ),
                          const PopupMenuItem(
                            value: 'delete',
                            child: ListTile(
                              contentPadding: EdgeInsets.zero,
                              leading: Icon(Icons.delete_outline),
                              title: Text('削除'),
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }

  Widget _chatPane() {
    return Column(
      children: [
        if (_error.isNotEmpty) _errorBanner(_error),
        if (_streamDisconnected && _liveTurn != null)
          MaterialBanner(
            content: const Text('サーバー側で継続中の可能性があります。再接続または停止を選べます。'),
            actions: [
              TextButton(
                onPressed: _cancelLiveTurn,
                child: const Text('停止を要求'),
              ),
              TextButton(
                onPressed: _sending ? null : _resumeLiveTurn,
                child: const Text('再接続'),
              ),
            ],
          ),
        if (_terminalReloadConversationId != null)
          MaterialBanner(
            content: const Text('会議は終了しました。保存済み結果だけを再読み込みしてください。'),
            actions: [
              TextButton(
                onPressed: _loadingConversation
                    ? null
                    : _reloadTerminalConversation,
                child: const Text('保存結果を再読込'),
              ),
            ],
          ),
        Expanded(child: _turnList()),
        _composer(),
      ],
    );
  }

  Widget _turnList() {
    if (_loadingConversation) {
      return const Center(child: CircularProgressIndicator());
    }
    final live = _liveTurn;
    final turns = (_conversation?.turns ?? const <TurnRecord>[])
        .where((turn) => turn.requestId != live?.requestId)
        .toList(growable: false);
    final showLive =
        live != null &&
        (_selectedId == live.conversationId || live.conversationId.isEmpty);
    if (turns.isEmpty && !showLive) return _emptyState();
    return ListView(
      controller: _scrollController,
      padding: const EdgeInsets.fromLTRB(16, 18, 16, 32),
      children: [
        if (_conversation != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 16),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    _conversation!.title,
                    style: Theme.of(context).textTheme.headlineSmall,
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.outlined(
                  tooltip: '会話ZIPを保存',
                  onPressed:
                      _exportingConversationIds.contains(_conversation!.id)
                      ? null
                      : () => unawaited(
                          _saveConversationArchive(
                            _conversation!.id,
                            _conversation!.title,
                          ),
                        ),
                  icon: const Icon(Icons.archive_outlined),
                ),
                const SizedBox(width: 8),
                IconButton.outlined(
                  tooltip: _conversation!.memory.text.trim().isEmpty
                      ? 'ローカルメモを追加'
                      : 'ローカルメモを編集',
                  onPressed: _sending ? null : _editConversationMemory,
                  icon: Icon(
                    _conversation!.memory.text.trim().isEmpty
                        ? Icons.note_add_outlined
                        : Icons.sticky_note_2,
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.outlined(
                  tooltip: '会話JSONをコピー',
                  onPressed:
                      _exportingConversationIds.contains(_conversation!.id)
                      ? null
                      : () => unawaited(
                          _copyConversationJson(
                            _conversation!.id,
                            _conversation!.title,
                          ),
                        ),
                  icon: _exportingConversationIds.contains(_conversation!.id)
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.content_copy_outlined),
                ),
              ],
            ),
          ),
        for (final turn in turns) ...[
          SavedTurnView(
            key: ValueKey(turn.requestId),
            turn: turn,
            actionPending: _savedRunActionIds.contains(turn.requestId),
            onReconnect: turn.status == 'running'
                ? () => unawaited(_reconnectSavedTurn(turn))
                : null,
            onCancel: turn.status == 'running'
                ? () => unawaited(_cancelSavedTurn(turn))
                : null,
            regenerationPending: _regenerationActionIds.any(
              (id) => id.startsWith('${turn.requestId}:'),
            ),
            onRegenerateAnswer: turn.status == 'completed'
                ? (provider) => unawaited(
                    _regenerateSavedTurn(
                      turn,
                      target: 'answer',
                      provider: provider,
                    ),
                  )
                : null,
            onRegenerateSynthesis:
                turn.status == 'completed' && !turn.synthesis.skipped
                ? () =>
                      unawaited(_regenerateSavedTurn(turn, target: 'synthesis'))
                : null,
            onForkEdit: turn.status == 'completed'
                ? () => unawaited(_forkEditTurn(turn))
                : null,
          ),
          const SizedBox(height: 24),
          const Divider(),
          const SizedBox(height: 20),
        ],
        if (showLive) LiveTurnView(key: ValueKey(live.requestId), turn: live),
      ],
    );
  }

  Widget _emptyState() {
    final theme = Theme.of(context);
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(28),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 620),
          child: Column(
            children: [
              Icon(
                Icons.groups_2_outlined,
                size: 76,
                color: theme.colorScheme.primary,
              ),
              const SizedBox(height: 18),
              Text('4つのAIを、1つの会議へ。', style: theme.textTheme.headlineSmall),
              const SizedBox(height: 10),
              const Text(
                'Claude・Gemini・ChatGPT・Grokが並列で回答し、'
                '最後に1つの結論へ統合します。APIキーがない間はモックデモで動きます。',
                textAlign: TextAlign.center,
              ),
              if (_server == null) ...[
                const SizedBox(height: 18),
                FilledButton.icon(
                  onPressed: _sending || _liveTurn != null
                      ? null
                      : _openSettings,
                  icon: const Icon(Icons.settings_ethernet),
                  label: const Text('バックエンドに接続'),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _composer() {
    final theme = Theme.of(context);
    final active = _server?.activeWorkers ?? const <String>[];
    final runActive = _liveTurn != null;
    final controlsDisabled =
        _sending || _loadingConversation || _uploadingAttachment || runActive;
    return Material(
      elevation: 8,
      color: theme.colorScheme.surfaceContainer,
      child: SafeArea(
        top: false,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 10, 12, 12),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1000),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: [
                      SegmentedButton<String>(
                        segments: const [
                          ButtonSegment(value: 'low', label: Text('LOW')),
                          ButtonSegment(
                            value: 'balanced',
                            label: Text('BALANCED'),
                          ),
                          ButtonSegment(value: 'high', label: Text('HIGH')),
                        ],
                        selected: {_tier},
                        onSelectionChanged: controlsDisabled
                            ? null
                            : (value) => setState(() => _tier = value.first),
                        showSelectedIcon: false,
                      ),
                      const SizedBox(width: 8),
                      FilterChip(
                        label: const Text('DEBATE'),
                        avatar: const Icon(Icons.forum_outlined, size: 18),
                        selected: _debate,
                        onSelected: controlsDisabled
                            ? null
                            : (value) => setState(() => _debate = value),
                      ),
                      const SizedBox(width: 6),
                      FilterChip(
                        label: const Text('統合'),
                        avatar: const Icon(Icons.auto_awesome, size: 18),
                        selected: _synthesize,
                        onSelected: controlsDisabled
                            ? null
                            : (value) => setState(() => _synthesize = value),
                      ),
                      const SizedBox(width: 6),
                      FilterChip(
                        label: const Text('BLIND'),
                        avatar: const Icon(
                          Icons.visibility_off_outlined,
                          size: 18,
                        ),
                        selected: _blind,
                        onSelected: controlsDisabled
                            ? null
                            : (value) => setState(() => _blind = value),
                      ),
                      const SizedBox(width: 6),
                      FilterChip(
                        label: const Text('WEB'),
                        avatar: const Icon(Icons.public, size: 18),
                        selected: _webSearch,
                        onSelected:
                            controlsDisabled ||
                                !(_server?.webSearch.enabled ?? false)
                            ? null
                            : (value) => setState(() => _webSearch = value),
                      ),
                      const SizedBox(width: 10),
                      for (final provider in _providerOrder.where(
                        active.contains,
                      )) ...[
                        FilterChip(
                          label: Text(_providerLabels[provider] ?? provider),
                          selected: _selectedProviders.contains(provider),
                          onSelected: controlsDisabled
                              ? null
                              : (value) => setState(() {
                                  if (value) {
                                    _selectedProviders.add(provider);
                                  } else {
                                    _selectedProviders.remove(provider);
                                  }
                                }),
                        ),
                        const SizedBox(width: 6),
                      ],
                    ],
                  ),
                ),
                if (_debate)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      'DEBATEは回答者をもう1度呼び出すため、利用量と待ち時間が増えます。',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                if (_blind)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      'BLINDは相互批評と統合へAI名を渡さず、ブランド先入観を減らします。',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                if (_webSearch)
                  Padding(
                    padding: const EdgeInsets.only(top: 6),
                    child: Text(
                      'WEBは初回回答にサーバー側検索を許可します。検索tool分の利用量や料金が増える場合があります。',
                      style: theme.textTheme.bodySmall,
                    ),
                  ),
                if (_pendingAttachments.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: [
                      for (final attachment in _pendingAttachments)
                        InputChip(
                          avatar: Icon(
                            attachment.kind == 'image'
                                ? Icons.image_outlined
                                : attachment.kind == 'pdf'
                                ? Icons.picture_as_pdf_outlined
                                : Icons.description_outlined,
                            size: 17,
                          ),
                          label: Text(
                            '${attachment.name} · ${_formatBytes(attachment.sizeBytes)}',
                          ),
                          onDeleted: controlsDisabled || _uploadingAttachment
                              ? null
                              : () => unawaited(
                                  _removePendingAttachment(attachment),
                                ),
                        ),
                    ],
                  ),
                ],
                const SizedBox(height: 8),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    IconButton(
                      tooltip: '添付を追加',
                      onPressed: controlsDisabled || _uploadingAttachment
                          ? null
                          : _pickAttachments,
                      icon: _uploadingAttachment
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.attach_file),
                    ),
                    const SizedBox(width: 4),
                    Expanded(
                      child: TextField(
                        controller: _messageController,
                        focusNode: _messageFocusNode,
                        minLines: 1,
                        maxLines: 6,
                        enabled: !controlsDisabled,
                        textCapitalization: TextCapitalization.sentences,
                        decoration: const InputDecoration(
                          hintText: '質問を入力…',
                          helperText: '下書きはサーバーが受理するまで消えません',
                          border: OutlineInputBorder(),
                        ),
                      ),
                    ),
                    const SizedBox(width: 8),
                    IconButton.filled(
                      tooltip: runActive ? '会議を停止' : '会議を開始',
                      onPressed: runActive
                          ? _cancelLiveTurn
                          : (controlsDisabled ? null : _send),
                      icon: runActive
                          ? const Icon(Icons.stop)
                          : _sending
                          ? const Icon(Icons.hourglass_top)
                          : const Icon(Icons.send),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _errorBanner(String message) {
    final colors = Theme.of(context).colorScheme;
    return Material(
      color: colors.errorContainer,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
        child: Row(
          children: [
            Icon(Icons.error_outline, color: colors.onErrorContainer),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                message,
                style: TextStyle(color: colors.onErrorContainer),
              ),
            ),
            IconButton(
              tooltip: '閉じる',
              onPressed: () => setState(() => _error = ''),
              icon: Icon(Icons.close, color: colors.onErrorContainer),
            ),
          ],
        ),
      ),
    );
  }
}

class _MemoryEditorDialog extends StatefulWidget {
  const _MemoryEditorDialog({required this.initialText});

  final String initialText;

  @override
  State<_MemoryEditorDialog> createState() => _MemoryEditorDialogState();
}

class _MemoryEditorDialogState extends State<_MemoryEditorDialog> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialText);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('ローカルメモ'),
    content: SizedBox(
      width: 620,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('会話の目的・制約・用語などを保存します。各ターンの初回回答へ参考データとして渡されます。'),
            const SizedBox(height: 10),
            TextField(
              controller: _controller,
              minLines: 6,
              maxLines: 14,
              maxLength: 20000,
              autofocus: true,
              decoration: const InputDecoration(
                hintText: '例: 対象読者、採用済み方針、避けるべき案…',
                border: OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() {}),
            ),
            const Text('APIキーなどの秘密候補は保存時にマスクされます。'),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('キャンセル'),
      ),
      FilledButton(
        onPressed: () => Navigator.pop(context, _controller.text),
        child: Text(_controller.text.trim().isEmpty ? 'クリア' : '保存'),
      ),
    ],
  );
}

String _cancelRunStatus(CancelRunResult result) =>
    switch (result.terminalOutcome) {
      'completed' => '完了が先に確定しました',
      'failed' => '停止前に処理失敗が確定しました',
      'cancelled' => result.alreadyDone ? 'すでに停止しています' : 'ローカル停止処理が完了しました',
      _ =>
        result.alreadyDone
            ? 'すでに処理は終了しています'
            : (result.cancelled ? 'ローカル停止処理が完了しました' : '停止要求済み'),
    };

String _cancelRunNotice(CancelRunResult result) =>
    switch (result.terminalOutcome) {
      'completed' => '停止要求より先に会議の完了が確定しました。保存済み結果を確認できます。',
      'failed' => '停止要求より先に処理失敗が確定しました。保存済みの実行状態を確認してください。',
      _ =>
        result.warning.isNotEmpty
            ? result.warning
            : '停止要求後も、外部Provider側の処理や課金が続く可能性があります。',
    };

String _formatDate(String value) {
  final parsed = DateTime.tryParse(value)?.toLocal();
  if (parsed == null) return '';
  String two(int number) => number.toString().padLeft(2, '0');
  return '${parsed.year}/${two(parsed.month)}/${two(parsed.day)} '
      '${two(parsed.hour)}:${two(parsed.minute)}';
}
