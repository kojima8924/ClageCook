import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';

import '../models.dart';
import '../services/api_client.dart';
import '../services/settings_store.dart';

typedef SettingsApiClientFactory =
    ApiClient Function(ConnectionSettings settings);

ApiClient _defaultSettingsApiClientFactory(ConnectionSettings settings) =>
    ApiClient(settings);

const _webTokenStorageWarning =
    'Web版ではBearerトークンはブラウザ内ストレージへ保存されます。'
    '共有端末、悪意ある拡張機能、XSSからは保護されません。';

@visibleForTesting
String webTokenStorageWarning({bool? isWeb}) =>
    (isWeb ?? kIsWeb) ? _webTokenStorageWarning : '';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({
    super.key,
    required this.repository,
    required this.initial,
    this.initialServerSettings,
    this.clientFactory = _defaultSettingsApiClientFactory,
  });

  final SettingsRepository repository;
  final ConnectionSettings initial;
  final ServerSettings? initialServerSettings;
  final SettingsApiClientFactory clientFactory;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  late final TextEditingController _urlController;
  late final TextEditingController _tokenController;
  ServerSettings? _serverSettings;
  ConnectionSettings? _serverSettingsConnection;
  bool _testing = false;
  bool _saving = false;
  bool _editingRuntime = false;
  bool _obscureToken = true;
  String _result = '';
  bool _resultOk = false;

  @override
  void initState() {
    super.initState();
    _urlController = TextEditingController(text: widget.initial.baseUrl);
    _tokenController = TextEditingController(text: widget.initial.token);
    _serverSettings = widget.initialServerSettings;
    if (widget.initialServerSettings != null) {
      _serverSettingsConnection = _normalizedConnection(widget.initial);
    }
  }

  @override
  void dispose() {
    _urlController.dispose();
    _tokenController.dispose();
    super.dispose();
  }

  ConnectionSettings get _current => ConnectionSettings(
    baseUrl: _urlController.text.trim(),
    token: _tokenController.text.trim(),
  );

  static ConnectionSettings _normalizedConnection(
    ConnectionSettings connection,
  ) => ConnectionSettings(
    baseUrl: normalizeServerBaseUrl(connection.baseUrl),
    token: connection.token.trim(),
  );

  static bool _sameConnection(
    ConnectionSettings left,
    ConnectionSettings right,
  ) => _sameBaseUrl(left, right) && left.token.trim() == right.token.trim();

  static bool _sameBaseUrl(ConnectionSettings left, ConnectionSettings right) =>
      normalizeServerBaseUrl(left.baseUrl) ==
      normalizeServerBaseUrl(right.baseUrl);

  Future<void> _test() async {
    final error = validateServerBaseUrl(_current.baseUrl);
    if (error != null) {
      setState(() {
        _result = error;
        _resultOk = false;
      });
      return;
    }
    final testedConnection = _normalizedConnection(_current);
    setState(() {
      _testing = true;
      _result = '';
    });
    final client = widget.clientFactory(testedConnection);
    try {
      final health = await client.health();
      final settings = await client.serverSettings();
      if (!mounted) return;
      if (!_sameConnection(_current, testedConnection)) {
        setState(() {
          _resultOk = false;
          _result = '接続テスト中にURLまたはトークンが変更されました。もう1度接続テストしてください。';
        });
        return;
      }
      setState(() {
        _serverSettings = settings;
        _serverSettingsConnection = testedConnection;
        _resultOk = true;
        _result =
            '接続成功: ${settings.liveApiEnabled ? 'LIVE API有効' : 'SAFE MOCK'} / '
            'mode=${health['mode']} / 統合役=${health['synthesizer']}';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _resultOk = false;
        _result = '接続失敗: $error';
      });
    } finally {
      client.close();
      if (mounted) setState(() => _testing = false);
    }
  }

  Future<void> _save() async {
    final error = validateServerBaseUrl(_current.baseUrl);
    if (error != null) {
      setState(() {
        _result = error;
        _resultOk = false;
      });
      return;
    }
    final serverSettingsCurrent =
        _serverSettingsConnection != null &&
        _sameBaseUrl(_current, _serverSettingsConnection!);
    if (serverSettingsCurrent &&
        _serverSettings?.authRequired == true &&
        _current.token.isEmpty) {
      setState(() {
        _result = 'このサーバーではBearerトークンが必須です。';
        _resultOk = false;
      });
      return;
    }
    setState(() => _saving = true);
    try {
      await widget.repository.save(_current);
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _resultOk = false;
        _result = '保存失敗: $error';
      });
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _editRuntimeSettings() async {
    final settings = _serverSettings;
    final settingsConnection = _serverSettingsConnection;
    if (settings == null ||
        settingsConnection == null ||
        !settings.runtimeSettings.writable) {
      return;
    }
    if (!_sameConnection(_current, settingsConnection)) {
      setState(() {
        _resultOk = false;
        _result = 'runtimeモデル設定は、現在のURLで接続テストしてから編集してください。';
      });
      return;
    }
    final tiers = const ['low', 'balanced', 'high'];
    final controllers = <String, TextEditingController>{};
    for (final provider in settings.providers) {
      for (final tier in tiers) {
        controllers['${provider.name}:$tier'] = TextEditingController(
          text: provider.models[tier] ?? '',
        );
      }
    }
    for (final tier in tiers) {
      controllers['synth:$tier'] = TextEditingController(
        text: settings.runtimeSettings.effectiveSynthesizerModels[tier] ?? '',
      );
    }
    var synthesizer = settings.runtimeSettings.synthesizerProvider;
    String? validation;
    final payload = await showDialog<_RuntimeSettingsPayload>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) => AlertDialog(
          title: const Text('runtimeモデル設定'),
          content: SizedBox(
            width: 720,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  const Text(
                    'APIキーは変更しません。空欄を保存するとruntime上書きを解除し、'
                    '.envまたは既定値へ戻します。変更は次のrunから適用されます。',
                  ),
                  const SizedBox(height: 16),
                  DropdownButtonFormField<String>(
                    initialValue: synthesizer,
                    decoration: const InputDecoration(
                      labelText: '統合役',
                      border: OutlineInputBorder(),
                    ),
                    items: const [
                      DropdownMenuItem(value: 'auto', child: Text('auto')),
                      DropdownMenuItem(value: 'claude', child: Text('Claude')),
                      DropdownMenuItem(
                        value: 'chatgpt',
                        child: Text('ChatGPT'),
                      ),
                      DropdownMenuItem(value: 'gemini', child: Text('Gemini')),
                      DropdownMenuItem(value: 'grok', child: Text('Grok')),
                    ],
                    onChanged: (value) {
                      if (value != null) {
                        setDialogState(() => synthesizer = value);
                      }
                    },
                  ),
                  const SizedBox(height: 16),
                  for (final provider in settings.providers) ...[
                    Text(
                      provider.label,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    const SizedBox(height: 8),
                    for (final tier in tiers) ...[
                      TextField(
                        controller: controllers['${provider.name}:$tier'],
                        autocorrect: false,
                        enableSuggestions: false,
                        decoration: InputDecoration(
                          labelText: '$tier model ID',
                          border: const OutlineInputBorder(),
                        ),
                      ),
                      const SizedBox(height: 8),
                    ],
                    const SizedBox(height: 8),
                  ],
                  Text(
                    '統合model上書き',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: 8),
                  for (final tier in tiers) ...[
                    TextField(
                      controller: controllers['synth:$tier'],
                      autocorrect: false,
                      enableSuggestions: false,
                      decoration: InputDecoration(
                        labelText: '$tier synthesizer model ID',
                        border: const OutlineInputBorder(),
                      ),
                    ),
                    const SizedBox(height: 8),
                  ],
                  if (validation != null)
                    Text(
                      validation!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
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
              onPressed: () {
                final invalid = controllers.values.any(
                  (controller) =>
                      controller.text.trim().contains(RegExp(r'\s')),
                );
                if (invalid) {
                  setDialogState(() => validation = 'model IDに空白は使用できません。');
                  return;
                }
                Navigator.pop(
                  context,
                  _RuntimeSettingsPayload(
                    synthesizer: synthesizer,
                    values: {
                      for (final entry in controllers.entries)
                        entry.key: entry.value.text.trim(),
                    },
                  ),
                );
              },
              child: const Text('保存'),
            ),
          ],
        ),
      ),
    );
    for (final controller in controllers.values) {
      controller.dispose();
    }
    if (payload == null || !mounted) return;
    setState(() {
      _editingRuntime = true;
      _result = '';
    });
    // PATCH must go to the exact connection that produced `settings`.
    final client = widget.clientFactory(settingsConnection);
    try {
      final updated = await client.updateRuntimeSettings(
        expectedRevision: settings.runtimeSettings.revision,
        models: {
          for (final provider in settings.providers)
            provider.name: {
              for (final tier in tiers)
                tier: _nullableModel(payload.values['${provider.name}:$tier']),
            },
        },
        synthesizerProvider: payload.synthesizer,
        synthesizerModels: {
          for (final tier in tiers)
            tier: _nullableModel(payload.values['synth:$tier']),
        },
      );
      if (!mounted) return;
      setState(() {
        _serverSettings = updated;
        _resultOk = true;
        _result =
            'runtimeモデル設定を保存しました。revision=${updated.runtimeSettings.revision}';
      });
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _resultOk = false;
        _result = 'runtimeモデル設定の保存に失敗しました: $error';
      });
    } finally {
      client.close();
      if (mounted) setState(() => _editingRuntime = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final warning = _securityWarning(_current);
    final webStorageWarning = webTokenStorageWarning();
    final runtimeConnectionCurrent =
        _serverSettingsConnection != null &&
        _sameConnection(_current, _serverSettingsConnection!);
    final displayedServerSettings = runtimeConnectionCurrent
        ? _serverSettings
        : null;
    final serverSettingsStale =
        _serverSettings != null && !runtimeConnectionCurrent;
    final authRequired =
        _serverSettingsConnection != null &&
        _sameBaseUrl(_current, _serverSettingsConnection!) &&
        _serverSettings?.authRequired == true;
    return Scaffold(
      appBar: AppBar(title: const Text('接続とBYOK状態')),
      body: ListView(
        padding: const EdgeInsets.all(20),
        children: [
          Text('バックエンド接続', style: theme.textTheme.titleLarge),
          const SizedBox(height: 12),
          TextField(
            controller: _urlController,
            keyboardType: TextInputType.url,
            autocorrect: false,
            decoration: const InputDecoration(
              labelText: 'サーバーURL',
              hintText: 'http://127.0.0.1:8000',
              border: OutlineInputBorder(),
              prefixIcon: Icon(Icons.dns_outlined),
            ),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _tokenController,
            obscureText: _obscureToken,
            autocorrect: false,
            enableSuggestions: false,
            decoration: InputDecoration(
              labelText: authRequired ? 'Bearerトークン（必須）' : 'Bearerトークン（任意）',
              helperText: authRequired ? 'このサーバーへの接続にはトークンが必要です。' : null,
              border: const OutlineInputBorder(),
              prefixIcon: const Icon(Icons.key_outlined),
              suffixIcon: IconButton(
                tooltip: _obscureToken ? '表示' : '隠す',
                onPressed: () => setState(() => _obscureToken = !_obscureToken),
                icon: Icon(
                  _obscureToken
                      ? Icons.visibility_outlined
                      : Icons.visibility_off_outlined,
                ),
              ),
            ),
            onChanged: (_) => setState(() {}),
          ),
          if (webStorageWarning.isNotEmpty) ...[
            const SizedBox(height: 12),
            _Notice(
              icon: Icons.warning_amber_rounded,
              color: theme.colorScheme.errorContainer,
              text: webStorageWarning,
            ),
          ],
          if (warning.isNotEmpty) ...[
            const SizedBox(height: 12),
            _Notice(
              icon: Icons.warning_amber_rounded,
              color: theme.colorScheme.errorContainer,
              text: warning,
            ),
          ],
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              OutlinedButton.icon(
                onPressed: _testing ? null : _test,
                icon: _testing
                    ? const SizedBox.square(
                        dimension: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.wifi_tethering),
                label: const Text('接続テスト'),
              ),
              FilledButton.icon(
                onPressed: _saving ? null : _save,
                icon: _saving
                    ? const SizedBox.square(
                        dimension: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.save_outlined),
                label: const Text('保存'),
              ),
            ],
          ),
          if (_result.isNotEmpty) ...[
            const SizedBox(height: 12),
            _Notice(
              icon: _resultOk
                  ? Icons.check_circle_outline
                  : Icons.error_outline,
              color: _resultOk
                  ? theme.colorScheme.secondaryContainer
                  : theme.colorScheme.errorContainer,
              text: _result,
            ),
          ],
          const SizedBox(height: 28),
          Row(
            children: [
              Expanded(
                child: Text('APIキー状態', style: theme.textTheme.titleLarge),
              ),
              if (_serverSettings?.runtimeSettings.writable == true)
                IconButton(
                  tooltip: runtimeConnectionCurrent
                      ? 'runtimeモデル設定を編集'
                      : '現在のURLで接続テストすると編集できます',
                  onPressed: _editingRuntime || !runtimeConnectionCurrent
                      ? null
                      : _editRuntimeSettings,
                  icon: _editingRuntime
                      ? const SizedBox.square(
                          dimension: 18,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        )
                      : const Icon(Icons.tune),
                ),
              if (displayedServerSettings != null)
                Chip(
                  avatar: Icon(
                    displayedServerSettings.liveApiEnabled
                        ? Icons.cloud_outlined
                        : Icons.lock_outline,
                    size: 17,
                  ),
                  label: Text(
                    displayedServerSettings.liveApiEnabled
                        ? 'mode: ${displayedServerSettings.mode}'
                        : 'SAFE MOCK',
                  ),
                ),
            ],
          ),
          const SizedBox(height: 6),
          const Text(
            'APIキーはバックエンドの .env だけに置きます。この画面には設定済みかどうかと'
            'モデル名だけが返り、キーそのものは返りません。',
          ),
          const SizedBox(height: 12),
          if (serverSettingsStale)
            _Notice(
              icon: Icons.sync_problem_outlined,
              color: theme.colorScheme.errorContainer,
              text:
                  '入力中のURLまたはトークンは、表示済み状態の取得元と一致しません。'
                  '接続テストを行うまでAPIキー状態は表示しません。',
            )
          else if (displayedServerSettings != null)
            _Notice(
              icon: displayedServerSettings.liveApiEnabled
                  ? Icons.warning_amber_rounded
                  : Icons.lock_outline,
              color: displayedServerSettings.liveApiEnabled
                  ? theme.colorScheme.errorContainer
                  : theme.colorScheme.secondaryContainer,
              text: displayedServerSettings.liveApiEnabled
                  ? 'LIVE API ENABLED: 送信前に実行プランを確認し、課金の可能性がある会議は確認を求めます。'
                  : 'SAFE MOCK: live_api_enabled=false。APIキーが設定済みでも外部AI APIは呼び出しません。',
            ),
          if (serverSettingsStale || displayedServerSettings != null)
            const SizedBox(height: 12),
          if (!serverSettingsStale && displayedServerSettings == null)
            const _Notice(
              icon: Icons.info_outline,
              color: Color(0x222196F3),
              text: '接続テストを行うとプロバイダ状態を表示します。',
            )
          else if (displayedServerSettings != null)
            for (final provider in displayedServerSettings.providers)
              Card(
                child: ExpansionTile(
                  leading: Icon(
                    !displayedServerSettings.liveApiEnabled
                        ? Icons.lock_outline
                        : provider.configured
                        ? Icons.cloud_done_outlined
                        : Icons.science_outlined,
                  ),
                  title: Text(provider.label),
                  subtitle: Text(
                    !displayedServerSettings.liveApiEnabled &&
                            provider.configured
                        ? 'キー検出済み · SAFE MOCKで未使用'
                        : provider.configured
                        ? '実API設定済み'
                        : provider.mode == 'mock'
                        ? 'モックデモ'
                        : '未設定（会議から除外）',
                  ),
                  trailing: Chip(
                    label: Text(provider.mode.toUpperCase()),
                    visualDensity: VisualDensity.compact,
                  ),
                  children: [
                    for (final tier in const ['low', 'balanced', 'high'])
                      ListTile(
                        dense: true,
                        title: Text(tier),
                        subtitle: Text(provider.models[tier] ?? '未設定'),
                      ),
                  ],
                ),
              ),
          const SizedBox(height: 20),
          const _Notice(
            icon: Icons.shield_outlined,
            color: Color(0x2210A37F),
            text:
                'LAN外からは公開せず、外出先ではTailscaleを使ってください。'
                '0.0.0.0で待ち受ける場合はCLAGE_AUTH_TOKENを必ず設定します。',
          ),
        ],
      ),
    );
  }

  static String _securityWarning(ConnectionSettings settings) {
    final uri = Uri.tryParse(settings.baseUrl);
    if (uri == null) return '';
    final local = const {'127.0.0.1', 'localhost', '::1'}.contains(uri.host);
    final warnings = <String>[];
    if (!local && uri.scheme == 'http') {
      warnings.add('平文HTTPではトークンと会話が暗号化されません。LAN/Tailscale内だけで使ってください。');
    }
    if (!local && settings.token.isEmpty) {
      warnings.add('リモートURLに接続する場合はBearerトークンを設定してください。');
    }
    return warnings.join('\n');
  }
}

String? _nullableModel(String? value) {
  final cleaned = value?.trim() ?? '';
  return cleaned.isEmpty ? null : cleaned;
}

class _RuntimeSettingsPayload {
  const _RuntimeSettingsPayload({
    required this.synthesizer,
    required this.values,
  });

  final String synthesizer;
  final Map<String, String> values;
}

class _Notice extends StatelessWidget {
  const _Notice({required this.icon, required this.color, required this.text});

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
