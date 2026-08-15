import '../models.dart';

/// 状態に依存しない表示用の純関数群(home_screen.dartから移設)。
/// 出力文字列は既存挙動そのまま。変更するとUI文言のスナップショットが変わるため注意。

String formatBytes(int bytes) {
  if (bytes < 1024) return '$bytes B';
  final kib = bytes / 1024;
  if (kib < 1024) return '${kib.toStringAsFixed(kib >= 100 ? 0 : 1)} KiB';
  final mib = kib / 1024;
  return '${mib.toStringAsFixed(mib >= 100 ? 0 : 2)} MiB';
}

String formatDate(String value) {
  final parsed = DateTime.tryParse(value)?.toLocal();
  if (parsed == null) return '';
  String two(int number) => number.toString().padLeft(2, '0');
  return '${parsed.year}/${two(parsed.month)}/${two(parsed.day)} '
      '${two(parsed.hour)}:${two(parsed.minute)}';
}

String cancelRunStatus(CancelRunResult result) =>
    switch (result.terminalOutcome) {
      'completed' => '完了が先に確定しました',
      'failed' => '停止前に処理失敗が確定しました',
      'cancelled' => result.alreadyDone ? 'すでに停止しています' : 'ローカル停止処理が完了しました',
      _ =>
        result.alreadyDone
            ? 'すでに処理は終了しています'
            : (result.cancelled ? 'ローカル停止処理が完了しました' : '停止要求済み'),
    };

String cancelRunNotice(CancelRunResult result) =>
    switch (result.terminalOutcome) {
      'completed' => '停止要求より先に会議の完了が確定しました。保存済み結果を確認できます。',
      'failed' => '停止要求より先に処理失敗が確定しました。保存済みの実行状態を確認してください。',
      _ =>
        result.warning.isNotEmpty
            ? result.warning
            : '停止要求後も、外部Provider側の処理や課金が続く可能性があります。',
    };
