import 'dart:convert';
import 'dart:math';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../models.dart';

String? validateServerBaseUrl(String value) {
  final uri = Uri.tryParse(value.trim());
  if (uri == null ||
      !uri.hasScheme ||
      !uri.hasAuthority ||
      uri.host.isEmpty ||
      (uri.scheme != 'http' && uri.scheme != 'https')) {
    return 'http:// または https:// で始まる有効なサーバーURLを入力してください。';
  }
  if (uri.userInfo.isNotEmpty) {
    return 'サーバーURLにユーザー名やパスワードを含めないでください。Bearerトークン欄を使用してください。';
  }
  if (uri.hasQuery || uri.hasFragment) {
    return 'サーバーURLにクエリ（?）やフラグメント（#）は指定できません。';
  }
  return null;
}

String normalizeServerBaseUrl(String value) {
  var result = value.trim();
  while (result.endsWith('/')) {
    result = result.substring(0, result.length - 1);
  }
  return result;
}

abstract class SettingsRepository {
  Future<ConnectionSettings> load();
  Future<void> save(ConnectionSettings settings);
}

/// SettingsStoreが使う最小の文字列ストア契約。
///
/// 公開設定と秘密設定を別実装へ分けたまま、障害を含む原子的保存をテストできる。
abstract interface class SettingsValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
}

class _PreferencesValueStore implements SettingsValueStore {
  _PreferencesValueStore([SharedPreferencesAsync? delegate])
    : _delegate = delegate ?? SharedPreferencesAsync();

  final SharedPreferencesAsync _delegate;

  @override
  Future<String?> read(String key) => _delegate.getString(key);

  @override
  Future<void> write(String key, String value) =>
      _delegate.setString(key, value);
}

class _SecureValueStore implements SettingsValueStore {
  _SecureValueStore([FlutterSecureStorage? delegate])
    : _delegate = delegate ?? const FlutterSecureStorage();

  final FlutterSecureStorage _delegate;

  @override
  Future<String?> read(String key) => _delegate.read(key: key);

  @override
  Future<void> write(String key, String value) =>
      _delegate.write(key: key, value: value);
}

class SettingsStore implements SettingsRepository {
  SettingsStore({
    SettingsValueStore? publicStore,
    SettingsValueStore? secretStore,
    String Function()? revisionFactory,
  }) : _publicStore = publicStore ?? _PreferencesValueStore(),
       _secretStore = secretStore ?? _SecureValueStore(),
       _revisionFactory = revisionFactory ?? _newRevision;

  static const _defaultBaseUrl = 'http://127.0.0.1:8000';
  static const _publicRecordKey = 'server_connection_public_v1';
  static const _secretRecordKey = 'server_connection_secret_v1';

  final SettingsValueStore _publicStore;
  final SettingsValueStore _secretStore;
  final String Function() _revisionFactory;

  @override
  Future<ConnectionSettings> load() async {
    final publicRecord = _decodeRecord(
      await _publicStore.read(_publicRecordKey),
    );
    final baseUrl = _normalizedRecordBaseUrl(publicRecord) ?? _defaultBaseUrl;
    final revision = _recordString(publicRecord, 'revision');
    var token = '';
    try {
      final secretRecord = _decodeRecord(
        await _secretStore.read(_secretRecordKey),
      );
      final secretBaseUrl = _normalizedRecordBaseUrl(secretRecord);
      final expectedOrigin = _originOf(baseUrl);
      final secretOrigin = _recordString(secretRecord, 'origin');
      final secretRevision = _recordString(secretRecord, 'revision');
      if (revision.isNotEmpty &&
          secretRevision == revision &&
          secretBaseUrl == baseUrl &&
          expectedOrigin != null &&
          secretOrigin == expectedOrigin) {
        token = _recordString(secretRecord, 'token');
      }
    } catch (_) {
      // Secure storageが使えない環境でも、tokenを平文側へ降格しない。
    }
    return ConnectionSettings(baseUrl: baseUrl, token: token);
  }

  @override
  Future<void> save(ConnectionSettings settings) async {
    final validationError = validateServerBaseUrl(settings.baseUrl);
    if (validationError != null) throw StateError(validationError);
    final baseUrl = normalizeServerBaseUrl(settings.baseUrl);
    final origin = _originOf(baseUrl);
    if (origin == null) {
      throw StateError('接続先URLが不正です。');
    }
    final revision = _revisionFactory().trim();
    if (revision.isEmpty) {
      throw StateError('接続設定のrevisionを生成できません。');
    }
    final secretRecord = jsonEncode({
      'version': 1,
      'base_url': baseUrl,
      'origin': origin,
      'revision': revision,
      'token': settings.token.trim(),
    });
    final publicRecord = jsonEncode({
      'version': 1,
      'base_url': baseUrl,
      'revision': revision,
    });

    try {
      // 秘密側を先に書き、公開側のrevision更新をcommit pointにする。
      // 途中停止や公開側失敗ではrevisionが一致せず、tokenはfail-closedになる。
      await _secretStore.write(_secretRecordKey, secretRecord);
      await _publicStore.write(_publicRecordKey, publicRecord);
    } catch (error) {
      throw StateError(
        '接続設定を安全に保存できません。Web版はHTTPSまたはlocalhostで開いてください。 '
        '保存に失敗した設定のBearerトークンは使用しません。 ($error)',
      );
    }
  }

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

  static String? _normalizedRecordBaseUrl(Map<String, dynamic> record) {
    final value = _recordString(record, 'base_url');
    if (value.isEmpty || validateServerBaseUrl(value) != null) return null;
    final normalized = normalizeServerBaseUrl(value);
    return _originOf(normalized) == null ? null : normalized;
  }

  static String _recordString(Map<String, dynamic> record, String key) {
    final value = record[key];
    return value is String ? value : '';
  }

  static String? _originOf(String value) {
    final uri = Uri.tryParse(value);
    if (uri == null ||
        !uri.hasAuthority ||
        (uri.scheme != 'http' && uri.scheme != 'https') ||
        uri.host.isEmpty) {
      return null;
    }
    return uri.origin;
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
