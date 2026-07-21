import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// AIへの接続経路。
enum ExecutionMode { directByok, referenceServer }

/// モデルtierとは独立した推論エフォート。
///
/// [auto] はモデルfamilyごとの推奨値、残りは利用者が明示する固定値を表す。
enum ReasoningMode { auto, low, medium, high }

/// Clage Cookが直接接続できるAIプロバイダー。
enum DirectProvider { claude, chatgpt, gemini, grok }

/// APIキーの設定状態。キーそのものをUIへ渡さず状態だけを表示できる。
enum DirectApiKeyStatus { configured, missing }

const _unchanged = Object();

/// Direct BYOKと開発用reference serverの端末設定。
///
/// すべてのフィールドは不変。APIキーは保存時にsecure storageへ分離され、
/// SharedPreferences側へ書き込まれない。
class DirectSettings {
  const DirectSettings({
    this.executionMode = ExecutionMode.directByok,
    this.reasoningMode = ReasoningMode.auto,
    this.showTokenUsageLedger = true,
    this.showLiveApiConfirmation = true,
    this.claudeApiKey = '',
    this.chatGptApiKey = '',
    this.geminiApiKey = '',
    this.grokApiKey = '',
    this.claudeModelOverride = '',
    this.chatGptModelOverride = '',
    this.geminiModelOverride = '',
    this.grokModelOverride = '',
    this.synthesizerProvider,
  });

  final ExecutionMode executionMode;
  final ReasoningMode reasoningMode;
  final bool showTokenUsageLedger;
  final bool showLiveApiConfirmation;

  final String claudeApiKey;
  final String chatGptApiKey;
  final String geminiApiKey;
  final String grokApiKey;

  final String claudeModelOverride;
  final String chatGptModelOverride;
  final String geminiModelOverride;
  final String grokModelOverride;

  /// nullは利用可能なプロバイダーからの自動選択を表す。
  final DirectProvider? synthesizerProvider;

  /// APIで一般的な事業者名を使いたい呼び出し側向けの別名。
  String get anthropicApiKey => claudeApiKey;
  String get openAiApiKey => chatGptApiKey;
  String get xAiApiKey => grokApiKey;

  String apiKeyFor(DirectProvider provider) => switch (provider) {
    DirectProvider.claude => claudeApiKey,
    DirectProvider.chatgpt => chatGptApiKey,
    DirectProvider.gemini => geminiApiKey,
    DirectProvider.grok => grokApiKey,
  };

  String modelOverrideFor(DirectProvider provider) => switch (provider) {
    DirectProvider.claude => claudeModelOverride,
    DirectProvider.chatgpt => chatGptModelOverride,
    DirectProvider.gemini => geminiModelOverride,
    DirectProvider.grok => grokModelOverride,
  };

  bool hasKey(DirectProvider provider) => apiKeyFor(provider).trim().isNotEmpty;

  DirectApiKeyStatus status(DirectProvider provider) => hasKey(provider)
      ? DirectApiKeyStatus.configured
      : DirectApiKeyStatus.missing;

  bool get hasAnyKey => DirectProvider.values.any(hasKey);

  bool get hasAllKeys => DirectProvider.values.every(hasKey);

  DirectSettings copyWith({
    ExecutionMode? executionMode,
    ReasoningMode? reasoningMode,
    bool? showTokenUsageLedger,
    bool? showLiveApiConfirmation,
    String? claudeApiKey,
    String? chatGptApiKey,
    String? geminiApiKey,
    String? grokApiKey,
    String? claudeModelOverride,
    String? chatGptModelOverride,
    String? geminiModelOverride,
    String? grokModelOverride,
    Object? synthesizerProvider = _unchanged,
  }) => DirectSettings(
    executionMode: executionMode ?? this.executionMode,
    reasoningMode: reasoningMode ?? this.reasoningMode,
    showTokenUsageLedger: showTokenUsageLedger ?? this.showTokenUsageLedger,
    showLiveApiConfirmation:
        showLiveApiConfirmation ?? this.showLiveApiConfirmation,
    claudeApiKey: claudeApiKey ?? this.claudeApiKey,
    chatGptApiKey: chatGptApiKey ?? this.chatGptApiKey,
    geminiApiKey: geminiApiKey ?? this.geminiApiKey,
    grokApiKey: grokApiKey ?? this.grokApiKey,
    claudeModelOverride: claudeModelOverride ?? this.claudeModelOverride,
    chatGptModelOverride: chatGptModelOverride ?? this.chatGptModelOverride,
    geminiModelOverride: geminiModelOverride ?? this.geminiModelOverride,
    grokModelOverride: grokModelOverride ?? this.grokModelOverride,
    synthesizerProvider: identical(synthesizerProvider, _unchanged)
        ? this.synthesizerProvider
        : synthesizerProvider as DirectProvider?,
  );
}

