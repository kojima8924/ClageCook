/// 外部送信前に秘密・個人情報らしい文字列を検出する端末内スキャナ。
///
/// reference server の `backend/policy.py` と同じrule集合・同じ判定・同じ
/// version文字列を返す。既定の実行方式であるDirect BYOKの方が検出が弱い、
/// という以前の状態を避けるため、rule表の唯一の仕様は `docs/policy_rules.json`
/// に置き、両実装がそれと一致することを回帰テストで固定している。
library;

/// 1件の検出ルール。
class PolicyRule {
  const PolicyRule({
    required this.ruleId,
    required this.label,
    required this.severity,
    required this.pattern,
    this.secretGroup = 0,
  });

  final String ruleId;
  final String label;

  /// 'block'(外部送信すべきでない候補) または 'confirm'(利用者確認が要る候補)。
  final String severity;

  final RegExp pattern;

  /// 伏せ字にする捕捉group。0なら一致全体。
  ///
  /// 0以外のgroupは必ず一致全体の末尾に置く。Dartのregexpは group単位の
  /// 位置を返さないため、位置は `一致end - group文字列長` で求めている。
  final int secretGroup;
}

/// 判定結果に載せるversion。仕様ファイルと同じ値でなければならない。
const policyRulesVersion = 'local-patterns-v1';

const policyScanDisclaimer = 'ローカルのパターン一致結果です。秘密・個人情報の有無を保証するものではありません。';

/// 検出ルール表。この並びが重複解決の優先順位(先にあるものが具体的)になる。
final List<PolicyRule> policyRules = List.unmodifiable(<PolicyRule>[
  PolicyRule(
    ruleId: 'private_key',
    label: '秘密鍵ブロック',
    severity: 'block',
    // Python側の \Z に相当する終端はDartでは $(non-multiline)。
    pattern: RegExp(
      r'-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----[\s\S]*?'
      r'(?:-----END(?: [A-Z0-9]+)? PRIVATE KEY-----|$)',
      caseSensitive: false,
    ),
  ),
  PolicyRule(
    ruleId: 'anthropic_api_key',
    label: 'Anthropic APIキーらしい文字列',
    severity: 'block',
    pattern: RegExp(r'\bsk-ant-[A-Za-z0-9_-]{16,}\b'),
  ),
  PolicyRule(
    ruleId: 'openai_api_key',
    label: 'OpenAI APIキーらしい文字列',
    severity: 'block',
    pattern: RegExp(
      r'\b(?:sk-(?:proj|svcacct)-[A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{32,})\b',
    ),
  ),
  PolicyRule(
    ruleId: 'google_api_key',
    label: 'Google APIキーらしい文字列',
    severity: 'block',
    pattern: RegExp(r'\bAIza[0-9A-Za-z_-]{30,}\b'),
  ),
  PolicyRule(
    ruleId: 'google_aq_api_key',
    label: 'Google APIキーらしいAQ形式の文字列',
    severity: 'block',
    pattern: RegExp(r'\bAQ\.[0-9A-Za-z_-]{20,}\b'),
  ),
  PolicyRule(
    ruleId: 'xai_api_key',
    label: 'xAI APIキーらしい文字列',
    severity: 'block',
    pattern: RegExp(r'\bxai-[A-Za-z0-9_-]{16,}\b', caseSensitive: false),
  ),
  PolicyRule(
    ruleId: 'github_token',
    label: 'GitHubトークンらしい文字列',
    severity: 'block',
    pattern: RegExp(
      r'\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}\b',
      caseSensitive: false,
    ),
  ),
  PolicyRule(
    ruleId: 'github_fine_grained_token',
    label: 'GitHub fine-grained tokenらしい文字列',
    severity: 'block',
    pattern: RegExp(r'\bgithub_pat_[A-Za-z0-9_]{20,}\b', caseSensitive: false),
  ),
  PolicyRule(
    ruleId: 'aws_access_key',
    label: 'AWSアクセスキーIDらしい文字列',
    severity: 'block',
    pattern: RegExp(r'\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'),
  ),
  PolicyRule(
    ruleId: 'bearer_token',
    label: 'Bearerトークンらしい文字列',
    severity: 'block',
    pattern: RegExp(
      r'\bBearer\s+([A-Za-z0-9._~+/-]{20,}={0,2})',
      caseSensitive: false,
    ),
    secretGroup: 1,
  ),
  PolicyRule(
    ruleId: 'assigned_secret',
    label: '環境変数へ設定された秘密値らしい文字列',
    severity: 'block',
    pattern: RegExp(
      r'\b(?:OPENAI|ANTHROPIC|GEMINI|GOOGLE|XAI|CLAUDE|GROK|CLAGE)'
      r'(?:_[A-Z0-9]+)*_(?:API_)?(?:KEY|TOKEN)\s*[:=]\s*["'
      "'"
      r']?'
      r'([A-Za-z0-9._~+/-]{16,}={0,2})',
      caseSensitive: false,
    ),
    secretGroup: 1,
  ),
  PolicyRule(
    ruleId: 'generic_assigned_secret',
    label: '秘密値用の変数へ設定された文字列',
    severity: 'block',
    pattern: RegExp(
      r'\b(?:AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN|DATABASE_URL|'
      r'[A-Z][A-Z0-9_]{1,64}(?:PASSWORD|PASSWD|SECRET|SECRET_KEY|'
      r'PRIVATE_KEY|ACCESS_TOKEN))\s*[:=]\s*["'
      "'"
      r']?'
      r'([^\s"'
      "'"
      r']{8,})',
      caseSensitive: false,
    ),
    secretGroup: 1,
  ),
  PolicyRule(
    ruleId: 'basic_auth',
    label: 'Basic認証情報らしい文字列',
    severity: 'block',
    pattern: RegExp(
      r'\bBasic\s+([A-Za-z0-9+/]{16,}={0,2})',
      caseSensitive: false,
    ),
    secretGroup: 1,
  ),
  PolicyRule(
    ruleId: 'email_address',
    label: 'メールアドレスらしい文字列',
    severity: 'confirm',
    pattern: RegExp(r'(?<![\w.+-])[\w.+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+'),
  ),
  PolicyRule(
    ruleId: 'phone_number',
    label: '電話番号らしい文字列',
    severity: 'confirm',
    pattern: RegExp(r'(?<!\d)(?:\+?\d[\d ()-]{8,}\d)(?!\d)'),
  ),
]);

