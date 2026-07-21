import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../models.dart';
import '../services/direct_provider_client.dart';
import '../services/direct_settings_store.dart';
import '../services/settings_store.dart';
import 'settings_screen.dart';

class AppSettingsScreen extends StatefulWidget {
  const AppSettingsScreen({
    super.key,
    required this.directRepository,
    required this.serverRepository,
    required this.initialDirect,
    required this.initialServer,
    this.initialServerSettings,
    this.allowReferenceServer = !kReleaseMode || kIsWeb,
  });

  final DirectSettingsRepository directRepository;
  final SettingsRepository serverRepository;
  final DirectSettings initialDirect;
  final ConnectionSettings initialServer;
  final ServerSettings? initialServerSettings;
  final bool allowReferenceServer;

  @override
  State<AppSettingsScreen> createState() => _AppSettingsScreenState();
}

class _AppSettingsScreenState extends State<AppSettingsScreen> {
  late ExecutionMode _mode;
  late ReasoningMode _reasoningMode;
  late bool _showTokenUsageLedger;
  late bool _showLiveApiConfirmation;
  late DirectProvider? _synthesizer;
  late final Map<DirectProvider, TextEditingController> _keyControllers;
  late final Map<DirectProvider, TextEditingController> _modelControllers;
  late final Map<DirectProvider, String> _modelSelections;
  final Set<DirectProvider> _clearedKeys = {};
  var _saving = false;
  String _error = '';

  @override
  void initState() {
    super.initState();
    _mode =
        !widget.allowReferenceServer &&
            widget.initialDirect.executionMode == ExecutionMode.referenceServer
        ? ExecutionMode.directByok
        : widget.initialDirect.executionMode;
    _reasoningMode = widget.initialDirect.reasoningMode;
    _showTokenUsageLedger = widget.initialDirect.showTokenUsageLedger;
    _showLiveApiConfirmation = widget.initialDirect.showLiveApiConfirmation;
    _synthesizer = widget.initialDirect.synthesizerProvider;
    _keyControllers = {
      for (final provider in DirectProvider.values)
        provider: TextEditingController(),
    };
    _modelControllers = {
      for (final provider in DirectProvider.values)
        provider: TextEditingController(
          text: widget.initialDirect.modelOverrideFor(provider),
        ),
    };
    _modelSelections = {
      for (final provider in DirectProvider.values)
        provider: _modelSelectionFor(
          provider,
          widget.initialDirect.modelOverrideFor(provider),
        ),
    };
  }

