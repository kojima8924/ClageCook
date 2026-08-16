import 'dart:convert';
import 'dart:io';

import 'package:clage_cook/services/policy_scanner.dart';
import 'package:flutter_test/flutter_test.dart';

/// Direct BYOKの秘密検出が reference server と同じ強さであることを固定する。
///
/// rule表の唯一の仕様は docs/policy_rules.json で、backend側にも同じ仕様と
/// 突き合わせるテストがある。片方だけにruleが増える/減る状態を検知する。
Map<String, dynamic> _loadSpec() {
  final file = File('../docs/policy_rules.json');
  if (!file.existsSync()) {
    throw StateError(
      'docs/policy_rules.json が見つかりません(cwd=${Directory.current.path})',
    );
  }
  return jsonDecode(file.readAsStringSync()) as Map<String, dynamic>;
}

void main() {
  final spec = _loadSpec();
  final specRules = (spec['rules'] as List).cast<Map<String, dynamic>>();

  group('policy_scanner と docs/policy_rules.json の一致', () {
    test('versionが仕様と一致する', () {
      expect(policyRulesVersion, spec['version']);
    });

    test('rule_idの集合と並びが仕様と一致する', () {
      expect(
        policyRules.map((rule) => rule.ruleId).toList(),
        specRules.map((rule) => rule['rule_id']).toList(),
      );
    });

    test('label・severity・secret_group・patternが仕様と一致する', () {
      for (var index = 0; index < specRules.length; index++) {
        final expected = specRules[index];
        final actual = policyRules[index];
        final ruleId = expected['rule_id'];
        expect(actual.label, expected['label'], reason: ruleId);
        expect(actual.severity, expected['severity'], reason: ruleId);
        expect(actual.secretGroup, expected['secret_group'], reason: ruleId);
        expect(
          actual.pattern.pattern,
          expected['pattern_dart'] ?? expected['pattern'],
          reason: ruleId,
        );
        final flags = (expected['flags'] as List).cast<String>();
        expect(
          actual.pattern.isCaseSensitive,
          !flags.contains('i'),
          reason: ruleId,
        );
      }
    });
  });

  group('scanPolicyTextは仕様のsampleと同じ判定を返す', () {
    for (final raw in (spec['samples'] as List).cast<Map<String, dynamic>>()) {
      test(raw['name'] as String, () {
        final result = scanPolicyText(raw['text'] as String);
        expect(result['action'], raw['action']);
        expect(result['redacted_text'], raw['redacted_text']);
        expect(
          (result['findings'] as List)
              .cast<Map<String, dynamic>>()
              .map(
                (finding) => <String, dynamic>{
                  'rule_id': finding['rule_id'],
                  'start': finding['start'],
                  'end': finding['end'],
                },
              )
              .toList(),
          raw['findings'],
        );
      });
    }
  });

  test('検出結果は生値を含まない', () {
    const secret = 'ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    final result = scanPolicyText('token=$secret');
    expect(result['action'], 'block');
    expect(result['redacted_text'], isNot(contains(secret)));
    for (final finding in (result['findings'] as List).cast<Map>()) {
      expect(finding.containsKey('value'), isFalse);
    }
  });

  test('redactPolicySecretsはallowの本文を変えない', () {
    const text = '4社の回答を比較してください。';
    expect(redactPolicySecrets(text), text);
    expect(
      redactPolicySecrets('連絡先 foo@example.com'),
      '連絡先 ⟪REDACTED:email_address⟫',
    );
  });
}
