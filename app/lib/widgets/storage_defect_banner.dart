import 'package:flutter/material.dart';

import '../services/local_conversation_store.dart';

/// 端末内会話の一部が読めなかったことを知らせるbanner。
///
/// 破損1件で接続そのものを無効化していた旧挙動の代わりに、健全な会話は
/// 表示したまま「読めなかった件数」と復旧操作だけを上に出す。
class StorageDefectBanner extends StatelessWidget {
  const StorageDefectBanner({
    super.key,
    required this.defects,
    required this.repairable,
    required this.busy,
    required this.onQuarantine,
    required this.onRebuild,
    required this.onDismiss,
  });

  final List<LocalConversationDefect> defects;
  final bool repairable;
  final bool busy;
  final VoidCallback onQuarantine;
  final VoidCallback onRebuild;
  final VoidCallback onDismiss;

  @override
  Widget build(BuildContext context) {
    if (defects.isEmpty) return const SizedBox.shrink();
    return MaterialBanner(
      leading: const Icon(Icons.report_problem_outlined),
      content: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Text('${defects.length}件の会話を読み込めませんでした。他の会話は通常どおり使えます。'),
          const SizedBox(height: 6),
          for (final defect in defects.take(5))
            Text(
              '・${defect.conversationId}: ${defect.reason}',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          if (defects.length > 5)
            Text(
              '・ほか${defects.length - 5}件',
              style: Theme.of(context).textTheme.bodySmall,
            ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : onDismiss,
          child: const Text('閉じる'),
        ),
        if (repairable) ...[
          TextButton(
            onPressed: busy ? null : onQuarantine,
            child: const Text('隔離する'),
          ),
          TextButton(
            onPressed: busy ? null : onRebuild,
            child: const Text('indexを再構築'),
          ),
        ],
      ],
    );
  }
}