  @override
  void dispose() {
    for (final controller in [
      ..._keyControllers.values,
      ..._modelControllers.values,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _save() async {
    if (!widget.allowReferenceServer &&
        _mode == ExecutionMode.referenceServer) {
      setState(() => _error = '配布版では開発用サーバーへ切り替えられません。');
      return;
    }
    if (kIsWeb && _mode == ExecutionMode.directByok) {
      setState(() {
        _error = 'Web版はAPIキーを安全に保持できないためDirect BYOKを有効化できません。';
      });
      return;
    }
    final keys = <DirectProvider, String>{};
    for (final provider in DirectProvider.values) {
      final replacement = _keyControllers[provider]!.text.trim();
      keys[provider] = _clearedKeys.contains(provider)
          ? ''
          : replacement.isNotEmpty
          ? replacement
          : widget.initialDirect.apiKeyFor(provider);
    }
    if (_mode == ExecutionMode.directByok &&
        keys.values.every((value) => value.isEmpty)) {
      setState(() => _error = 'Direct BYOKには1つ以上のAPIキーが必要です。');
      return;
    }
    setState(() {
      _saving = true;
      _error = '';
    });
    try {
      await widget.directRepository.save(
        DirectSettings(
          executionMode: _mode,
          reasoningMode: _reasoningMode,
          showTokenUsageLedger: _showTokenUsageLedger,
          showLiveApiConfirmation: _showLiveApiConfirmation,
          claudeApiKey: keys[DirectProvider.claude]!,
          chatGptApiKey: keys[DirectProvider.chatgpt]!,
          geminiApiKey: keys[DirectProvider.gemini]!,
          grokApiKey: keys[DirectProvider.grok]!,
          claudeModelOverride: _modelControllers[DirectProvider.claude]!.text
              .trim(),
          chatGptModelOverride: _modelControllers[DirectProvider.chatgpt]!.text
              .trim(),
          geminiModelOverride: _modelControllers[DirectProvider.gemini]!.text
              .trim(),
          grokModelOverride: _modelControllers[DirectProvider.grok]!.text
              .trim(),
          synthesizerProvider: _synthesizer,
        ),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (error) {
      if (mounted) setState(() => _error = '保存できませんでした: $error');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _openServerSettings() async {
    final changed = await Navigator.of(context).push<bool>(
      MaterialPageRoute(
        builder: (_) => SettingsScreen(
          repository: widget.serverRepository,
          initial: widget.initialServer,
          initialServerSettings: widget.initialServerSettings,
        ),
      ),
    );
    if (changed == true && mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(const SnackBar(content: Text('開発用サーバー接続を保存しました。')));
    }
  }

  Future<void> _clearAllKeys() async {
    final confirmed =
        await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('保存済みAPIキーを削除'),
            content: const Text('4社すべてのAPIキーを端末のSecure Storageから削除します。'),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('キャンセル'),
              ),
              FilledButton(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('すべて削除'),
              ),
            ],
          ),
        ) ??
        false;
    if (!confirmed) return;
    try {
      await widget.directRepository.clearAllKeys();
      if (!mounted) return;
      setState(() {
        _clearedKeys.addAll(DirectProvider.values);
        for (final controller in _keyControllers.values) {
          controller.clear();
        }
      });
    } catch (error) {
      if (mounted) setState(() => _error = 'APIキーを削除できませんでした: $error');
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final direct = _mode == ExecutionMode.directByok;
    return Scaffold(
      appBar: AppBar(title: const Text('接続とBYOK設定')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text('実行方式', style: theme.textTheme.titleLarge),
          const SizedBox(height: 12),
          if (widget.allowReferenceServer)
            SegmentedButton<ExecutionMode>(
              segments: const [
                ButtonSegment(
                  value: ExecutionMode.directByok,
                  icon: Icon(Icons.phone_android),
                  label: Text('Direct BYOK'),
                ),
                ButtonSegment(
                  value: ExecutionMode.referenceServer,
                  icon: Icon(Icons.developer_mode),
                  label: Text('開発用サーバー'),
                ),
              ],
              selected: {_mode},
              onSelectionChanged: _saving
                  ? null
                  : (values) => setState(() {
                      _mode = values.first;
                      _error = '';
                    }),
            )
          else
            _SettingsNotice(
              icon: Icons.verified_user_outlined,
              color: theme.colorScheme.secondaryContainer,
              text: '配布版はDirect BYOK専用です。開発用サーバー切替はDebug/Profileビルドだけで利用できます。',
            ),
          const SizedBox(height: 12),
          _SettingsNotice(
            icon: direct ? Icons.lock_outline : Icons.science_outlined,
            color: direct
                ? theme.colorScheme.secondaryContainer
                : theme.colorScheme.tertiaryContainer,
            text: direct
                ? 'APIキーだけで各社へ直接接続します。会話はこの端末だけに保存されます。'
                : 'UI比較・検証用です。このリポジトリのOSS FastAPIサーバーへ接続します。',
          ),
          if (kIsWeb && direct) ...[
            const SizedBox(height: 12),
            _SettingsNotice(
              icon: Icons.warning_amber_rounded,
              color: theme.colorScheme.errorContainer,
              text:
                  'Web版のDirect BYOKは、ブラウザからのキー抽出と各社CORS制約を避けられないため無効です。Android・iOS・Desktopを使用してください。',
            ),
          ],
          ..._conferenceDefaultFields(theme),
          if (direct) ..._directFields(theme) else ..._serverFields(theme),
          if (_error.isNotEmpty) ...[
            const SizedBox(height: 12),
            _SettingsNotice(
              icon: Icons.error_outline,
              color: theme.colorScheme.errorContainer,
              text: _error,
            ),
          ],
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: _saving ? null : _save,
            icon: _saving
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.save_outlined),
            label: const Text('実行方式を保存'),
          ),
        ],
      ),
    );
  }

  List<Widget> _conferenceDefaultFields(ThemeData theme) => [
    const SizedBox(height: 28),
    Text('会議の既定値', style: theme.textTheme.titleLarge),
    const SizedBox(height: 12),
    DropdownButtonFormField<ReasoningMode>(
      initialValue: _reasoningMode,
      decoration: const InputDecoration(
        labelText: '既定の推論エフォート',
        border: OutlineInputBorder(),
      ),
      items: const [
        DropdownMenuItem(
          value: ReasoningMode.auto,
          child: Text('AUTO（モデル推奨値）'),
        ),
        DropdownMenuItem(value: ReasoningMode.low, child: Text('LOW')),
        DropdownMenuItem(value: ReasoningMode.medium, child: Text('MEDIUM')),
        DropdownMenuItem(value: ReasoningMode.high, child: Text('HIGH')),
      ],
      onChanged: (value) {
        if (value != null) setState(() => _reasoningMode = value);
      },
    ),
    const SizedBox(height: 6),
    const Text(
      'AUTOは質問内容を分類せず、選択モデルに対する固定推奨エフォートを使います。'
      '会話画面では、この設定を使うかLOW〜HIGHで一時的に上書きできます。',
    ),
    const SizedBox(height: 24),
    Text('表示', style: theme.textTheme.titleLarge),
    const SizedBox(height: 8),
    Card(
      margin: EdgeInsets.zero,
      child: SwitchListTile.adaptive(
        key: const ValueKey('show-token-usage-ledger'),
        value: _showTokenUsageLedger,
        onChanged: _saving
            ? null
            : (value) => setState(() => _showTokenUsageLedger = value),
        secondary: const Icon(Icons.receipt_long_outlined),
        title: const Text('トークン利用量台帳を表示'),
        subtitle: const Text(
          '会話内にProvider実測値の折りたたみ台帳を表示します。'
          'OFFでもusageの取得・端末保存・エクスポートは続きます。',
        ),
      ),
    ),
    const SizedBox(height: 8),
    Card(
      margin: EdgeInsets.zero,
      child: SwitchListTile.adaptive(
        key: const ValueKey('show-live-api-confirmation'),
        value: _showLiveApiConfirmation,
        onChanged: _saving
            ? null
            : (value) => setState(() => _showLiveApiConfirmation = value),
        secondary: const Icon(Icons.payments_outlined),
        title: const Text('「実APIを使用します」の確認を表示'),
        subtitle: const Text(
          'OFFにすると通常の課金可能性の確認だけを省略します。'
          '秘密情報・個人情報などpolicy上必要な確認は常に表示します。',
        ),
      ),
    ),
  ];

  List<Widget> _directFields(ThemeData theme) => [
    const SizedBox(height: 28),
    _SettingsNotice(
      icon: Icons.outbound_outlined,
      color: theme.colorScheme.surfaceContainerHighest,
      text:
          '選択した各社へ質問・添付・会議用の回答を端末から直接送信します。各社で料金とデータ取扱いが異なります。Clage Cookの開発者サーバーは通信を中継しません。',
    ),
    const SizedBox(height: 18),
    Text('AIプロバイダー', style: theme.textTheme.titleLarge),
    const SizedBox(height: 4),
    const Text(
      '閉じたままキー・接続確認・モデルの概要を確認できます。'
      '保存済みキーは画面へ読み戻さず、新規入力も常にマスク表示します。',
    ),
    const SizedBox(height: 12),
    for (final provider in _providerUiOrder) ...[
      _providerEditor(provider),
      const SizedBox(height: 12),
    ],
    Align(
      alignment: Alignment.centerLeft,
      child: TextButton.icon(
        onPressed: _clearAllKeys,
        icon: const Icon(Icons.key_off_outlined),
        label: const Text('保存済みキーをすべて削除'),
      ),
    ),
    const SizedBox(height: 18),
    Text('Direct BYOKの統合役', style: theme.textTheme.titleLarge),
    const SizedBox(height: 12),
    DropdownButtonFormField<DirectProvider?>(
      initialValue: _synthesizer,
      decoration: const InputDecoration(
        labelText: '統合役',
        border: OutlineInputBorder(),
      ),
      items: [
        const DropdownMenuItem<DirectProvider?>(value: null, child: Text('自動')),
        for (final provider in _providerUiOrder)
          DropdownMenuItem<DirectProvider?>(
            value: provider,
            child: Text(DirectProviderClient.labels[provider]!),
          ),
      ],
      onChanged: (value) => setState(() => _synthesizer = value),
    ),
  ];

  List<Widget> _serverFields(ThemeData theme) => [
    const SizedBox(height: 28),
    Text('開発用サーバー', style: theme.textTheme.titleLarge),
    const SizedBox(height: 8),
    Text(widget.initialServer.baseUrl),
    const SizedBox(height: 12),
    OutlinedButton.icon(
      onPressed: _openServerSettings,
      icon: const Icon(Icons.settings_ethernet),
      label: const Text('サーバー接続・モデル詳細を開く'),
    ),
    const SizedBox(height: 12),
    _SettingsNotice(
      icon: Icons.info_outline,
      color: theme.colorScheme.surfaceContainerHighest,
      text: 'この切替は開発中のUI照合用です。Direct BYOKの端末内履歴とは混ざりません。',
    ),
  ];

  Widget _providerEditor(DirectProvider provider) {
    final hasSavedKey =
        widget.initialDirect.hasKey(provider) &&
        !_clearedKeys.contains(provider);
    final hasDraftKey = _keyControllers[provider]!.text.trim().isNotEmpty;
    final configured = hasSavedKey || hasDraftKey;
    final label = DirectProviderClient.labels[provider]!;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: ExpansionTile(
        key: ValueKey('direct-provider-${provider.name}'),
        maintainState: false,
        dense: true,
        tilePadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 2),
        childrenPadding: const EdgeInsets.fromLTRB(14, 0, 14, 14),
        title: Row(
          children: [
            Expanded(
              child: Text(
                label,
                style: Theme.of(context).textTheme.titleMedium,
              ),
            ),
            _ProviderStatusBadge(
              icon: configured ? Icons.key_outlined : Icons.key_off_outlined,
              text: hasDraftKey
                  ? 'キー入力済み'
                  : hasSavedKey
                  ? 'キー設定済み'
                  : 'キー未設定',
              positive: configured,
            ),
          ],
        ),
        subtitle: Padding(
          padding: const EdgeInsets.only(top: 2),
          child: Text(
            '${configured ? '接続: 未確認' : '接続: キー待ち'}'
            '  •  モデル: ${_modelSummary(provider)}',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: Text(
              '直接送信先: ${DirectProviderClient.endpointHosts[provider]}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          const SizedBox(height: 10),
          TextField(
            key: ValueKey('direct-provider-key-${provider.name}'),
            controller: _keyControllers[provider],
            obscureText: true,
            autocorrect: false,
            enableSuggestions: false,
            decoration: InputDecoration(
              labelText: '$label APIキー（更新時だけ入力）',
              helperText: hasSavedKey ? '空欄のままなら保存済み値を維持' : null,
              border: const OutlineInputBorder(),
              prefixIcon: const Icon(Icons.key_outlined),
              suffixIcon: configured
                  ? IconButton(
                      tooltip: 'このキーを削除予定にする',
                      onPressed: () => setState(() {
                        _clearedKeys.add(provider);
                        _keyControllers[provider]!.clear();
                      }),
                      icon: const Icon(Icons.clear),
                    )
                  : null,
            ),
            onChanged: (value) => setState(() {
              if (value.trim().isNotEmpty) {
                _clearedKeys.remove(provider);
              }
            }),
          ),
          const SizedBox(height: 14),
          DropdownButtonFormField<String>(
            key: ValueKey(
              'direct-provider-model-${provider.name}-${_modelSelections[provider]}',
            ),
            initialValue: _modelSelections[provider],
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'モデル',
              border: OutlineInputBorder(),
              prefixIcon: Icon(Icons.memory_outlined),
            ),
            items: [
              const DropdownMenuItem(
                value: _tierDefaultModelChoice,
                child: Text('品質別の既定モデル（推奨）'),
              ),
              for (final model in _knownModelsFor(provider))
                DropdownMenuItem(
                  value: model,
                  child: Text(_knownModelLabel(provider, model)),
                ),
              const DropdownMenuItem(
                value: _customModelChoice,
                child: Text('詳細: カスタムmodel ID'),
              ),
            ],
            onChanged: (value) {
              if (value == null) return;
              setState(() {
                final previous = _modelSelections[provider];
                _modelSelections[provider] = value;
                if (value == _tierDefaultModelChoice) {
                  _modelControllers[provider]!.clear();
                } else if (value == _customModelChoice) {
                  if (previous != _customModelChoice) {
                    _modelControllers[provider]!.clear();
                  }
                } else {
                  _modelControllers[provider]!.text = value;
                }
              });
            },
          ),
          if (_modelSelections[provider] == _customModelChoice) ...[
            const SizedBox(height: 10),
            TextField(
              key: ValueKey('direct-provider-custom-model-${provider.name}'),
              controller: _modelControllers[provider],
              autocorrect: false,
              enableSuggestions: false,
              decoration: InputDecoration(
                labelText: '$label custom model ID',
                hintText: DirectProviderClient.modelFor(provider, 'balanced'),
                helperText: '公式APIで利用できるIDのみ入力してください。',
                border: const OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() {}),
            ),
          ],
        ],
      ),
    );
  }

