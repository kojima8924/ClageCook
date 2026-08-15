import 'dart:async';

import '../models.dart';
import '../services/api_client.dart';

/// 会話全文検索の状態(結果・進行中・エラー)とデバウンス実行を管理する。
///
/// 既存の2 controllerと同じく「epoch世代の照合で遅延応答を破棄する」パターン。
/// home_screen.dartの_scheduleSearch/_runSearchから移設した(issue #11の残り責務)。
/// 状態変化はonChangedコールバックで通知し、UI側がsetStateで再描画する。
class ConversationSearchController {
  ConversationSearchController({
    required this._client,
    required this._onChanged,
  });

  static const _debounce = Duration(milliseconds: 350);

  /// 検索実行時点のクライアントを参照する(schedule時点で固定しない)。
  final ApiClient? Function() _client;
  final void Function() _onChanged;

  List<ConversationSummary>? _results;
  bool _searching = false;
  String _error = '';
  int _epoch = 0;
  Timer? _timer;

  /// 検索結果。null=検索していない(通常一覧を表示する)。
  List<ConversationSummary>? get results => _results;
  bool get searching => _searching;
  String get error => _error;

  /// 入力変化を受けて検索を予約する。空クエリは結果を即クリアする。
  /// [immediate]は再検索ボタン・一覧更新後などデバウンス不要な場合に使う。
  void schedule(String rawQuery, {bool immediate = false}) {
    _timer?.cancel();
    final query = rawQuery.trim();
    final epoch = ++_epoch;
    if (query.isEmpty) {
      _results = null;
      _searching = false;
      _error = '';
      _onChanged();
      return;
    }
    _results = null;
    _searching = true;
    _error = '';
    _onChanged();
    if (immediate) {
      unawaited(_run(query, epoch));
    } else {
      _timer = Timer(_debounce, () => unawaited(_run(query, epoch)));
    }
  }

  Future<void> _run(String query, int epoch) async {
    final client = _client();
    if (client == null) {
      if (epoch != _epoch) return;
      _results = const [];
      _searching = false;
      _error = '実行方式を設定すると回答本文まで全文検索できます。';
      _onChanged();
      return;
    }
    try {
      final result = await client.searchConversations(query, limit: 50);
      if (epoch != _epoch) return;
      _results = result.results;
      _searching = false;
      _error = '';
      _onChanged();
    } catch (error) {
      if (epoch != _epoch) return;
      _results = const [];
      _searching = false;
      _error = '全文検索に失敗しました: $error';
      _onChanged();
    }
  }

  /// 接続初期化失敗時など、進行中の検索を無効化して結果を破棄する。
  /// (エラー文言は呼び出し側の表示方針に合わせて維持する)
  void invalidate() {
    _epoch++;
    _timer?.cancel();
    _results = null;
    _searching = false;
  }

  void dispose() {
    _epoch++;
    _timer?.cancel();
  }
}