abstract interface class DirectSettingsRepository {
  Future<DirectSettings> load();
  Future<void> save(DirectSettings settings);

  /// APIキーや他の設定を再保存せず、通常の実API確認表示だけを更新する。
  Future<void> setShowLiveApiConfirmation(bool value);

  /// 保存済みAPIキーを全社分削除し、非秘密設定は保持する。
  Future<void> clearAllKeys();
}

/// DirectSettingsStoreが使う、テスト注入可能な最小ストア契約。
abstract interface class DirectSettingsValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

class _PreferencesDirectValueStore implements DirectSettingsValueStore {
  _PreferencesDirectValueStore([SharedPreferencesAsync? delegate])
    : _delegate = delegate ?? SharedPreferencesAsync();

  final SharedPreferencesAsync _delegate;

  @override
  Future<String?> read(String key) => _delegate.getString(key);

  @override
  Future<void> write(String key, String value) =>
      _delegate.setString(key, value);

  @override
  Future<void> delete(String key) => _delegate.remove(key);
}

class _SecureDirectValueStore implements DirectSettingsValueStore {
  _SecureDirectValueStore([FlutterSecureStorage? delegate])
    : _delegate = delegate ?? const FlutterSecureStorage();

  final FlutterSecureStorage _delegate;

  @override
  Future<String?> read(String key) => _delegate.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _delegate.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _delegate.delete(key: key);
}

/// Direct設定を公開値と秘密値へrevision付きで分離保存する。
///
/// 秘密側を先に書き、公開側のrevision更新をcommit pointにする。どちらかの
/// 書き込みが失敗した場合、revision不一致のAPIキーは読み込まずfail-closedに
/// する。secure storage失敗時にSharedPreferencesへ平文で降格しない。
class DirectSettingsStore implements DirectSettingsRepository {
  DirectSettingsStore({
    DirectSettingsValueStore? publicStore,
    DirectSettingsValueStore? secretStore,
    String Function()? revisionFactory,
  }) : _publicStore = publicStore ?? _PreferencesDirectValueStore(),
       _secretStore = secretStore ?? _SecureDirectValueStore(),
       _revisionFactory = revisionFactory ?? _newRevision;

  static const _publicRecordKey = 'direct_settings_public_v1';
  static const _secretRecordKey = 'direct_settings_secret_v1';
  static final _mutex = _DirectSettingsMutex();

  final DirectSettingsValueStore _publicStore;
  final DirectSettingsValueStore _secretStore;
  final String Function() _revisionFactory;

  @override
  Future<DirectSettings> load() => _mutex.protect(_load);

  Future<DirectSettings> _load() async {
    final publicRecord = _decodeRecord(
      await _publicStore.read(_publicRecordKey),
    );
    final publicSettings = _settingsFromPublicRecord(publicRecord);
    final revision = _recordString(publicRecord, 'revision');
    if (revision.isEmpty) return publicSettings;

    try {
      final secretRecord = _decodeRecord(
        await _secretStore.read(_secretRecordKey),
      );
      if (_recordString(secretRecord, 'revision') != revision) {
        return publicSettings;
      }
      final keys = _recordMap(secretRecord, 'api_keys');
      return publicSettings.copyWith(
        claudeApiKey: _recordString(keys, DirectProvider.claude.name),
        chatGptApiKey: _recordString(keys, DirectProvider.chatgpt.name),
        geminiApiKey: _recordString(keys, DirectProvider.gemini.name),
        grokApiKey: _recordString(keys, DirectProvider.grok.name),
      );
    } catch (_) {
      // 秘密ストアが使えなくても、公開ストアへAPIキーを降格しない。
      return publicSettings;
    }
  }

