import 'dart:async';

import 'package:flutter/services.dart';

import 'direct_run_guard_platform_stub.dart'
    if (dart.library.io) 'direct_run_guard_platform_io.dart'
    as platform;

const _channelName = 'jp.akoji.clage_cook/direct_run_guard';

typedef DirectRunGuardInvoker =
    Future<Object?> Function(String method, Map<String, Object?> arguments);

enum DirectRunOperation { conference, regeneration }

/// A sanitized diagnostic emitted when Android foreground-service control fails.
///
/// Android start failures stop API execution before a paid request is sent;
/// stop failures remain non-fatal after the request has completed. The
/// diagnostic intentionally stores only a stable error category rather than a
/// platform error message that could contain device data.
class DirectRunGuardWarning {
  const DirectRunGuardWarning({
    required this.operation,
    required this.phase,
    required this.errorCode,
    required this.occurredAt,
  });

  final DirectRunOperation operation;
  final String phase;
  final String errorCode;
  final DateTime occurredAt;

  Map<String, Object?> toJson() => {
    'operation': operation.name,
    'phase': phase,
    'error_code': errorCode,
    'occurred_at': occurredAt.toUtc().toIso8601String(),
  };
}

class DirectRunGuardStartException implements Exception {
  const DirectRunGuardStartException();

  @override
  String toString() => 'Androidのバックグラウンド実行保護を開始できないため、API送信を中止しました。';
}

class DirectRunGuardDuplicateJobException implements Exception {
  const DirectRunGuardDuplicateJobException();

  @override
  String toString() => '同じ実行IDのバックグラウンド保護は既に開始されています。';
}

/// Keeps Direct BYOK work in an Android foreground service while paid API calls
/// are active. Other platforms and Web deliberately use the same safe no-op API.
///
/// Native transitions are tracked by unique job IDs so overlapping conference
/// and regeneration calls produce exactly one start and one final stop request.
class DirectRunGuard {
  DirectRunGuard({
    bool? enabled,
    DirectRunGuardInvoker? invoker,
    Duration operationTimeout = const Duration(seconds: 6),
    void Function(DirectRunGuardWarning warning)? onWarning,
  }) : _enabled = enabled ?? platform.isAndroidHost,
       _invoker = invoker ?? _invokePlatform,
       // Public parameter names intentionally describe the testing seam.
       // ignore: prefer_initializing_formals
       _operationTimeout = operationTimeout,
       // ignore: prefer_initializing_formals
       _onWarning = onWarning;

  static final DirectRunGuard shared = DirectRunGuard();
  static const MethodChannel _channel = MethodChannel(_channelName);

  final bool _enabled;
  final DirectRunGuardInvoker _invoker;
  final Duration _operationTimeout;
  final void Function(DirectRunGuardWarning warning)? _onWarning;

  final Set<String> _activeJobIds = {};
  var _nativeRunning = false;
  Future<void>? _startFuture;
  Future<void>? _stopFuture;
  DirectRunGuardWarning? _lastWarning;

  int get activeCount => _activeJobIds.length;
  bool get enabled => _enabled;
  DirectRunGuardWarning? get lastWarning => _lastWarning;

  Map<String, Object?> get diagnostics => {
    'enabled': _enabled,
    'active_count': _activeJobIds.length,
    'native_running': _nativeRunning,
    if (_lastWarning != null) 'last_warning': _lastWarning!.toJson(),
  };

  Future<DirectRunGuardLease> acquire({
    required String jobId,
    required DirectRunOperation operation,
  }) async {
    final normalizedJobId = jobId.trim();
    if (normalizedJobId.isEmpty) {
      throw ArgumentError.value(jobId, 'jobId', 'must not be empty');
    }
    if (!_activeJobIds.add(normalizedJobId)) {
      throw const DirectRunGuardDuplicateJobException();
    }
    if (_enabled) {
      try {
        await _ensureNativeStarted(operation);
      } catch (error) {
        _activeJobIds.remove(normalizedJobId);
        _recordWarning('start', operation, error);
        throw const DirectRunGuardStartException();
      }
    }
    return DirectRunGuardLease._(this, normalizedJobId, operation);
  }

  Future<void> _ensureNativeStarted(DirectRunOperation operation) async {
    final stopping = _stopFuture;
    if (stopping != null) {
      await stopping;
    }
    if (_nativeRunning) return;
    final inFlight = _startFuture;
    if (inFlight != null) return inFlight;
    final start = _invoker('start', {'operation': operation.name})
        .timeout(_operationTimeout)
        .then<void>((_) {
          _nativeRunning = true;
        });
    _startFuture = start;
    try {
      await start;
    } finally {
      if (identical(_startFuture, start)) _startFuture = null;
    }
  }

  Future<void> _release(String jobId, DirectRunOperation operation) async {
    if (!_activeJobIds.remove(jobId)) return;
    if (_activeJobIds.isNotEmpty || !_enabled || !_nativeRunning) {
      return;
    }
    final existing = _stopFuture;
    if (existing != null) return existing;
    final stop = _stopNative(operation);
    _stopFuture = stop;
    try {
      await stop;
    } finally {
      if (identical(_stopFuture, stop)) _stopFuture = null;
    }
  }

  Future<void> _stopNative(DirectRunOperation operation) async {
    try {
      await _invoker('stop', {
        'operation': operation.name,
      }).timeout(_operationTimeout);
    } catch (error) {
      // A failed or timed-out stop has an unknown native outcome. Mark the
      // local state stopped so the next paid run must obtain a fresh native
      // startForeground acknowledgement before dispatching.
      _recordWarning('stop', operation, error);
    } finally {
      _nativeRunning = false;
    }
  }

  void _recordWarning(
    String phase,
    DirectRunOperation operation,
    Object error,
  ) {
    final warning = DirectRunGuardWarning(
      operation: operation,
      phase: phase,
      errorCode: _safeErrorCode(error),
      occurredAt: DateTime.now(),
    );
    _lastWarning = warning;
    try {
      _onWarning?.call(warning);
    } catch (_) {
      // Diagnostics must never break run lifecycle transitions.
    }
  }

  static Future<Object?> _invokePlatform(
    String method,
    Map<String, Object?> arguments,
  ) => _channel.invokeMethod<Object?>(method, arguments);

  static String _safeErrorCode(Object error) {
    if (error is TimeoutException) return 'timeout';
    if (error is MissingPluginException) return 'missing_plugin';
    if (error is PlatformException) {
      return switch (error.code) {
        'foreground_service_start_failed' => error.code,
        'foreground_service_stop_failed' => error.code,
        _ => 'platform_error',
      };
    }
    return 'unexpected_error';
  }
}

class DirectRunGuardLease {
  DirectRunGuardLease._(this._owner, this.jobId, this.operation);

  final DirectRunGuard _owner;
  final String jobId;
  final DirectRunOperation operation;
  bool _released = false;

  Future<void> release() async {
    if (_released) return;
    _released = true;
    await _owner._release(jobId, operation);
  }
}
