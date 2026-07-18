import 'dart:async';

import '../models.dart';

/// Owns the UI-visible lifecycle of one live run.
class LiveRunController {
  LiveRunController({
    this.subscriptionCancelTimeout = const Duration(seconds: 1),
  }) : assert(subscriptionCancelTimeout > Duration.zero);

  final Duration subscriptionCancelTimeout;

  LiveTurn? _turn;
  _OwnedLiveStream? _activeStream;
  bool _sending = false;
  bool _disconnected = false;
  String? _terminalReloadConversationId;

  LiveTurn? get turn => _turn;
  bool get sending => _sending;
  bool get disconnected => _disconnected;
  String? get terminalReloadConversationId => _terminalReloadConversationId;
  bool get hasActiveStream => _activeStream != null;

  bool isCurrent(String requestId) => _turn?.requestId == requestId;

  void beginRequest() {
    _sending = true;
    _disconnected = false;
  }

  void endRequest() {
    _sending = false;
    _disconnected = false;
  }

  void attach(LiveTurn turn) {
    unawaited(_closeActiveStream(detach: true));
    _turn = turn;
    _sending = true;
    _disconnected = false;
    _terminalReloadConversationId = null;
  }

  bool beginReconnect(LiveTurn turn) {
    if (!identical(_turn, turn)) return false;
    _sending = true;
    _disconnected = false;
    turn
      ..phase = 'ストリームへ再接続しています'
      ..error = '';
    return true;
  }

  bool disconnect(LiveTurn turn, String message) {
    if (!identical(_turn, turn)) return false;
    _sending = false;
    _disconnected = true;
    turn.error = message;
    return true;
  }

  void finish() {
    unawaited(_closeActiveStream(detach: true));
    _turn = null;
    _sending = false;
    _disconnected = false;
    _terminalReloadConversationId = null;
  }

  void requireTerminalReload(String conversationId) {
    unawaited(_closeActiveStream(detach: true));
    _turn = null;
    _sending = false;
    _disconnected = false;
    _terminalReloadConversationId = conversationId;
  }

  bool isTerminalReloadCurrent(String conversationId) =>
      _terminalReloadConversationId == conversationId;

  void clearTerminalReload() {
    _terminalReloadConversationId = null;
  }

  void reset() {
    unawaited(_closeActiveStream(detach: true));
    _turn = null;
    _sending = false;
    _disconnected = false;
    _terminalReloadConversationId = null;
  }

  /// Registers the SSE resources that belong to [turn]. A controller reset or
  /// disposal can then detach the waiter, cancel its idle timer, and cancel the
  /// HTTP subscription even when the widget that started it has disappeared.
  void ownStream<T>(
    LiveTurn turn,
    LiveStreamSession session,
    StreamSubscription<T> subscription,
  ) {
    if (!identical(_turn, turn)) {
      session.markDetached();
      session.dispose();
      unawaited(_cancelSubscription(() => subscription.cancel()));
      return;
    }
    final previous = _activeStream;
    _activeStream = _OwnedLiveStream(
      session: session,
      cancel: () => subscription.cancel(),
      cancelTimeout: subscriptionCancelTimeout,
    );
    if (previous != null) unawaited(previous.close(detach: true));
  }

  /// Releases the registered stream from a consume-loop `finally` block.
  Future<void> releaseStream(LiveStreamSession session) async {
    final active = _activeStream;
    if (active == null || !identical(active.session, session)) {
      session.dispose();
      return;
    }
    _activeStream = null;
    await active.close(detach: false);
  }

  Future<void> dispose() async {
    _turn = null;
    _sending = false;
    _disconnected = false;
    _terminalReloadConversationId = null;
    await _closeActiveStream(detach: true);
  }

  Future<void> _closeActiveStream({required bool detach}) async {
    final active = _activeStream;
    _activeStream = null;
    if (active != null) await active.close(detach: detach);
  }

  Future<void> _cancelSubscription(Future<void> Function() cancel) async {
    try {
      await cancel().timeout(subscriptionCancelTimeout);
    } catch (_) {
      // Resource cleanup must remain bounded even for a broken stream.
    }
  }
}

class _OwnedLiveStream {
  _OwnedLiveStream({
    required this.session,
    required this.cancel,
    required this.cancelTimeout,
  });

  final LiveStreamSession session;
  final Future<void> Function() cancel;
  final Duration cancelTimeout;
  Future<void>? _closing;

  Future<void> close({required bool detach}) {
    if (detach) session.markDetached();
    session.dispose();
    return _closing ??= _cancelSafely();
  }

  Future<void> _cancelSafely() async {
    try {
      await cancel().timeout(cancelTimeout);
    } catch (_) {
      // A cancellation failure must not retain the controller or block the UI.
    }
  }
}

/// Tracks one SSE subscription's terminal state and idle deadline.
class LiveStreamSession {
  LiveStreamSession({required this.idleTimeout})
    : assert(idleTimeout > Duration.zero) {
    _resetIdleTimer();
  }

  final Duration idleTimeout;
  final Completer<void> _terminal = Completer<void>();
  Timer? _idleTimer;

  bool _sawDone = false;
  bool _failed = false;
  bool _cancelled = false;
  bool _detached = false;
  Object? _failure;

  Future<void> get completed => _terminal.future;
  bool get isCompleted => _terminal.isCompleted;
  bool get sawDone => _sawDone;
  bool get failed => _failed;
  bool get cancelled => _cancelled;
  bool get detached => _detached;
  Object? get failure => _failure;

  void recordActivity() {
    if (!_terminal.isCompleted) _resetIdleTimer();
  }

  void markDone(Map<String, dynamic> data) {
    if (_terminal.isCompleted) return;
    _sawDone = true;
    _failed = data['failed'] == true;
    _cancelled = data['cancelled'] == true;
    _complete();
  }

  void markError(Object error) {
    if (_terminal.isCompleted) return;
    _failure = error;
    _complete();
  }

  void markEof() => _complete();

  void markDetached() {
    if (_terminal.isCompleted) return;
    _detached = true;
    _complete();
  }

  void dispose() {
    _idleTimer?.cancel();
  }

  void _resetIdleTimer() {
    _idleTimer?.cancel();
    _idleTimer = Timer(idleTimeout, () {
      if (_terminal.isCompleted) return;
      _failure = TimeoutException(
        'SSEが${idleTimeout.inSeconds}秒間無通信です',
        idleTimeout,
      );
      _complete();
    });
  }

  void _complete() {
    _idleTimer?.cancel();
    if (!_terminal.isCompleted) _terminal.complete();
  }
}