  @override
  Future<void> save(DirectSettings settings) =>
      _mutex.protect(() => _save(settings));

  Future<void> _save(DirectSettings settings) async {
    final revision = _requiredRevision();
    final normalized = _normalized(settings);
    final secretRecord = _encodeSecretRecord(normalized, revision);
    final publicRecord = _encodePublicRecord(normalized, revision);

    try {
      await _secretStore.write(_secretRecordKey, secretRecord);
      await _publicStore.write(_publicRecordKey, publicRecord);
    } catch (_) {
      throw StateError(
        'Direct BYOK設定を安全に保存できません。APIキーは平文保存へ降格しません。 '
        'Web版はHTTPSまたはlocalhostで開いてください。',
      );
    }
  }

  @override
  Future<void> setShowLiveApiConfirmation(bool value) =>
      _mutex.protect(() => _setShowLiveApiConfirmation(value));

  Future<void> _setShowLiveApiConfirmation(bool value) async {
    final publicRecord = _decodeRecord(
      await _publicStore.read(_publicRecordKey),
    );
    final current = _settingsFromPublicRecord(publicRecord);
    final existingRevision = _recordString(publicRecord, 'revision');
    final revision = existingRevision.isEmpty
        ? _requiredRevision()
        : existingRevision;
    final updated = current.copyWith(showLiveApiConfirmation: value);
    try {
      await _publicStore.write(
        _publicRecordKey,
        _encodePublicRecord(updated, revision),
      );
    } catch (_) {
      throw StateError('実API確認の表示設定を保存できませんでした。');
    }
  }

  @override
  Future<void> clearAllKeys() => _mutex.protect(_clearAllKeys);

  Future<void> _clearAllKeys() async {
    final publicRecord = _decodeRecord(
      await _publicStore.read(_publicRecordKey),
    );
    final settings = _settingsFromPublicRecord(publicRecord);
    final revision = _requiredRevision();
    final clearedPublicRecord = _encodePublicRecord(settings, revision);

    try {
      // 物理削除を先に行う。公開側更新が失敗しても旧キーは復活しない。
      await _secretStore.delete(_secretRecordKey);
      await _publicStore.write(_publicRecordKey, clearedPublicRecord);
    } catch (_) {
      throw StateError('保存済みAPIキーを安全に削除できませんでした。');
    }
  }

  /// 呼び出し側で意図が明確になる別名。
  Future<void> clearAllApiKeys() => clearAllKeys();

  String _requiredRevision() {
    final revision = _revisionFactory().trim();
    if (revision.isEmpty) {
      throw StateError('Direct BYOK設定のrevisionを生成できません。');
    }
    return revision;
  }

  static DirectSettings _normalized(DirectSettings settings) =>
      settings.copyWith(
        claudeApiKey: settings.claudeApiKey.trim(),
        chatGptApiKey: settings.chatGptApiKey.trim(),
        geminiApiKey: settings.geminiApiKey.trim(),
        grokApiKey: settings.grokApiKey.trim(),
        claudeModelOverride: settings.claudeModelOverride.trim(),
        chatGptModelOverride: settings.chatGptModelOverride.trim(),
        geminiModelOverride: settings.geminiModelOverride.trim(),
        grokModelOverride: settings.grokModelOverride.trim(),
      );

