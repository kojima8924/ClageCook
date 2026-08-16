// DirectByokClientが使う純粋関数。以前はクラスのstatic memberだったが、
// 分割後のpartからも同じ名前で呼べるようlibrary privateの関数にした。
part of '../direct_byok_client.dart';

Map<String, dynamic>? _findAttempt(
  Map<String, dynamic> turn,
  String attemptId,
) {
  final attempts = turn['attempts'];
  if (attempts is! List) return null;
  for (final raw in attempts) {
    if (raw is Map && raw['attempt_id']?.toString() == attemptId) {
      return Map<String, dynamic>.from(raw);
    }
  }
  return null;
}

bool _sameRegeneration(
  Map<String, dynamic> attempt,
  String target,
  String provider,
) =>
    attempt['target']?.toString() == target &&
    attempt['provider']?.toString() == provider;

Map<String, Map<String, dynamic>> _completedAnswers(Map<String, dynamic> turn) {
  final result = <String, Map<String, dynamic>>{};
  final answers = turn['answers'];
  if (answers is! Map) return result;
  for (final entry in answers.entries) {
    if (entry.value is Map && entry.value['ok'] == true) {
      result[entry.key.toString()] = Map<String, dynamic>.from(
        entry.value as Map,
      );
    }
  }
  return result;
}

List<String> _turnAttachmentIds(Map<String, dynamic> turn) {
  final result = <String>[];
  final seen = <String>{};
  final resume = turn['resume_request'];
  final resumeIds = resume is Map ? resume['attachment_ids'] : null;
  if (resumeIds is List) {
    for (final raw in resumeIds) {
      final id = raw?.toString().trim() ?? '';
      if (id.isNotEmpty && seen.add(id)) result.add(id);
    }
  }
  final references = turn['attachments'];
  if (references is List) {
    for (final raw in references) {
      final id = raw is Map ? raw['id']?.toString().trim() ?? '' : '';
      if (id.isNotEmpty && seen.add(id)) result.add(id);
    }
    if (references.isNotEmpty && result.isEmpty) {
      throw const ApiException('元の添付を識別できないため、再生成を実行しません。');
    }
  }
  return result;
}

void _requirePolicyConfirmation(
  Map<String, dynamic> policy, {
  required bool confirmSensitiveData,
}) {
  if (policy['action'] == 'block') {
    throw const ApiException('秘密情報らしい文字列が含まれるため送信しません。');
  }
  if (policy['action'] == 'confirm' && !confirmSensitiveData) {
    throw const ApiException('個人情報らしい文字列の送信確認が必要です。');
  }
}

ConversationSummary _summary(LocalConversationSummary value) =>
    ConversationSummary(
      id: value.id,
      title: value.title,
      updatedAt: value.updatedAt,
      turnCount: value.turnCount,
      preview: value.preview,
    );

Map<String, dynamic> _turnById(
  Map<String, dynamic> conversation,
  String requestId,
) {
  for (final turn in _mapList(conversation['turns'])) {
    if (turn['request_id'] == requestId) return turn;
  }
  throw const ApiException('対象ターンが見つかりません。');
}

void _recordAttempt(
  Map<String, dynamic> turn,
  String targetKey,
  String target,
  String provider,
  dynamic original,
  Map<String, dynamic> result, {
  required String attemptId,
}) {
  final attempts = turn['attempts'] is List
      ? List<dynamic>.from(turn['attempts'] as List)
      : <dynamic>[];
  final active = turn['active_attempts'] is Map
      ? Map<String, dynamic>.from(turn['active_attempts'] as Map)
      : <String, dynamic>{};
  var parent = active[targetKey]?.toString();
  if (parent == null || parent.isEmpty) {
    parent = _newId('original');
    attempts.add({
      'attempt_id': parent,
      'parent_attempt_id': null,
      'target': target,
      'provider': provider,
      'status': 'completed',
      'created_at': _now(),
      'completed_at': _now(),
      'original': true,
      'result': original is Map
          ? Map<String, dynamic>.from(original)
          : const {},
    });
  }
  final status = result['completion_status'] == 'cancelled'
      ? 'interrupted'
      : result['ok'] == true
      ? 'completed'
      : 'failed';
  attempts.add({
    'attempt_id': attemptId,
    'parent_attempt_id': parent,
    'target': target,
    'provider': provider,
    'status': status,
    'created_at': _now(),
    'completed_at': _now(),
    'cancelled': status == 'interrupted',
    'original': false,
    'result': _cloneMap(result),
  });
  active[targetKey] = attemptId;
  turn['attempts'] = attempts;
  turn['active_attempts'] = active;
}

ReasoningMode _parseReasoning(String value) => switch (value) {
  'low' => ReasoningMode.low,
  'medium' => ReasoningMode.medium,
  'high' => ReasoningMode.high,
  _ => ReasoningMode.auto,
};

DirectProvider? _providerByName(String value) {
  for (final provider in DirectProvider.values) {
    if (provider.name == value) return provider;
  }
  return null;
}

Map<String, dynamic> _failureAnswer(
  DirectProvider provider,
  String message,
  int round, {
  String errorCode = '',
  String errorStage = '',
  bool usageMayBeIncomplete = false,
  Map<String, dynamic> requestAudit = const {},
}) => {
  'source': provider.name,
  'ok': false,
  'text': '',
  'error': message,
  if (errorCode.isNotEmpty) 'error_code': errorCode,
  if (errorStage.isNotEmpty) 'error_stage': errorStage,
  'completion_status': 'failed',
  'partial': false,
  'usage_may_be_incomplete': usageMayBeIncomplete,
  'request_audit': requestAudit,
  'round': round,
};

String _safeRunError(Object error) {
  if (error is DirectProviderException ||
      error is DirectRunGuardStartException ||
      error is DirectRunGuardDuplicateJobException ||
      error is ApiException) {
    return error.toString();
  }
  return 'Direct BYOKの処理に失敗しました。';
}

Map<String, int> _mergeUsage(dynamic first, dynamic second) {
  final result = <String, int>{};
  for (final raw in [first, second]) {
    if (raw is! Map) continue;
    for (final entry in raw.entries) {
      if (entry.value is int) {
        result[entry.key.toString()] =
            (result[entry.key.toString()] ?? 0) + entry.value as int;
      }
    }
  }
  return result;
}

List<Map<String, dynamic>> _mapList(dynamic value) => value is List
    ? value
          .whereType<Map>()
          .map((item) => Map<String, dynamic>.from(item))
          .toList()
    : <Map<String, dynamic>>[];

Map<String, dynamic> _cloneMap(Map<dynamic, dynamic> value) =>
    Map<String, dynamic>.from(jsonDecode(jsonEncode(value)) as Map);

Map<String, Map<String, dynamic>> _cloneMapMap(
  Map<String, Map<String, dynamic>> value,
) => {for (final entry in value.entries) entry.key: _cloneMap(entry.value)};

String _titleFromMessage(String message) {
  final clean = message.replaceAll(RegExp(r'\s+'), ' ').trim();
  if (clean.isEmpty) return '新しい会話';
  return String.fromCharCodes(clean.runes.take(60));
}

String _now() => DateTime.now().toUtc().toIso8601String();

String _newId(String prefix) {
  final random = Random.secure();
  final time = DateTime.now().microsecondsSinceEpoch.toRadixString(16);
  final suffix = List.generate(
    3,
    (_) => random.nextInt(0x7fffffff).toRadixString(16).padLeft(8, '0'),
  ).join();
  return '$prefix-$time-$suffix';
}
