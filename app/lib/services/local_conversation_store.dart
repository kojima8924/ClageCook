import 'dart:async';
import 'dart:convert';
import 'dart:math';

import 'package:shared_preferences/shared_preferences.dart';

/// Logical storage partition for locally persisted conversations.
///
/// 現在はDirect BYOKだけが端末を正本にする。将来ほかの実行方式が端末保存を
/// 持つときはnamespaceを増やし、履歴が混ざらないようにする。
class LocalConversationNamespace {
  const LocalConversationNamespace._(this.identity);

  static const directByok = LocalConversationNamespace._('direct-byok');

  final String identity;

  @override
  bool operator ==(Object other) =>
      other is LocalConversationNamespace && other.identity == identity;

  @override
  int get hashCode => identity.hashCode;

  @override
  String toString() => identity;
}

/// A raw conversation document together with its storage-level revision.
///
/// [value] follows the existing backend JSON shape. The storage revision is
/// kept outside that JSON so direct-mode persistence does not leak an
/// implementation field into exports or provider prompts.
class LocalConversationDocument {
  const LocalConversationDocument({
    required this.value,
    required this.storageRevision,
  });

  final Map<String, dynamic> value;
  final int storageRevision;

  String get id => value['id']?.toString() ?? '';
}

class LocalConversationSummary {
  const LocalConversationSummary({
    required this.id,
    required this.title,
    required this.updatedAt,
    required this.turnCount,
    required this.preview,
  });

  /// 会話documentから一覧用の要約を作る。
  ///
  /// previewの作り方をここへ一本化し、呼び出し側ごとに違う要約が出るのを防ぐ。
  factory LocalConversationSummary.fromDocument(
    LocalConversationDocument document,
  ) {
    final value = document.value;
    final turns = value['turns'] is List ? value['turns'] as List : const [];
    var preview = '';
    if (turns.isNotEmpty && turns.last is Map) {
      final last = turns.last as Map;
      final synthesis = last['synthesis'];
      if (synthesis is Map) preview = synthesis['text']?.toString() ?? '';
      if (preview.trim().isEmpty) {
        preview = last['message']?.toString() ?? '';
      }
    }
    return LocalConversationSummary(
      id: document.id,
      title: value['title']?.toString() ?? '新しい会話',
      updatedAt: value['updated_at']?.toString() ?? '',
      turnCount: turns.length,
      preview: _truncate(preview.replaceAll(RegExp(r'\s+'), ' ').trim(), 160),
    );
  }

  final String id;
  final String title;
  final String updatedAt;
  final int turnCount;
  final String preview;
}

class LocalConversationNotFound implements Exception {
  const LocalConversationNotFound(this.conversationId);

  final String conversationId;

  @override
  String toString() => 'ローカル会話が見つかりません: $conversationId';
}

class LocalConversationConflict implements Exception {
  const LocalConversationConflict({
    required this.conversationId,
    required this.expectedRevision,
    required this.actualRevision,
  });

  final String conversationId;
  final int expectedRevision;
  final int actualRevision;

  @override
  String toString() =>
      'ローカル会話が別の操作で更新されています: $conversationId '
      '(expected=$expectedRevision, actual=$actualRevision)';
}

class LocalConversationCorruption implements Exception {
  const LocalConversationCorruption(this.message);

  final String message;

  @override
  String toString() => 'ローカル会話ストレージが破損しています: $message';
}

/// 一覧・検索で読めなかった1件分の記録。
///
/// 破損は「読めない1件」の事実であり、他の健全な会話を巻き添えにしない。
/// [storageRevision] が0のときは、manifest entry自体が壊れていて
/// どのrevisionを指していたか分からないことを表す。
class LocalConversationDefect {
  const LocalConversationDefect({
    required this.conversationId,
    required this.storageRevision,
    required this.reason,
  });

  final String conversationId;
  final int storageRevision;
  final String reason;

  @override
  String toString() => '$conversationId@$storageRevision: $reason';
}

/// [LocalConversationRepository.list] / [LocalConversationRepository.search]
/// の戻り値。読めた分と読めなかった分を同時に返す。
class LocalConversationListing {
  const LocalConversationListing({
    required this.items,
    this.defects = const <LocalConversationDefect>[],
  });

  static const empty = LocalConversationListing(
    items: <LocalConversationSummary>[],
  );

  final List<LocalConversationSummary> items;
  final List<LocalConversationDefect> defects;

  bool get hasDefects => defects.isNotEmpty;
  int get length => items.length;
  bool get isEmpty => items.isEmpty;
  bool get isNotEmpty => items.isNotEmpty;
}

