import 'package:clage_cook/models.dart';
import 'package:clage_cook/saved_run_resume.dart';
import 'package:flutter_test/flutter_test.dart';

// SavedRunResume.fromTurn(home_screen.dartの_reconnectSavedTurn冒頭から移設した
// バリデーション・正規化)の既存挙動を固定するテスト。

TurnRecord _turn({
  Map<String, dynamic> resumeRequest = const {},
  Map<String, dynamic> options = const {},
}) => TurnRecord(
  requestId: 'req-1',
  message: 'hello',
  cleanMessage: 'hello',
  answers: const {},
  synthesis: const SynthesisRecord(ok: false),
  options: options,
  resumeRequest: resumeRequest,
  status: 'running',
);

void main() {
  group('SavedRunResume.fromTurn', () {
    test('空のresume_request/optionsでは安全な既定値へ落ちる', () {
      final resume = SavedRunResume.fromTurn(_turn());
      expect(resume.requestTier, 'balanced');
      expect(resume.requestReasoningMode, 'auto');
      expect(resume.requestedProviders, isNull);
      expect(resume.attachmentIds, isEmpty);
      expect(resume.displayProviders, isEmpty);
      expect(resume.effectiveTier, 'balanced');
    });

    test('不正なtierはbalancedへ丸める', () {
      final resume = SavedRunResume.fromTurn(
        _turn(resumeRequest: {'tier': 'ultra'}),
      );
      expect(resume.requestTier, 'balanced');
    });

    test('有効なtierはそのまま採用する', () {
      final resume = SavedRunResume.fromTurn(
        _turn(resumeRequest: {'tier': 'high'}),
      );
      expect(resume.requestTier, 'high');
    });

    test('reasoning_modeはresume優先・無ければoptionsへフォールバックする', () {
      final fromResume = SavedRunResume.fromTurn(
        _turn(
          resumeRequest: {'reasoning_mode': 'low'},
          options: {'reasoning_mode': 'high'},
        ),
      );
      expect(fromResume.requestReasoningMode, 'low');
      final fromOptions = SavedRunResume.fromTurn(
        _turn(options: {'reasoning_mode': 'high'}),
      );
      expect(fromOptions.requestReasoningMode, 'high');
    });

    test('不正なreasoning_modeはautoへ丸める', () {
      final resume = SavedRunResume.fromTurn(
        _turn(resumeRequest: {'reasoning_mode': 'extreme'}),
      );
      expect(resume.requestReasoningMode, 'auto');
    });

    test('providersは表示順で正規化し、未知の名前は落とす', () {
      final resume = SavedRunResume.fromTurn(
        _turn(
          resumeRequest: {
            'providers': ['grok', 'unknown', 'claude'],
          },
        ),
      );
      expect(resume.requestedProviders, ['claude', 'grok']);
    });

    test('providersがList以外ならnull(サーバー既定へ委ねる)', () {
      final resume = SavedRunResume.fromTurn(
        _turn(resumeRequest: {'providers': 'claude'}),
      );
      expect(resume.requestedProviders, isNull);
    });

    test('attachment_idsは文字列化して引き継ぐ', () {
      final resume = SavedRunResume.fromTurn(
        _turn(
          resumeRequest: {
            'attachment_ids': ['a1', 2],
          },
        ),
      );
      expect(resume.attachmentIds, ['a1', '2']);
    });

    test('displayProvidersは保存済みoptionsのproviders由来で表示順に整列する', () {
      final resume = SavedRunResume.fromTurn(
        _turn(
          options: {
            'providers': ['chatgpt', 'claude'],
          },
        ),
      );
      expect(resume.displayProviders, ['claude', 'chatgpt']);
    });

    test('effectiveTierはoptions優先・不正値はrequestTierへフォールバックする', () {
      final fromOptions = SavedRunResume.fromTurn(
        _turn(resumeRequest: {'tier': 'low'}, options: {'tier': 'high'}),
      );
      expect(fromOptions.effectiveTier, 'high');
      final invalidOptions = SavedRunResume.fromTurn(
        _turn(resumeRequest: {'tier': 'low'}, options: {'tier': 'mega'}),
      );
      expect(invalidOptions.effectiveTier, 'low');
    });
  });
}
