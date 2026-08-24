import 'package:flutter/material.dart';

import '../models.dart';
import '../utils/format.dart';

/// home_screen.dartから移設したダイアログ群。
/// いずれもNavigator.popで結果を返すだけの純UIで、
/// 呼び出し後のsetState・設定保存フローは呼び出し側(home_screen)に残している。
/// 文言・構造はwidget_test.dartのassert対象のため変更しないこと。

/// 実API課金確認ダイアログの選択結果。
enum BillableConfirmationAction { cancel, confirmOnce, disableFuture }

/// 秘密情報検出(block)時のダイアログを表示する。
/// 戻り値: true=マスク済み文面へ置換 / false・null=送信せず戻る。
Future<bool?> showPolicyBlockedDialog(
  BuildContext context,
  PolicyScanResult policy,
) {
  final labels = policy.findings
      .where((finding) => finding.severity == 'block')
      .map((finding) => finding.label)
      .toSet()
      .toList(growable: false);
  final redacted = policy.redactedText.trim();
  return showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (context) => AlertDialog(
      icon: const Icon(Icons.gpp_bad_outlined),
      title: const Text('秘密情報らしい文字列を検出しました'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('この文面は外部AIへ送信されません。検出理由:'),
            const SizedBox(height: 8),
            if (labels.isEmpty)
              const Text('• 秘密情報らしい文字列')
            else
              for (final label in labels) Text('• $label'),
            const SizedBox(height: 16),
            const Text(
              'マスク済み文面',
              style: TextStyle(fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 6),
            DecoratedBox(
              decoration: BoxDecoration(
                color: Theme.of(context).colorScheme.surfaceContainerHighest,
                borderRadius: BorderRadius.circular(8),
              ),
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: SelectableText(
                  redacted.isEmpty ? '⟪REDACTED⟫' : redacted,
                ),
              ),
            ),
            if (policy.disclaimer.isNotEmpty) ...[
              const SizedBox(height: 10),
              Text(
                policy.disclaimer,
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('送信せず戻る'),
        ),
        FilledButton.icon(
          onPressed: redacted.isEmpty
              ? null
              : () => Navigator.pop(context, true),
          icon: const Icon(Icons.find_replace_outlined),
          label: const Text('マスク済み文面へ置換'),
        ),
      ],
    ),
  );
}

/// 実API課金前の確認ダイアログを表示する。閉じられた場合はcancel扱い。
Future<BillableConfirmationAction> showBillableRunConfirmationDialog(
  BuildContext context, {
  required RunPlan plan,
  required PolicyScanResult policy,
  required bool allowDisableFuture,
}) async =>
    await showDialog<BillableConfirmationAction>(
      context: context,
      barrierDismissible: false,
      builder: (context) => _BillableRunConfirmationDialog(
        plan: plan,
        policy: policy,
        allowDisableFuture: allowDisableFuture,
      ),
    ) ??
    BillableConfirmationAction.cancel;

/// 「次回から表示しない」を肯定ボタンと同格に並べると誤タップで安全弁が
/// 恒久OFFになるため、チェックボックス+単一の実行ボタンへ畳んでいる。
class _BillableRunConfirmationDialog extends StatefulWidget {
  const _BillableRunConfirmationDialog({
    required this.plan,
    required this.policy,
    required this.allowDisableFuture,
  });

  final RunPlan plan;
  final PolicyScanResult policy;
  final bool allowDisableFuture;

  @override
  State<_BillableRunConfirmationDialog> createState() =>
      _BillableRunConfirmationDialogState();
}