/// 秘密・個人情報らしい箇所を検出し、生値を含まない結果を返す。
///
/// 戻り値のkeyは `/api/policy/scan` の応答と同じで、UI側は実行方式の違いを
/// 意識しなくてよい。
Map<String, dynamic> scanPolicyText(String text) {
  final candidates = <_Candidate>[];
  for (var priority = 0; priority < policyRules.length; priority++) {
    final rule = policyRules[priority];
    for (final match in rule.pattern.allMatches(text)) {
      final span = _span(match, rule.secretGroup);
      if (span == null) continue;
      candidates.add(
        _Candidate(
          rule: rule,
          priority: priority,
          start: span.$1,
          end: span.$2,
        ),
      );
    }
  }

  final findings = _withoutOverlaps(candidates);
  var redacted = text;
  for (final finding in findings.reversed) {
    redacted =
        '${redacted.substring(0, finding.start)}'
        '⟪REDACTED:${finding.rule.ruleId}⟫'
        '${redacted.substring(finding.end)}';
  }
  final action = findings.any((item) => item.rule.severity == 'block')
      ? 'block'
      : findings.isNotEmpty
      ? 'confirm'
      : 'allow';
  return <String, dynamic>{
    'version': policyRulesVersion,
    'action': action,
    'findings': [
      for (final finding in findings)
        <String, dynamic>{
          'rule_id': finding.rule.ruleId,
          'label': finding.rule.label,
          'severity': finding.rule.severity,
          'start': finding.start,
          'end': finding.end,
        },
    ],
    'redacted_text': redacted,
    'disclaimer': policyScanDisclaimer,
  };
}

/// 履歴・メモをProviderへ渡す前に伏せ字化する。allowならそのまま返す。
String redactPolicySecrets(String text) {
  final scan = scanPolicyText(text);
  return scan['action'] == 'allow'
      ? text
      : scan['redacted_text']?.toString() ?? '';
}

/// 一致範囲を求める。[secretGroup] が0以外なら、末尾のgroupだけを対象にする。
(int, int)? _span(RegExpMatch match, int secretGroup) {
  if (secretGroup == 0) {
    return match.start == match.end ? null : (match.start, match.end);
  }
  final secret = match.group(secretGroup);
  if (secret == null || secret.isEmpty) return null;
  return (match.end - secret.length, match.end);
}

/// 重複候補は重大度、具体的な規則、長い一致の順で1件へ畳む。
///
/// backend/policy.py の `_without_overlaps` と同じ順序規則。
List<_Candidate> _withoutOverlaps(List<_Candidate> candidates) {
  const severityRank = <String, int>{'block': 0, 'confirm': 1};
  final ordered = [...candidates]
    ..sort((left, right) {
      final bySeverity = (severityRank[left.rule.severity] ?? 99).compareTo(
        severityRank[right.rule.severity] ?? 99,
      );
      if (bySeverity != 0) return bySeverity;
      final byPriority = left.priority.compareTo(right.priority);
      if (byPriority != 0) return byPriority;
      final byLength = (right.end - right.start).compareTo(
        left.end - left.start,
      );
      if (byLength != 0) return byLength;
      return left.start.compareTo(right.start);
    });
  final selected = <_Candidate>[];
  for (final item in ordered) {
    final overlaps = selected.any(
      (existing) => item.start < existing.end && existing.start < item.end,
    );
    if (overlaps) continue;
    selected.add(item);
  }
  selected.sort((left, right) {
    final byStart = left.start.compareTo(right.start);
    return byStart != 0 ? byStart : left.end.compareTo(right.end);
  });
  return selected;
}

class _Candidate {
  const _Candidate({
    required this.rule,
    required this.priority,
    required this.start,
    required this.end,
  });

  final PolicyRule rule;
  final int priority;
  final int start;
  final int end;
}
