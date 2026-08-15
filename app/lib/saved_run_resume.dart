import 'models.dart';
import 'provider_catalog.dart';

/// 保存済みrunning ターンへ再接続するときのリクエストパラメータ正規化結果。
///
/// home_screen.dartの_reconnectSavedTurn冒頭にあったバリデーション・正規化を
/// 純関数として切り出したもの。startChat呼び出し・世代管理・LiveTurn構築の
/// フローは呼び出し側に残している。挙動はsaved_run_resume_test.dartで固定。
class SavedRunResume {
  const SavedRunResume._({
    required this.requestTier,
    required this.requestReasoningMode,
    required this.requestedProviders,
    required this.attachmentIds,
    required this.displayProviders,
    required this.effectiveTier,
  });

  factory SavedRunResume.fromTurn(TurnRecord turn) {
    final resume = turn.resumeRequest;
    final rawRequestTier = resume['tier']?.toString() ?? 'balanced';
    final requestTier = {'low', 'balanced', 'high'}.contains(rawRequestTier)
        ? rawRequestTier
        : 'balanced';
    final rawReasoningMode =
        resume['reasoning_mode']?.toString() ??
        turn.options['reasoning_mode']?.toString() ??
        'auto';
    final requestReasoningMode =
        {'auto', 'low', 'medium', 'high'}.contains(rawReasoningMode)
        ? rawReasoningMode
        : 'auto';
    final rawRequestedProviders = resume['providers'];
    final requestedProviders = rawRequestedProviders is List
        ? providerOrder
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
    final displayProviders = providerOrder
        .where(turn.providers.toSet().contains)
        .toList(growable: false);
    final rawEffectiveTier = turn.options['tier']?.toString() ?? requestTier;
    final effectiveTier = {'low', 'balanced', 'high'}.contains(rawEffectiveTier)
        ? rawEffectiveTier
        : requestTier;
    return SavedRunResume._(
      requestTier: requestTier,
      requestReasoningMode: requestReasoningMode,
      requestedProviders: requestedProviders,
      attachmentIds: attachmentIds,
      displayProviders: displayProviders,
      effectiveTier: effectiveTier,
    );
  }

  /// 再開リクエストへ渡すtier(不正値はbalancedへ丸める)。
  final String requestTier;

  /// 再開リクエストへ渡す推論エフォート(resume優先、次にoptions、不正値はauto)。
  final String requestReasoningMode;

  /// 再開リクエストへ渡すProvider一覧。resume_requestに無ければnull(サーバー既定に委ねる)。
  final List<String>? requestedProviders;

  /// 再開リクエストへ渡す添付ID一覧。
  final List<String> attachmentIds;

  /// LiveTurn表示用のProvider一覧(保存済みoptions由来・表示順で整列)。
  final List<String> displayProviders;

  /// LiveTurn表示用のtier(保存済みoptions優先、不正値はrequestTierへフォールバック)。
  final String effectiveTier;
}
