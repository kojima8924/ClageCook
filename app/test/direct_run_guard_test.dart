import 'dart:async';

import 'package:clage_cook/services/direct_run_guard.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('overlapping runs share one native start and final stop', () async {
    final calls = <String>[];
    final guard = DirectRunGuard(
      enabled: true,
      invoker: (method, arguments) async {
        calls.add('$method:${arguments['operation']}');
        return null;
      },
    );

    final conference = await guard.acquire(
      jobId: 'conference-1',
      operation: DirectRunOperation.conference,
    );
    final regeneration = await guard.acquire(
      jobId: 'regeneration-1',
      operation: DirectRunOperation.regeneration,
    );

    expect(guard.activeCount, 2);
    expect(calls, ['start:conference']);
    await conference.release();
    expect(calls, ['start:conference']);
    await regeneration.release();
    expect(calls, ['start:conference', 'stop:regeneration']);
    expect(guard.activeCount, 0);
  });

  test('lease release is idempotent', () async {
    final calls = <String>[];
    final guard = DirectRunGuard(
      enabled: true,
      invoker: (method, _) async {
        calls.add(method);
        return null;
      },
    );

    final lease = await guard.acquire(
      jobId: 'conference-1',
      operation: DirectRunOperation.conference,
    );
    await lease.release();
    await lease.release();

    expect(calls, ['start', 'stop']);
    expect(guard.activeCount, 0);
  });

  test(
    'duplicate job IDs are rejected without creating a second owner',
    () async {
      final calls = <String>[];
      final guard = DirectRunGuard(
        enabled: true,
        invoker: (method, _) async {
          calls.add(method);
          return null;
        },
      );
      final lease = await guard.acquire(
        jobId: 'same-job',
        operation: DirectRunOperation.conference,
      );

      await expectLater(
        guard.acquire(
          jobId: 'same-job',
          operation: DirectRunOperation.conference,
        ),
        throwsA(isA<DirectRunGuardDuplicateJobException>()),
      );
      expect(guard.activeCount, 1);
      expect(calls, ['start']);

      await lease.release();
      expect(calls, ['start', 'stop']);
      expect(guard.activeCount, 0);
    },
  );

  test('missing Android MethodChannel fails closed', () async {
    final guard = DirectRunGuard(
      enabled: true,
      invoker: (_, _) async => throw MissingPluginException(),
    );

    await expectLater(
      guard.acquire(
        jobId: 'conference-1',
        operation: DirectRunOperation.conference,
      ),
      throwsA(isA<DirectRunGuardStartException>()),
    );
    expect(guard.lastWarning?.errorCode, 'missing_plugin');
    expect(guard.activeCount, 0);
  });

  test('Android start failure is audited and rejects API execution', () async {
    final warnings = <DirectRunGuardWarning>[];
    final guard = DirectRunGuard(
      enabled: true,
      onWarning: warnings.add,
      invoker: (method, _) async {
        if (method == 'start') {
          throw PlatformException(code: 'start_denied', message: 'device data');
        }
        return null;
      },
    );

    await expectLater(
      guard.acquire(
        jobId: 'conference-1',
        operation: DirectRunOperation.conference,
      ),
      throwsA(isA<DirectRunGuardStartException>()),
    );
    expect(guard.activeCount, 0);
    expect(guard.diagnostics['native_running'], isFalse);
    expect(warnings.single.errorCode, 'platform_error');
    expect(warnings.single.toJson().toString(), isNot(contains('device data')));
  });

  test(
    'platform timeout blocks execution and records a safe warning',
    () async {
      final never = Completer<Object?>();
      final guard = DirectRunGuard(
        enabled: true,
        operationTimeout: const Duration(milliseconds: 5),
        invoker: (_, _) => never.future,
      );

      await expectLater(
        guard.acquire(
          jobId: 'regeneration-1',
          operation: DirectRunOperation.regeneration,
        ),
        throwsA(isA<DirectRunGuardStartException>()),
      );
      expect(guard.lastWarning?.errorCode, 'timeout');
    },
  );

  test(
    'stop timeout forces a fresh acknowledged start before the next run',
    () async {
      final transitions = <String>[];
      var stopCalls = 0;
      final guard = DirectRunGuard(
        enabled: true,
        operationTimeout: const Duration(milliseconds: 5),
        invoker: (method, _) async {
          transitions.add(method);
          if (method == 'stop' && stopCalls++ == 0) {
            await Completer<void>().future;
          }
          return null;
        },
      );

      final first = await guard.acquire(
        jobId: 'conference-1',
        operation: DirectRunOperation.conference,
      );
      await first.release();
      expect(guard.lastWarning?.phase, 'stop');
      expect(guard.diagnostics['native_running'], isFalse);

      final second = await guard.acquire(
        jobId: 'conference-2',
        operation: DirectRunOperation.conference,
      );
      transitions.add('provider');
      await second.release();

      expect(transitions, ['start', 'stop', 'start', 'provider', 'stop']);
      expect(guard.activeCount, 0);
    },
  );

  test('disabled guard tracks jobs without invoking the platform', () async {
    var calls = 0;
    final guard = DirectRunGuard(
      enabled: false,
      invoker: (_, _) async {
        calls++;
        return null;
      },
    );

    final lease = await guard.acquire(
      jobId: 'conference-1',
      operation: DirectRunOperation.conference,
    );
    expect(guard.activeCount, 1);
    await lease.release();
    expect(guard.activeCount, 0);
    expect(calls, 0);
  });
}
