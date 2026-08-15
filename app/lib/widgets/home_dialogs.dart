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
                child: SelectableText(redacted.isEmpty ? '⟪REDACTED⟫' : redacted),
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
}) async {
  final participants = plan.billableParticipants;
  final liveTokens = plan.maxOutputTokens['live_total'] ?? 0;
  final allCalls = plan.calls['total'] ?? 0;
  final retryEnvelope = plan.retryEnvelope;
  final sensitiveConfirmation = policy.action == 'confirm';
  final personalDataLabels = policy.findings
      .where((finding) => finding.severity == 'confirm')
      .map((finding) => finding.label)
      .toSet()
      .toList(growable: false);
  return await showDialog<BillableConfirmationAction>(
        context: context,
        barrierDismissible: false,
        builder: (context) => AlertDialog(
          icon: const Icon(Icons.payments_outlined),
          title: Text(
            sensitiveConfirmation ? '個人情報らしい内容を外部送信します' : '実APIを使用します',
          ),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
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
                const Divider(height: 24),
                Text('課金対象APIの最大呼出回数: ${plan.maxLiveCalls}回'),
                Text('会議全体の最大Provider呼出回数: $allCalls回'),
                Text('課金対象呼出の最大出力token合計: $liveTokens'),
                if (plan.inputEnvelope.liveWithRetries > 0)
                  Text(
                    '課金対象呼出の入力送信量（再試行込み）: '
                    '${formatBytes(plan.inputEnvelope.liveWithRetries)}',
                  ),
                const SizedBox(height: 8),
                if (plan.costEstimate.available &&
                    plan.costEstimate.totalUsd != null) ...[
                  Text(
                    '利用者設定価格による最大見積: '
                    '\$${plan.costEstimate.totalUsd}',
                    style: Theme.of(context).textTheme.titleSmall,
                  ),
                  if (plan.costEstimate.priceVersion != null)
                    Text(
                      '価格版: ${plan.costEstimate.priceVersion}',
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  if (plan.budget.configured) ...[
                    if (plan.budget.perRunLimitUsd != null)
                      Text('1会議上限: \$${plan.budget.perRunLimitUsd}'),
                    if (plan.budget.todayRemainingUsd != null)
                      Text('実行前の日次残り: \$${plan.budget.todayRemainingUsd}'),
                  ],
                  const SizedBox(height: 6),
                  const Text(
                    '金額は明示価格表と安全側token上限による推定で、請求書やcredit残高ではありません。',
                  ),
                ] else
                  const Text(
                    '入力token数・料金・思考tokenはProvider仕様で変わるため、'
                    'この確認は金額上限を保証しません。',
                  ),
                if (plan.inputEnvelope.disclaimer.isNotEmpty)
                  Text(
                    plan.inputEnvelope.disclaimer,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                if (retryEnvelope.hasRetries) ...[
                  const SizedBox(height: 10),
                  Text(
                    '再試行込み最大Provider実行回数: '
                    '${retryEnvelope.totalProviderExecutions}回 '
                    '（追加${retryEnvelope.additionalHttpAttempts}回）',
                  ),
                  Text(
                    '再試行込み最大出力token合計: '
                    '${retryEnvelope.maxOutputTokens}',
                  ),
                  if (retryEnvelope.disclaimer.isNotEmpty)
                    Text(
                      retryEnvelope.disclaimer,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                ],
                if (personalDataLabels.isNotEmpty) ...[
                  const SizedBox(height: 14),
                  Text(
                    '送信前に確認してください: ${personalDataLabels.join('、')}',
                    style: TextStyle(color: Theme.of(context).colorScheme.error),
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
            if (allowDisableFuture)
              TextButton(
                onPressed: () => Navigator.pop(
                  context,
                  BillableConfirmationAction.disableFuture,
                ),
                child: const Text('実行して次回から表示しない'),
              ),
            FilledButton(
              onPressed: () => Navigator.pop(
                context,
                BillableConfirmationAction.confirmOnce,
              ),
              child: const Text('確認して実行'),
            ),
          ],
        ),
      ) ??
      BillableConfirmationAction.cancel;
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