class _BillableRunConfirmationDialogState
    extends State<_BillableRunConfirmationDialog> {
  bool _disableFuture = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final plan = widget.plan;
    final policy = widget.policy;
    final participants = plan.billableParticipants;
    final liveTokens = plan.maxOutputTokens['live_total'] ?? 0;
    final allCalls = plan.calls['total'] ?? 0;
    final retryEnvelope = plan.retryEnvelope;
    final sensitiveConfirmation = policy.needsConfirmation;
    final personalDataLabels = policy.findings
        .where((finding) => finding.severity == 'confirm')
        .map((finding) => finding.label)
        .toSet()
        .toList(growable: false);
    final excerpt = _policyExcerpt(policy);
    final detailStyle = theme.textTheme.bodySmall;
    return AlertDialog(
      icon: Icon(
        sensitiveConfirmation
            ? Icons.privacy_tip_outlined
            : Icons.payments_outlined,
      ),
      title: Text(sensitiveConfirmation ? '個人情報らしい内容を外部送信します' : '実APIを使用します'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // 警告は本文末尾ではなくタイトル直下へ置き、該当箇所も示す。
            if (personalDataLabels.isNotEmpty) ...[
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: theme.colorScheme.errorContainer,
                  borderRadius: BorderRadius.circular(10),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '送信前に確認してください: ${personalDataLabels.join('、')}',
                      style: TextStyle(
                        color: theme.colorScheme.onErrorContainer,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    if (excerpt.isNotEmpty) ...[
                      const SizedBox(height: 8),
                      Text(
                        '該当箇所（⟪…⟫ が検出部分）',
                        style: detailStyle?.copyWith(
                          color: theme.colorScheme.onErrorContainer,
                        ),
                      ),
                      const SizedBox(height: 4),
                      SelectableText(
                        excerpt,
                        style: TextStyle(
                          fontFamily: 'monospace',
                          color: theme.colorScheme.onErrorContainer,
                        ),
                      ),
                    ],
                    const SizedBox(height: 8),
                    Text(
                      'ローカルのパターン一致による判定です。誤検知のこともあります。'
                      '送信したくない場合はキャンセルして該当部分を書き換えてください。',
                      style: detailStyle?.copyWith(
                        color: theme.colorScheme.onErrorContainer,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),
            ],
            const Text('以下の外部API呼び出しは、各社との契約に応じて課金される可能性があります。'),
            const SizedBox(height: 14),
            for (final participant in participants)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(
                  '• ${participant.label} / ${participant.model}: '
                  '最大${participant.maxCalls}回',
                ),
              ),
            const SizedBox(height: 4),
            if (plan.costEstimate.available &&
                plan.costEstimate.totalUsd != null) ...[
              Text(
                '利用者設定価格による最大見積: \$${plan.costEstimate.totalUsd}',
                style: theme.textTheme.titleSmall,
              ),
              if (plan.costEstimate.priceVersion != null)
                Text(
                  '価格版: ${plan.costEstimate.priceVersion}',
                  style: detailStyle,
                ),
              if (plan.budget.configured) ...[
                if (plan.budget.perRunLimitUsd != null)
                  Text('1会議上限: \$${plan.budget.perRunLimitUsd}'),
                if (plan.budget.todayRemainingUsd != null)
                  Text('実行前の日次残り: \$${plan.budget.todayRemainingUsd}'),
              ],
              const SizedBox(height: 6),
              Text(
                '金額は明示価格表と安全側token上限による推定で、請求書やcredit残高ではありません。',
                style: detailStyle,
              ),
            ] else
              Text(
                '入力token数・料金・思考tokenはProvider仕様で変わるため、'
                'この確認は金額上限を保証しません。',
                style: detailStyle,
              ),
            // 数量の内訳は主役ではないので、見出し付きで小さくまとめる。
            const Divider(height: 24),
            Text('内訳', style: theme.textTheme.labelLarge),
            const SizedBox(height: 4),
            Text('課金対象APIの最大呼出回数: ${plan.maxLiveCalls}回', style: detailStyle),
            Text('会議全体の最大Provider呼出回数: $allCalls回', style: detailStyle),
            Text('課金対象呼出の最大出力token合計: $liveTokens', style: detailStyle),
            if (plan.inputEnvelope.liveWithRetries > 0)
              Text(
                '課金対象呼出の入力送信量（再試行込み）: '
                '${formatBytes(plan.inputEnvelope.liveWithRetries)}',
                style: detailStyle,
              ),
            if (plan.inputEnvelope.disclaimer.isNotEmpty)
              Text(plan.inputEnvelope.disclaimer, style: detailStyle),
            if (retryEnvelope.hasRetries) ...[
              const SizedBox(height: 10),
              Text(
                '再試行込み最大Provider実行回数: '
                '${retryEnvelope.totalProviderExecutions}回 '
                '（追加${retryEnvelope.additionalHttpAttempts}回）',
                style: detailStyle,
              ),
              Text(
                '再試行込み最大出力token合計: ${retryEnvelope.maxOutputTokens}',
                style: detailStyle,
              ),
              if (retryEnvelope.disclaimer.isNotEmpty)
                Text(retryEnvelope.disclaimer, style: detailStyle),
            ],
            if (widget.allowDisableFuture) ...[
              const Divider(height: 24),
              CheckboxListTile(
                key: const Key('billable-disable-future'),
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                value: _disableFuture,
                onChanged: (value) =>
                    setState(() => _disableFuture = value ?? false),
                title: const Text('次回からこの確認を表示しない'),
                subtitle: Text(
                  '秘密情報・個人情報など、policy上必要な確認は常に表示します。',
                  style: detailStyle,
                ),
              ),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () =>
              Navigator.pop(context, BillableConfirmationAction.cancel),
          child: const Text('キャンセル'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(
            context,
            _disableFuture
                ? BillableConfirmationAction.disableFuture
                : BillableConfirmationAction.confirmOnce,
          ),
          child: const Text('確認して実行'),
        ),
      ],
    );
  }
}

/// policyのマスク済み文面から、最初の検出マーカー周辺だけを抜き出す。
/// 生の秘密値は含まれない(マーカーへ置換済み)。
String _policyExcerpt(PolicyScanResult policy) {
  final text = policy.redactedText;
  final marker = text.indexOf('⟪REDACTED:');
  if (marker < 0) return '';
  const margin = 40;
  final start = marker - margin < 0 ? 0 : marker - margin;
  final markerEnd = text.indexOf('⟫', marker);
  final tail = (markerEnd < 0 ? marker : markerEnd) + margin;
  final end = tail > text.length ? text.length : tail;
  final body = text.substring(start, end).replaceAll('\n', ' ');
  return '${start > 0 ? '…' : ''}$body${end < text.length ? '…' : ''}';
}

/// 会話ローカルメモの編集ダイアログ。Navigator.popで編集後テキストを返す。
class MemoryEditorDialog extends StatefulWidget {
  const MemoryEditorDialog({super.key, required this.initialText});

  final String initialText;

  @override
  State<MemoryEditorDialog> createState() => _MemoryEditorDialogState();
}

class _MemoryEditorDialogState extends State<MemoryEditorDialog> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialText);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('ローカルメモ'),
    content: SizedBox(
      width: 620,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('会話の目的・制約・用語などを保存します。各ターンの初回回答へ参考データとして渡されます。'),
            const SizedBox(height: 10),
            TextField(
              controller: _controller,
              minLines: 6,
              maxLines: 14,
              maxLength: 20000,
              autofocus: true,
              decoration: const InputDecoration(
                hintText: '例: 対象読者、採用済み方針、避けるべき案…',
                border: OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() {}),
            ),
            const Text('APIキーなどの秘密候補は保存時にマスクされます。'),
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
        onPressed: () => Navigator.pop(context, _controller.text),
        child: Text(_controller.text.trim().isEmpty ? 'クリア' : '保存'),
      ),
    ],
  );
}