/// Persistence contract used by direct BYOK mode.
///
/// Implementations must isolate each [namespace] and must not return mutable
/// references to their internal snapshots.
abstract interface class LocalConversationRepository {
  LocalConversationNamespace get namespace;

  Future<LocalConversationDocument> create({
    String firstMessage = '',
    String? conversationId,
  });

  Future<LocalConversationDocument?> read(String conversationId);

  Future<LocalConversationDocument> save(
    Map<String, dynamic> conversation, {
    int? expectedStorageRevision,
  });

  /// 読めた会話と、破損して読めなかった会話をまとめて返す。
  ///
  /// 破損1件で一覧全体を落とさない。健全な会話のexport・削除経路を残すため、
  /// ここでは例外を投げない(特定の会話を開く [read] だけがfail-closedを保つ)。
  Future<LocalConversationListing> list();

  Future<LocalConversationListing> search(String query, {int limit = 30});

  Future<LocalConversationDocument> rename(
    String conversationId,
    String title, {
    int? expectedStorageRevision,
  });

  Future<void> delete(String conversationId, {int? expectedStorageRevision});

  Future<LocalConversationDocument> fork({
    required String conversationId,
    required int beforeTurnIndex,
    required String parentTurnRequestId,
    String? branchConversationId,
    int? expectedStorageRevision,
  });

  Future<LocalConversationDocument> updateMemory({
    required String conversationId,
    required int expectedMemoryRevision,
    required String text,
    int? expectedStorageRevision,
  });

  Future<String> exportJson(String conversationId);

  /// 破損した会話をmanifestから外し、recordは隔離keyへ退避する。
  ///
  /// 端末が正本なので中身は削除しない。戻り値は隔離できた件数。
  Future<int> quarantine(Iterable<String> conversationIds);

  /// 残っているrecordからmanifestを組み直す。
  ///
  /// manifest自体が壊れたときの明示的な復旧操作で、自動実行はしない。
  /// 戻り値は再登録できた会話の件数。
  Future<int> rebuildManifestFromRecords();

  /// Removes immutable records no longer referenced by the commit manifest.
  ///
  /// 破損entryが残っている間は何も削除しない。隔離または再構築で
  /// 破損が解消してから物理削除する。
  Future<void> compact();
}

/// Minimal string store used by the manifest/immutable-record protocol.
abstract interface class LocalConversationValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> remove(String key);
  Future<Set<String>> keys();
}

class SharedPreferencesLocalConversationValueStore
    implements LocalConversationValueStore {
  SharedPreferencesLocalConversationValueStore([
    SharedPreferencesAsync? delegate,
  ]) : _delegate = delegate ?? SharedPreferencesAsync();

  final SharedPreferencesAsync _delegate;

  @override
  Future<String?> read(String key) => _delegate.getString(key);

  @override
  Future<void> write(String key, String value) =>
      _delegate.setString(key, value);

  @override
  Future<void> remove(String key) => _delegate.remove(key);

  @override
  Future<Set<String>> keys() => _delegate.getKeys();
}

/// In-memory fake for unit tests and dependency-injected previews.
class MemoryLocalConversationValueStore implements LocalConversationValueStore {
  final Map<String, String> values = <String, String>{};

  @override
  Future<Set<String>> keys() async => values.keys.toSet();

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> remove(String key) async {
    values.remove(key);
  }

  @override
  Future<void> write(String key, String value) async {
    values[key] = value;
  }
}

