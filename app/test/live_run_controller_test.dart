import 'dart:async';

import 'package:clage_cook/controllers/live_run_controller.dart';
import 'package:clage_cook/models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('ライブ実行の開始・切断・再接続・終了を単一管理する', () {
    final controller = LiveRunController();
    final live = _liveTurn();

    controller.beginRequest();
    expect(controller.sending, isTrue);

    controller.attach(live);
    expect(controller.turn, same(live));
    expect(controller.isCurrent(live.requestId), isTrue);

    expect(controller.disconnect(live, '通信が切断されました'), isTrue);
    expect(controller.sending, isFalse);
    expect(controller.disconnected, isTrue);
    expect(live.error, '通信が切断されました');

    expect(controller.beginReconnect(live), isTrue);
    expect(controller.sending, isTrue);
    expect(controller.disconnected, isFalse);
    expect(live.error, isEmpty);

    controller.finish();
    expect(controller.turn, isNull);
    expect(controller.sending, isFalse);
  });

  test('終了後の会話再読込状態をライブ実行と排他的に管理する', () {
    final controller = LiveRunController()
      ..requireTerminalReload('conversation-a');

    expect(controller.isTerminalReloadCurrent('conversation-a'), isTrue);
    expect(controller.turn, isNull);

    controller.attach(_liveTurn());
    expect(controller.terminalReloadConversationId, isNull);
  });

  test('doneはHTTP EOFを待たずにセッションを終了する', () async {
    final session = LiveStreamSession(idleTimeout: const Duration(seconds: 1));
    addTearDown(session.dispose);

    session.markDone(const {'failed': true, 'cancelled': true});
    await session.completed;

    expect(session.sawDone, isTrue);
    expect(session.failed, isTrue);
    expect(session.cancelled, isTrue);
    expect(session.failure, isNull);
  });

  test('アクティビティごとにidle期限を延長する', () async {
    const idle = Duration(milliseconds: 120);
    final session = LiveStreamSession(idleTimeout: idle);
    addTearDown(session.dispose);

    await Future<void>.delayed(const Duration(milliseconds: 70));
    session.recordActivity();
    await Future<void>.delayed(const Duration(milliseconds: 70));
    expect(session.isCompleted, isFalse);

    await session.completed.timeout(const Duration(milliseconds: 100));
    expect(session.failure, isA<TimeoutException>());
  });

  test('controller破棄は所有中のsessionとsubscriptionを確実に解放する', () async {
    var cancelCount = 0;
    final events = StreamController<int>(onCancel: () => cancelCount++);
    addTearDown(events.close);
    final controller = LiveRunController(
      subscriptionCancelTimeout: const Duration(milliseconds: 100),
    );
    final live = _liveTurn();
    final session = LiveStreamSession(idleTimeout: const Duration(minutes: 1));
    final subscription = events.stream.listen((_) {});

    controller.attach(live);
    controller.ownStream(live, session, subscription);
    expect(controller.hasActiveStream, isTrue);

    await controller.dispose();

    expect(controller.hasActiveStream, isFalse);
    expect(session.detached, isTrue);
    expect(session.isCompleted, isTrue);
    expect(cancelCount, 1);
  });

  test('consumeのfinally相当のreleaseは未完了sessionのidle timerも止める', () async {
    var cancelled = false;
    final events = StreamController<int>(onCancel: () => cancelled = true);
    addTearDown(events.close);
    final controller = LiveRunController();
    final live = _liveTurn();
    final session = LiveStreamSession(
      idleTimeout: const Duration(milliseconds: 30),
    );
    final subscription = events.stream.listen((_) {});

    controller.attach(live);
    controller.ownStream(live, session, subscription);
    await controller.releaseStream(session);
    await Future<void>.delayed(const Duration(milliseconds: 50));

    expect(cancelled, isTrue);
    expect(controller.hasActiveStream, isFalse);
    expect(session.isCompleted, isFalse);
  });
}

LiveTurn _liveTurn() => LiveTurn(
  requestId: 'request-a',
  message: 'テスト',
  providers: const ['claude'],
  tier: 'balanced',
  debate: false,
  synthesize: true,
  conversationId: 'conversation-a',
);