  String _modelSummary(DirectProvider provider) {
    final selection = _modelSelections[provider]!;
    if (selection == _tierDefaultModelChoice) {
      final balanced = DirectProviderClient.modelFor(provider, 'balanced');
      return '品質別（BALANCED: $balanced）';
    }
    if (selection == _customModelChoice) {
      final custom = _modelControllers[provider]!.text.trim();
      return custom.isEmpty ? 'カスタムID未入力' : 'カスタム・$custom';
    }
    return '固定・$selection';
  }
}

const _tierDefaultModelChoice = '__tier_default__';
const _customModelChoice = '__custom__';

String _modelSelectionFor(DirectProvider provider, String override) {
  final normalized = override.trim();
  if (normalized.isEmpty) return _tierDefaultModelChoice;
  return _knownModelsFor(provider).contains(normalized)
      ? normalized
      : _customModelChoice;
}

List<String> _knownModelsFor(DirectProvider provider) =>
    DirectProviderClient.defaultModels[provider]!.values.toSet().toList();

String _knownModelLabel(DirectProvider provider, String model) {
  const tierLabels = {'low': 'LOW', 'balanced': 'BALANCED', 'high': 'HIGH'};
  final tiers = DirectProviderClient.defaultModels[provider]!.entries
      .where((entry) => entry.value == model)
      .map((entry) => tierLabels[entry.key]!)
      .join(' / ');
  return '$model（$tiers）';
}

const _providerUiOrder = [
  DirectProvider.claude,
  DirectProvider.gemini,
  DirectProvider.chatgpt,
  DirectProvider.grok,
];

class _ProviderStatusBadge extends StatelessWidget {
  const _ProviderStatusBadge({
    required this.icon,
    required this.text,
    required this.positive,
  });

  final IconData icon;
  final String text;
  final bool positive;

  @override
  Widget build(BuildContext context) {
    final colors = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
      decoration: BoxDecoration(
        color: positive
            ? colors.secondaryContainer
            : colors.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 14),
          const SizedBox(width: 4),
          Text(text, style: Theme.of(context).textTheme.labelSmall),
        ],
      ),
    );
  }
}

class _SettingsNotice extends StatelessWidget {
  const _SettingsNotice({
    required this.icon,
    required this.color,
    required this.text,
  });

  final IconData icon;
  final Color color;
  final String text;

  @override
  Widget build(BuildContext context) => Container(
    padding: const EdgeInsets.all(12),
    decoration: BoxDecoration(
      color: color,
      borderRadius: BorderRadius.circular(12),
    ),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 20),
        const SizedBox(width: 10),
        Expanded(child: Text(text)),
      ],
    ),
  );
}