/// SharedPreferences implementation using immutable records and one manifest
/// as the commit point.
///
/// A save writes the new conversation record first and publishes it by a
/// single manifest write. A failed manifest write leaves the previous revision
/// readable. Old or crash-orphaned records are ignored and can be removed by
/// [compact]. Calls in the same isolate and namespace are serialized.
class SharedPreferencesLocalConversationRepository
    implements LocalConversationRepository {
  SharedPreferencesLocalConversationRepository({
    required this.namespace,
    LocalConversationValueStore? valueStore,
    DateTime Function()? clock,
    String Function()? idFactory,
    // SharedPreferencesはprefs全体をメモリへ展開し、保存のたびに書き直す。
    // 16 MiBの会話は現実的に扱えないため、分岐やexportを促す上限にする。
    this.maxConversationBytes = 2 * 1024 * 1024,
    this.maxMemoryCharacters = 20 * 1000,
  }) : assert(maxConversationBytes > 0),
       assert(maxMemoryCharacters > 0),
       _valueStore =
           valueStore ?? SharedPreferencesLocalConversationValueStore(),
       _clock = clock ?? DateTime.now,
       _idFactory = idFactory ?? _uuidV4,
       _rootKey = _rootFor(namespace),
       _mutex = _mutexFor(_rootFor(namespace));

  static const _formatVersion = 1;
  static final Map<String, _AsyncMutex> _mutexes = <String, _AsyncMutex>{};

  @override
  final LocalConversationNamespace namespace;
  final LocalConversationValueStore _valueStore;
  final DateTime Function() _clock;
  final String Function() _idFactory;
  final String _rootKey;
  final _AsyncMutex _mutex;
  final int maxConversationBytes;
  final int maxMemoryCharacters;

  String get _manifestKey => '$_rootKey.manifest';
  String get _recordRoot => '$_rootKey.record.';

  /// 隔離recordの置き場。`_recordRoot` とは別prefixにして、
  /// compact()やdelete()の物理削除が隔離分を巻き込まないようにする。
  String get _quarantineRoot => '$_rootKey.quarantine.';

  @override
  Future<LocalConversationDocument> create({
    String firstMessage = '',
    String? conversationId,
  }) => _mutex.protect(() async {
    final manifest = await _readManifest();
    final id = await _availableId(manifest, requested: conversationId);
    final now = _now();
    final conversation = <String, dynamic>{
      'schema_version': 1,
      'id': id,
      'title': _titleFromMessage(firstMessage),
      'created_at': now,
      'updated_at': now,
      'memory': <String, dynamic>{'revision': 0, 'text': '', 'updated_at': now},
      'turns': <dynamic>[],
    };
    return _commit(manifest, conversation, expectedStorageRevision: 0);
  });

  @override
  Future<LocalConversationDocument?> read(String conversationId) =>
      _mutex.protect(() async {
        final id = _validateId(conversationId);
        final manifest = await _readManifest();
        final revision = manifest.entries[id];
        if (revision == null) return null;
        return _readCommitted(id, revision);
      });

  @override
  Future<LocalConversationDocument> save(
    Map<String, dynamic> conversation, {
    int? expectedStorageRevision,
  }) => _mutex.protect(() async {
    final snapshot = _cloneMap(conversation, label: 'conversation');
    final id = _validateId(snapshot['id']?.toString() ?? '');
    snapshot['id'] = id;
    final manifest = await _readManifest();
    return _commit(
      manifest,
      snapshot,
      expectedStorageRevision: expectedStorageRevision,
    );
  });

  @override
  Future<LocalConversationListing> list() => _mutex.protect(() async {
    final manifest = await _readManifest();
    final summaries = <LocalConversationSummary>[];
    final defects = <LocalConversationDefect>[...manifest.defects];
    for (final entry in manifest.entries.entries) {
      final read = await _tryReadCommitted(entry.key, entry.value);
      switch (read) {
        case _RecordOk(:final document):
          summaries.add(LocalConversationSummary.fromDocument(document));
        case _RecordCorrupt(:final reason):
          defects.add(
            LocalConversationDefect(
              conversationId: entry.key,
              storageRevision: entry.value,
              reason: reason,
            ),
          );
      }
    }
    _sortSummaries(summaries);
    return LocalConversationListing(
      items: List.unmodifiable(summaries),
      defects: List.unmodifiable(defects),
    );
  });

  @override
  Future<LocalConversationListing> search(String query, {int limit = 30}) =>
      _mutex.protect(() async {
        final terms = query
            .trim()
            .toLowerCase()
            .split(RegExp(r'\s+'))
            .where((term) => term.isNotEmpty)
            .toList(growable: false);
        if (terms.isEmpty) return LocalConversationListing.empty;
        final boundedLimit = limit.clamp(1, 100);
        final manifest = await _readManifest();
        final matches = <LocalConversationSummary>[];
        final defects = <LocalConversationDefect>[...manifest.defects];
        for (final entry in manifest.entries.entries) {
          final read = await _tryReadCommitted(entry.key, entry.value);
          switch (read) {
            case _RecordOk(:final document):
              final haystack = _searchText(document.value).toLowerCase();
              if (terms.every(haystack.contains)) {
                matches.add(LocalConversationSummary.fromDocument(document));
              }
            case _RecordCorrupt(:final reason):
              // 検索できない会話を黙って落とすと「消えた」ように見える。
              defects.add(
                LocalConversationDefect(
                  conversationId: entry.key,
                  storageRevision: entry.value,
                  reason: reason,
                ),
              );
          }
        }
        _sortSummaries(matches);
        return LocalConversationListing(
          items: List.unmodifiable(matches.take(boundedLimit)),
          defects: List.unmodifiable(defects),
        );
      });

  static void _sortSummaries(List<LocalConversationSummary> summaries) {
    summaries.sort((left, right) {
      final byUpdated = right.updatedAt.compareTo(left.updatedAt);
      return byUpdated != 0 ? byUpdated : left.id.compareTo(right.id);
    });
  }

  @override
  Future<LocalConversationDocument> rename(
    String conversationId,
    String title, {
    int? expectedStorageRevision,
  }) => _mutex.protect(() async {
    final cleaned = title.trim();
    if (cleaned.isEmpty) throw ArgumentError.value(title, 'title', '空です');
    final manifest = await _readManifest();
    final current = await _requiredDocument(manifest, conversationId);
    _checkExpectedRevision(current, expectedStorageRevision);
    final updated = _cloneMap(current.value, label: 'conversation');
    updated['title'] = _truncate(cleaned, 120);
    return _commit(
      manifest,
      updated,
      expectedStorageRevision: current.storageRevision,
    );
  });

  @override
  Future<void> delete(String conversationId, {int? expectedStorageRevision}) =>
      _mutex.protect(() async {
        final id = _validateId(conversationId);
        final manifest = await _readManifest();
        final revision = manifest.entries[id];
        if (revision == null) throw LocalConversationNotFound(id);
        if (expectedStorageRevision != null &&
            expectedStorageRevision != revision) {
          throw LocalConversationConflict(
            conversationId: id,
            expectedRevision: expectedStorageRevision,
            actualRevision: revision,
          );
        }
        final entries = Map<String, int>.from(manifest.entries)..remove(id);
        await _writeManifest(
          _Manifest(revision: manifest.revision + 1, entries: entries),
        );
        await _removeRecordFamily(id);
      });

  @override
  Future<LocalConversationDocument> fork({
    required String conversationId,
    required int beforeTurnIndex,
    required String parentTurnRequestId,
    String? branchConversationId,
    int? expectedStorageRevision,
  }) => _mutex.protect(() async {
    final manifest = await _readManifest();
    final parent = await _requiredDocument(manifest, conversationId);
    _checkExpectedRevision(parent, expectedStorageRevision);
    final turns = parent.value['turns'];
    if (turns is! List ||
        beforeTurnIndex < 0 ||
        beforeTurnIndex >= turns.length) {
      throw RangeError.range(
        beforeTurnIndex,
        0,
        turns is List && turns.isNotEmpty ? turns.length - 1 : 0,
        'beforeTurnIndex',
      );
    }
    final target = turns[beforeTurnIndex];
    if (target is! Map ||
        target['request_id']?.toString() != parentTurnRequestId) {
      throw StateError('分岐対象のrequest IDが現在の会話と一致しません。');
    }
    final id = await _availableId(manifest, requested: branchConversationId);
    final now = _now();
    final parentTitle = parent.value['title']?.toString().trim();
    final memory = parent.value['memory'];
    final branch = <String, dynamic>{
      'schema_version': 1,
      'id': id,
      'title': _truncate(
        '${parentTitle == null || parentTitle.isEmpty ? '新しい会話' : parentTitle} · 分岐',
        120,
      ),
      'created_at': now,
      'updated_at': now,
      'memory': memory is Map
          ? _cloneMap(Map<String, dynamic>.from(memory), label: 'memory')
          : <String, dynamic>{'revision': 0, 'text': '', 'updated_at': now},
      'turns': _cloneList(turns.take(beforeTurnIndex).toList()),
      'branch': <String, dynamic>{
        'parent_conversation_id': parent.id,
        'parent_turn_request_id': parentTurnRequestId,
        'forked_at': now,
        'copied_turn_count': beforeTurnIndex,
      },
    };
    return _commit(manifest, branch, expectedStorageRevision: 0);
  });

  @override
  Future<LocalConversationDocument> updateMemory({
    required String conversationId,
    required int expectedMemoryRevision,
    required String text,
    int? expectedStorageRevision,
  }) => _mutex.protect(() async {
    if (text.runes.length > maxMemoryCharacters) {
      throw ArgumentError.value(
        text,
        'text',
        '$maxMemoryCharacters文字以下にしてください',
      );
    }
    final manifest = await _readManifest();
    final current = await _requiredDocument(manifest, conversationId);
    _checkExpectedRevision(current, expectedStorageRevision);
    final updated = _cloneMap(current.value, label: 'conversation');
    final rawMemory = updated['memory'];
    final memory = rawMemory is Map
        ? Map<String, dynamic>.from(rawMemory)
        : <String, dynamic>{};
    final actualMemoryRevision = _nonNegativeInt(memory['revision']);
    if (actualMemoryRevision != expectedMemoryRevision) {
      throw LocalConversationConflict(
        conversationId: current.id,
        expectedRevision: expectedMemoryRevision,
        actualRevision: actualMemoryRevision,
      );
    }
    updated['memory'] = <String, dynamic>{
      ...memory,
      'revision': actualMemoryRevision + 1,
      'text': text,
      'updated_at': _now(),
    };
    return _commit(
      manifest,
      updated,
      expectedStorageRevision: current.storageRevision,
    );
  });

  @override
  Future<String> exportJson(String conversationId) => _mutex.protect(() async {
    final manifest = await _readManifest();
    final document = await _requiredDocument(manifest, conversationId);
    return '${const JsonEncoder.withIndent('  ').convert(document.value)}\n';
  });

  @override
  Future<int> quarantine(Iterable<String> conversationIds) =>
      _mutex.protect(() async {
        final requested = <String>{};
        for (final raw in conversationIds) {
          final id = raw.trim();
          if (id.isNotEmpty) requested.add(id);
        }
        if (requested.isEmpty) return 0;
        final manifest = await _readManifest();
        final entries = Map<String, int>.from(manifest.entries);
        final moved = <String, int>{};
        for (final id in requested) {
          final revision = entries.remove(id);
          if (revision != null) moved[id] = revision;
        }
        // manifest entry自体が壊れていたIDも、manifestから外れる時点で
        // 隔離済みとして数える(次の書き出しで不正entryは残らない)。
        final defective = manifest.defects
            .where((defect) => requested.contains(defect.conversationId))
            .length;
        if (moved.isEmpty && defective == 0) return 0;
        await _writeManifest(
          _Manifest(revision: manifest.revision + 1, entries: entries),
        );
        for (final entry in moved.entries) {
          await _moveToQuarantine(entry.key, entry.value);
        }
        return moved.length + defective;
      });

  @override
  Future<int> rebuildManifestFromRecords() => _mutex.protect(() async {
    final manifest = await _readManifest();
    final keys = await _valueStore.keys();
    final recovered = <String, int>{};
    for (final key in keys.where((key) => key.startsWith(_recordRoot))) {
      final raw = await _valueStore.read(key);
      if (raw == null || raw.isEmpty) continue;
      dynamic decoded;
      try {
        decoded = jsonDecode(raw);
      } on FormatException {
        continue;
      }
      if (decoded is! Map ||
          decoded['version'] != _formatVersion ||
          decoded['namespace'] != namespace.identity ||
          decoded['value'] is! Map) {
        continue;
      }
      final id = decoded['conversation_id']?.toString() ?? '';
      final revision = _positiveInt(decoded['storage_revision']);
      if (revision == null || key != _recordKey(id, revision)) continue;
      try {
        _validateId(id);
      } on ArgumentError {
        continue;
      }
      final value = decoded['value'] as Map;
      if (value['id']?.toString() != id) continue;
      final current = recovered[id];
      if (current == null || revision > current) recovered[id] = revision;
    }
    await _writeManifest(
      _Manifest(revision: manifest.revision + 1, entries: recovered),
    );
    return recovered.length;
  });

  @override
  Future<void> compact() => _mutex.protect(() async {
    final manifest = await _readManifest();
    // 破損entryが残っている間は、隔離・再構築で救出できる可能性のある
    // recordを物理削除しない。
    if (manifest.defects.isNotEmpty) return;
    final retained = <String>{
      for (final entry in manifest.entries.entries)
        _recordKey(entry.key, entry.value),
    };
    final keys = await _valueStore.keys();
    for (final key in keys) {
      if (key.startsWith(_recordRoot) && !retained.contains(key)) {
        await _valueStore.remove(key);
      }
    }
  });

  Future<void> _moveToQuarantine(String id, int revision) async {
    final source = _recordKey(id, revision);
    final raw = await _valueStore.read(source);
    if (raw == null) return;
    // 端末が正本なので中身は捨てない。読める形に戻せる可能性を残す。
    await _valueStore.write(_quarantineKey(id, revision), raw);
    await _valueStore.remove(source);
  }

  Future<LocalConversationDocument> _commit(
    _Manifest manifest,
    Map<String, dynamic> rawConversation, {
    int? expectedStorageRevision,
  }) async {
    final conversation = _normalizeConversation(rawConversation);
    final id = _validateId(conversation['id']?.toString() ?? '');
    final actualRevision = manifest.entries[id] ?? 0;
    if (expectedStorageRevision != null &&
        expectedStorageRevision != actualRevision) {
      throw LocalConversationConflict(
        conversationId: id,
        expectedRevision: expectedStorageRevision,
        actualRevision: actualRevision,
      );
    }

    if (actualRevision > 0) {
      final current = await _readCommitted(id, actualRevision);
      conversation['created_at'] =
          current.value['created_at']?.toString() ?? _now();
    } else {
      conversation['created_at'] =
          conversation['created_at']?.toString() ?? _now();
    }
    conversation['updated_at'] = _now();

    final nextRevision = actualRevision + 1;
    final recordKey = _recordKey(id, nextRevision);
    final record = <String, dynamic>{
      'version': _formatVersion,
      'namespace': namespace.identity,
      'conversation_id': id,
      'storage_revision': nextRevision,
      'value': conversation,
    };
    final encoded = jsonEncode(record);
    if (utf8.encode(encoded).length > maxConversationBytes) {
      throw StateError('会話データがローカル保存上限の$maxConversationBytes byteを超えています。');
    }

    await _valueStore.write(recordKey, encoded);
    final entries = Map<String, int>.from(manifest.entries)
      ..[id] = nextRevision;
    try {
      await _writeManifest(
        _Manifest(revision: manifest.revision + 1, entries: entries),
      );
    } catch (_) {
      // The immutable record was never published. Cleanup is best-effort; a
      // crash orphan is harmless and [compact] removes it later.
      try {
        await _valueStore.remove(recordKey);
      } catch (_) {}
      rethrow;
    }
    if (actualRevision > 0) {
      try {
        await _valueStore.remove(_recordKey(id, actualRevision));
      } catch (_) {
        // The manifest already committed the new revision. Keep success
        // monotonic and leave cleanup to compact().
      }
    }
    return LocalConversationDocument(
      value: _cloneMap(conversation, label: 'conversation'),
      storageRevision: nextRevision,
    );
  }

  Future<_Manifest> _readManifest() async {
    final raw = await _valueStore.read(_manifestKey);
    if (raw == null || raw.isEmpty) return const _Manifest();
    dynamic decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      throw const LocalConversationCorruption('manifestがJSONではありません');
    }
    if (decoded is! Map ||
        decoded['version'] != _formatVersion ||
        decoded['namespace'] != namespace.identity) {
      throw const LocalConversationCorruption('manifestの形式またはnamespaceが不正です');
    }
    final revision = _strictNonNegativeInt(decoded['manifest_revision']);
    if (revision == null) {
      throw const LocalConversationCorruption('manifest revisionが不正です');
    }
    final rawEntries = decoded['entries'];
    if (rawEntries is! Map) {
      throw const LocalConversationCorruption('manifest indexが不正です');
    }
    final entries = <String, int>{};
    final defects = <LocalConversationDefect>[];
    for (final entry in rawEntries.entries) {
      final id = entry.key.toString();
      final recordRevision = _positiveInt(entry.value);
      // 1件のentry破損で全会話を失わない。壊れたentryはdefectとして持ち回り、
      // 隔離またはmanifest再構築というユーザー操作で解消させる。
      try {
        _validateId(id);
      } on ArgumentError {
        defects.add(
          LocalConversationDefect(
            conversationId: id,
            storageRevision: 0,
            reason: 'manifest entryのIDが不正です',
          ),
        );
        continue;
      }
      if (recordRevision == null) {
        defects.add(
          LocalConversationDefect(
            conversationId: id,
            storageRevision: 0,
            reason: 'manifest entryのrevisionが不正です',
          ),
        );
        continue;
      }
      entries[id] = recordRevision;
    }
    return _Manifest(
      revision: revision,
      entries: entries,
      defects: List.unmodifiable(defects),
    );
  }

  Future<void> _writeManifest(_Manifest manifest) async {
    final sortedIds = manifest.entries.keys.toList()..sort();
    await _valueStore.write(
      _manifestKey,
      jsonEncode(<String, dynamic>{
        'version': _formatVersion,
        'namespace': namespace.identity,
        'manifest_revision': manifest.revision,
        'entries': <String, int>{
          for (final id in sortedIds) id: manifest.entries[id]!,
        },
      }),
    );
  }

  /// 特定の会話を開く経路。破損はfail-closedのまま例外にする。
  Future<LocalConversationDocument> _readCommitted(
    String id,
    int revision,
  ) async {
    final read = await _tryReadCommitted(id, revision);
    return switch (read) {
      _RecordOk(:final document) => document,
      _RecordCorrupt(:final reason) => throw LocalConversationCorruption(
        '$reason: $id@$revision',
      ),
    };
  }

  /// 一覧・検索用の読み取り。例外を制御フローに使わず結果で返す。
  Future<_RecordRead> _tryReadCommitted(String id, int revision) async {
    String? raw;
    try {
      raw = await _valueStore.read(_recordKey(id, revision));
    } catch (error) {
      return _RecordCorrupt('会話recordを読み出せません($error)');
    }
    if (raw == null || raw.isEmpty) {
      return const _RecordCorrupt('manifestが参照する会話recordがありません');
    }
    dynamic decoded;
    try {
      decoded = jsonDecode(raw);
    } on FormatException {
      return const _RecordCorrupt('会話recordがJSONではありません');
    }
    if (decoded is! Map ||
        decoded['version'] != _formatVersion ||
        decoded['namespace'] != namespace.identity ||
        decoded['conversation_id'] != id ||
        decoded['storage_revision'] != revision ||
        decoded['value'] is! Map) {
      return const _RecordCorrupt('会話recordの形式が不正です');
    }
    final Map<String, dynamic> value;
    try {
      value = _cloneMap(
        Map<String, dynamic>.from(decoded['value'] as Map),
        label: 'conversation',
      );
    } on ArgumentError {
      return const _RecordCorrupt('会話recordの本文がJSON objectではありません');
    }
    if (value['id']?.toString() != id) {
      return const _RecordCorrupt('会話record内のIDが一致しません');
    }
    return _RecordOk(
      LocalConversationDocument(value: value, storageRevision: revision),
    );
  }

  Future<LocalConversationDocument> _requiredDocument(
    _Manifest manifest,
    String conversationId,
  ) async {
    final id = _validateId(conversationId);
    final revision = manifest.entries[id];
    if (revision == null) throw LocalConversationNotFound(id);
    return _readCommitted(id, revision);
  }

  void _checkExpectedRevision(
    LocalConversationDocument current,
    int? expected,
  ) {
    if (expected == null || expected == current.storageRevision) return;
    throw LocalConversationConflict(
      conversationId: current.id,
      expectedRevision: expected,
      actualRevision: current.storageRevision,
    );
  }

  Map<String, dynamic> _normalizeConversation(Map<String, dynamic> raw) {
    final value = _cloneMap(raw, label: 'conversation');
    final id = _validateId(value['id']?.toString() ?? '');
    value['id'] = id;
    value['schema_version'] = _positiveInt(value['schema_version']) ?? 1;
    final title = value['title']?.toString().trim() ?? '';
    value['title'] = _truncate(title.isEmpty ? '新しい会話' : title, 120);
    if (value['turns'] is! List) value['turns'] = <dynamic>[];
    if (value['memory'] is! Map) {
      value['memory'] = <String, dynamic>{
        'revision': 0,
        'text': '',
        'updated_at': _now(),
      };
    }
    return value;
  }

  String _searchText(Map<String, dynamic> conversation) {
    final chunks = <String>[conversation['title']?.toString() ?? ''];
    final memory = conversation['memory'];
    if (memory is Map) chunks.add(memory['text']?.toString() ?? '');
    final turns = conversation['turns'];
    if (turns is List) {
      for (final turn in turns.whereType<Map>()) {
        chunks
          ..add(turn['message']?.toString() ?? '')
          ..add(turn['clean_message']?.toString() ?? '');
        final answers = turn['answers'];
        if (answers is Map) {
          for (final answer in answers.values.whereType<Map>()) {
            chunks.add(answer['text']?.toString() ?? '');
          }
        }
        final synthesis = turn['synthesis'];
        if (synthesis is Map) {
          chunks.add(synthesis['text']?.toString() ?? '');
        }
      }
    }
    return chunks.join('\n');
  }

  Future<String> _availableId(_Manifest manifest, {String? requested}) async {
    if (requested != null) {
      final id = _validateId(requested);
      if (manifest.entries.containsKey(id)) {
        throw LocalConversationConflict(
          conversationId: id,
          expectedRevision: 0,
          actualRevision: manifest.entries[id]!,
        );
      }
      return id;
    }
    for (var attempt = 0; attempt < 8; attempt++) {
      final id = _validateId(_idFactory());
      if (!manifest.entries.containsKey(id)) return id;
    }
    throw StateError('一意なローカル会話IDを生成できませんでした。');
  }

  Future<void> _removeRecordFamily(String conversationId) async {
    final prefix = _recordPrefix(conversationId);
    late final Set<String> keys;
    try {
      keys = await _valueStore.keys();
    } catch (_) {
      // manifest commit後の物理削除はbest-effort。compact()で再試行する。
      return;
    }
    for (final key in keys.where((key) => key.startsWith(prefix))) {
      try {
        await _valueStore.remove(key);
      } catch (_) {
        // The manifest already made the conversation unreachable. A later
        // compact() retries physical cleanup.
      }
    }
  }

  String _recordPrefix(String id) =>
      '$_recordRoot${base64Url.encode(utf8.encode(id))}.';

  String _recordKey(String id, int revision) => '${_recordPrefix(id)}$revision';

  String _quarantineKey(String id, int revision) =>
      '$_quarantineRoot${base64Url.encode(utf8.encode(id))}.$revision';

  String _now() {
    final utc = _clock().toUtc();
    return DateTime.fromMillisecondsSinceEpoch(
      utc.millisecondsSinceEpoch,
      isUtc: true,
    ).toIso8601String();
  }

  static String _rootFor(LocalConversationNamespace namespace) =>
      'clage.local.conversations.v1.'
      '${base64Url.encode(utf8.encode(namespace.identity))}';

  static _AsyncMutex _mutexFor(String root) =>
      _mutexes.putIfAbsent(root, _AsyncMutex.new);
}