  static DirectSettings _settingsFromPublicRecord(Map<String, dynamic> record) {
    final models = _recordMap(record, 'model_overrides');
    return DirectSettings(
      executionMode: _enumByName(
        ExecutionMode.values,
        _recordString(record, 'execution_mode'),
        ExecutionMode.directByok,
      ),
      reasoningMode: _enumByName(
        ReasoningMode.values,
        _recordString(record, 'reasoning_mode'),
        ReasoningMode.auto,
      ),
      showTokenUsageLedger: _recordBool(
        record,
        'show_token_usage_ledger',
        true,
      ),
      showLiveApiConfirmation: _recordBool(
        record,
        'show_live_api_confirmation',
        true,
      ),
      claudeModelOverride: _recordString(
        models,
        DirectProvider.claude.name,
      ).trim(),
      chatGptModelOverride: _recordString(
        models,
        DirectProvider.chatgpt.name,
      ).trim(),
      geminiModelOverride: _recordString(
        models,
        DirectProvider.gemini.name,
      ).trim(),
      grokModelOverride: _recordString(models, DirectProvider.grok.name).trim(),
      synthesizerProvider: _providerOrNull(
        _recordString(record, 'synthesizer_provider'),
      ),
    );
  }

  static String _encodePublicRecord(DirectSettings settings, String revision) =>
      jsonEncode({
        'version': 1,
        'revision': revision,
        'execution_mode': settings.executionMode.name,
        'reasoning_mode': settings.reasoningMode.name,
        'show_token_usage_ledger': settings.showTokenUsageLedger,
        'show_live_api_confirmation': settings.showLiveApiConfirmation,
        'model_overrides': {
          DirectProvider.claude.name: settings.claudeModelOverride,
          DirectProvider.chatgpt.name: settings.chatGptModelOverride,
          DirectProvider.gemini.name: settings.geminiModelOverride,
          DirectProvider.grok.name: settings.grokModelOverride,
        },
        'synthesizer_provider': settings.synthesizerProvider?.name ?? 'auto',
      });

  static String _encodeSecretRecord(DirectSettings settings, String revision) =>
      jsonEncode({
        'version': 1,
        'revision': revision,
        'api_keys': {
          DirectProvider.claude.name: settings.claudeApiKey,
          DirectProvider.chatgpt.name: settings.chatGptApiKey,
          DirectProvider.gemini.name: settings.geminiApiKey,
          DirectProvider.grok.name: settings.grokApiKey,
        },
      });

  static Map<String, dynamic> _decodeRecord(String? value) {
    if (value == null || value.isEmpty) return const {};
    try {
      final decoded = jsonDecode(value);
      if (decoded is! Map || decoded['version'] != 1) return const {};
      return Map<String, dynamic>.from(decoded);
    } on FormatException {
      return const {};
    }
  }

  static Map<String, dynamic> _recordMap(
    Map<String, dynamic> record,
    String key,
  ) {
    final value = record[key];
    return value is Map ? Map<String, dynamic>.from(value) : const {};
  }

  static String _recordString(Map<String, dynamic> record, String key) {
    final value = record[key];
    return value is String ? value : '';
  }

  static bool _recordBool(
    Map<String, dynamic> record,
    String key,
    bool fallback,
  ) {
    final value = record[key];
    return value is bool ? value : fallback;
  }

  static T _enumByName<T extends Enum>(
    List<T> values,
    String name,
    T fallback,
  ) {
    for (final value in values) {
      if (value.name == name) return value;
    }
    return fallback;
  }

  static DirectProvider? _providerOrNull(String name) {
    if (name.isEmpty || name == 'auto') return null;
    for (final provider in DirectProvider.values) {
      if (provider.name == name) return provider;
    }
    return null;
  }

  static String _newRevision() {
    final random = Random.secure();
    final time = DateTime.now().microsecondsSinceEpoch.toRadixString(36);
    final suffix = List.generate(
      4,
      (_) => random.nextInt(0x7fffffff).toRadixString(36).padLeft(6, '0'),
    ).join();
    return '$time-$suffix';
  }
}

class _DirectSettingsMutex {
  Future<void> _tail = Future<void>.value();

  Future<T> protect<T>(Future<T> Function() action) {
    final completer = Completer<T>();
    _tail = _tail.then((_) async {
      try {
        completer.complete(await action());
      } catch (error, stackTrace) {
        completer.completeError(error, stackTrace);
      }
    });
    return completer.future;
  }
}