class _Manifest {
  const _Manifest({
    this.revision = 0,
    this.entries = const <String, int>{},
    this.defects = const <LocalConversationDefect>[],
  });

  final int revision;
  final Map<String, int> entries;

  /// 解釈できなかったmanifest entry。書き戻すと消えるため、
  /// [SharedPreferencesLocalConversationRepository.compact] は
  /// これが残っている間、物理削除を行わない。
  final List<LocalConversationDefect> defects;
}

/// recordの読み取り結果。例外を制御フローに使わないための小さな型。
sealed class _RecordRead {
  const _RecordRead();
}

class _RecordOk extends _RecordRead {
  const _RecordOk(this.document);

  final LocalConversationDocument document;
}

class _RecordCorrupt extends _RecordRead {
  const _RecordCorrupt(this.reason);

  final String reason;
}

class _AsyncMutex {
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

String _validateId(String value) {
  final id = value.trim();
  if (id.isEmpty || id.runes.length > 200) {
    throw ArgumentError.value(value, 'conversationId', '空または長すぎます');
  }
  return id;
}

Map<String, dynamic> _cloneMap(
  Map<String, dynamic> value, {
  required String label,
}) {
  try {
    final decoded = jsonDecode(jsonEncode(value));
    if (decoded is Map) return Map<String, dynamic>.from(decoded);
  } on JsonUnsupportedObjectError catch (error) {
    throw ArgumentError.value(value, label, error.toString());
  }
  throw ArgumentError.value(value, label, 'JSON objectではありません');
}

List<dynamic> _cloneList(List<dynamic> value) {
  try {
    final decoded = jsonDecode(jsonEncode(value));
    if (decoded is List) return List<dynamic>.from(decoded);
  } on JsonUnsupportedObjectError catch (error) {
    throw ArgumentError.value(value, 'value', error.toString());
  }
  throw ArgumentError.value(value, 'value', 'JSON arrayではありません');
}

int _nonNegativeInt(dynamic value) {
  if (value is int && value >= 0) return value;
  if (value is num && value >= 0) return value.toInt();
  return int.tryParse(value?.toString() ?? '')?.clamp(0, 0x7fffffff) ?? 0;
}

int? _strictNonNegativeInt(dynamic value) {
  if (value is bool) return null;
  if (value is int) return value >= 0 ? value : null;
  if (value is String && RegExp(r'^\d+$').hasMatch(value)) {
    return int.tryParse(value);
  }
  return null;
}

int? _positiveInt(dynamic value) {
  if (value is bool) return null;
  final parsed = value is num
      ? value.toInt()
      : int.tryParse(value?.toString() ?? '');
  return parsed != null && parsed > 0 ? parsed : null;
}

String _titleFromMessage(String message) {
  final cleaned = message.replaceAll(RegExp(r'\s+'), ' ').trim();
  return cleaned.isEmpty ? '新しい会話' : _truncate(cleaned, 60);
}

String _truncate(String value, int maxCharacters) =>
    String.fromCharCodes(value.runes.take(maxCharacters));

String _uuidV4() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  final hex = bytes
      .map((byte) => byte.toRadixString(16).padLeft(2, '0'))
      .join();
  return '${hex.substring(0, 8)}-'
      '${hex.substring(8, 12)}-'
      '${hex.substring(12, 16)}-'
      '${hex.substring(16, 20)}-'
      '${hex.substring(20)}';
}
