import 'package:clage_cook/models.dart';
import 'package:clage_cook/widgets/turn_view.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('保存ターンへ回答比較と実測usage台帳を統合表示する', (tester) async {
    final turn = TurnRecord.fromJson({
      'request_id': 'request-id',
      'message': '比較して',
      'clean_message': '比較して',
      'options': {
        'tier': 'balanced',
        'providers': ['claude', 'gemini', 'chatgpt'],
      },
      'status': 'failed',
      'failed': true,
      'usage_may_be_incomplete': true,
      'answers': {
        'claude': {
          'source': 'claude',
          'ok': true,
          'text': '共通の回答',
          'model': 'claude-test',
          'usage': {'input_tokens': 12, 'output_tokens': 8, 'total_tokens': 20},
          'completion_status': 'incomplete',
          'partial': true,
          'incomplete_reason': 'max_output_tokens',
          'usage_may_be_incomplete': true,
          'request_audit': {
            'http_attempts': 2,
            'retry_count': 1,
            'outcome': 'response_received',
            'usage_may_be_incomplete': true,
          },
        },
        'chatgpt': {
          'source': 'chatgpt',
          'ok': true,
          'text': '共通の別回答',
          'model': 'gpt-test',
          'usage': {'input_tokens': 10, 'output_tokens': 7, 'total_tokens': 17},
        },
      },
      'insights': {
        'is_comparable': true,
        'answer_count': 2,
        'comparison_count': 1,
        'agreement_score': 0.5,
        'pairwise_similarities': [
          {
            'sources': ['claude', 'chatgpt'],
            'similarity': 0.5,
          },
        ],
        'provider_similarities': {'claude': 0.5, 'chatgpt': 0.5},
        'shared_terms': ['共通'],
        'distinctive_terms': <String, Object>{},
        'caution_signals': <String, Object>{},
        'method': {'limitations': '語彙だけを比較します。'},
      },
      'synthesis': {'ok': true, 'text': '統合結果', 'source': 'claude'},
    });

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(child: SavedTurnView(turn: turn)),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('回答間の語彙比較'), findsOneWidget);
    expect(find.text('トークン利用量台帳'), findsOneWidget);
    expect(find.text('Input'), findsNothing);
    await tester.ensureVisible(find.text('トークン利用量台帳'));
    await tester.tap(find.text('トークン利用量台帳'));
    await tester.pumpAndSettle();
    expect(find.text('Input'), findsWidgets);
    expect(find.textContaining('正しさ'), findsWidgets);
    expect(find.text('統合結果'), findsOneWidget);
    expect(find.text('Gemini'), findsOneWidget);
    expect(find.text('PARTIAL · 出力上限'), findsOneWidget);
    expect(find.text('HTTP 2回'), findsOneWidget);
    expect(find.text('利用量は不完全な可能性'), findsOneWidget);
    expect(find.textContaining('完全な回答として扱わないでください'), findsOneWidget);
    expect(find.textContaining('Provider側の処理・利用量'), findsOneWidget);
  });

  testWidgets('表示設定OFFではusageを保持したまま台帳だけを隠す', (tester) async {
    final turn = TurnRecord.fromJson({
      'request_id': 'hidden-ledger',
      'message': '比較して',
      'status': 'completed',
      'options': {
        'providers': ['claude'],
      },
      'answers': {
        'claude': {
          'source': 'claude',
          'ok': true,
          'text': '回答本文',
          'usage': {'input_tokens': 12, 'output_tokens': 8},
        },
      },
      'insights': {
        'is_comparable': false,
        'answer_count': 1,
        'comparison_count': 0,
      },
      'synthesis': {'ok': false, 'skipped': true},
    });

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: SavedTurnView(turn: turn, showTokenUsageLedger: false),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(turn.answers['claude']!.usage['input_tokens'], 12);
    expect(find.text('回答間の語彙比較'), findsOneWidget);
    expect(find.text('トークン利用量台帳'), findsNothing);
  });

  testWidgets('保存されたrunningターンから再接続または停止を選べる', (tester) async {
    var reconnects = 0;
    var cancellations = 0;
    final turn = TurnRecord.fromJson({
      'request_id': 'running-request',
      'message': '継続中',
      'status': 'running',
      'usage_may_be_incomplete': true,
      'options': {
        'tier': 'high',
        'providers': ['chatgpt'],
      },
      'answers': <String, Object>{},
      'synthesis': {'ok': false, 'pending': true},
    });

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: SavedTurnView(
              turn: turn,
              onReconnect: () => reconnects++,
              onCancel: () => cancellations++,
            ),
          ),
        ),
      ),
    );

    expect(find.textContaining('実行中、または接続が切れて'), findsOneWidget);
    await tester.tap(find.text('実行へ再接続'));
    await tester.tap(find.text('停止を要求'));
    expect(reconnects, 1);
    expect(cancellations, 1);
  });

  testWidgets('完了ターンは再生成操作とimmutable attempt履歴を表示する', (tester) async {
    var answerRegenerated = '';
    var synthesisRegenerated = 0;
    final turn = TurnRecord.fromJson({
      'request_id': 'completed-request',
      'message': '再生成して',
      'status': 'completed',
      'options': {
        'tier': 'balanced',
        'providers': ['claude'],
      },
      'answers': {
        'claude': {'source': 'claude', 'ok': true, 'text': '新しい回答'},
      },
      'synthesis': {'ok': true, 'text': '古い統合', 'source': 'claude'},
      'synthesis_stale': true,
      'attempts': [
        {
          'attempt_id': 'original-answer',
          'target': 'answer',
          'provider': 'claude',
          'status': 'completed',
          'original': true,
          'result': {
            'source': 'claude',
            'ok': true,
            'text': '保存された初回本文',
            'model': 'claude-original-model',
          },
        },
        {
          'attempt_id': 'new-answer',
          'target': 'answer',
          'provider': 'claude',
          'status': 'completed',
          'original': false,
          'result': {
            'source': 'claude',
            'ok': true,
            'text': '再生成した回答本文',
            'model': 'claude-regenerated-model',
          },
        },
      ],
    });

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: SavedTurnView(
              turn: turn,
              onRegenerateAnswer: (provider) => answerRegenerated = provider,
              onRegenerateSynthesis: () => synthesisRegenerated++,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('再生成履歴 1件'), findsOneWidget);
    expect(find.text('統合回答は更新前です'), findsOneWidget);
    await tester.tap(find.byTooltip('Claudeの回答を再生成'));
    await tester.tap(find.byTooltip('統合回答を再生成'));
    expect(answerRegenerated, 'claude');
    expect(synthesisRegenerated, 1);
  });

  testWidgets('狭いAndroid幅で回答ヘッダーと二段の履歴アコーディオンを安全に表示する', (tester) async {
    tester.view.physicalSize = const Size(320, 1000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    var regenerated = 0;
    final turn = TurnRecord.fromJson({
      'request_id': 'dense-mobile-header',
      'message': '狭い画面でも比較したい',
      'status': 'completed',
      'options': {
        'tier': 'low',
        'reasoning_mode': 'high',
        'providers': ['claude'],
      },
      'answers': {
        'claude': {
          'source': 'claude',
          'ok': true,
          'text': '批評後の最終回答本文',
          'model':
              'claude-a-deliberately-extremely-long-real-model-name-for-overflow-testing',
          'elapsed_sec': 25.4,
          'reasoning': {'requested': 'high', 'effective': 'medium'},
          'round': 2,
          'round1_text': '最初の回答本文',
          'round1_model': 'claude-first-round-model',
          'round1_elapsed_sec': 12.3,
        },
      },
      'attempts': [
        {
          'attempt_id': 'original-answer',
          'target': 'answer',
          'provider': 'claude',
          'status': 'completed',
          'original': true,
          'result': {
            'source': 'claude',
            'ok': true,
            'text': '保存された初回本文',
            'model': 'claude-original-model',
          },
        },
        {
          'attempt_id': 'regenerated-answer',
          'target': 'answer',
          'provider': 'claude',
          'status': 'completed',
          'original': false,
          'result': {
            'source': 'claude',
            'ok': true,
            'text': '再生成した回答本文',
            'model': 'claude-regenerated-model',
          },
        },
      ],
    });

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            padding: const EdgeInsets.all(4),
            child: SavedTurnView(
              turn: turn,
              onRegenerateAnswer: (_) => regenerated++,
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(tester.takeException(), isNull);
    expect(find.textContaining('effort MEDIUM'), findsOneWidget);
    expect(find.textContaining('批評前あり'), findsOneWidget);
    expect(find.byTooltip('完了'), findsOneWidget);
    expect(find.byTooltip('Claudeの回答をコピー'), findsOneWidget);
    expect(find.byTooltip('Claudeの回答を再生成'), findsOneWidget);
    expect(find.text('批評後の最終回答本文'), findsNothing);

    await tester.tap(find.text('Claude'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('批評後の最終回答本文'), findsOneWidget);
    expect(find.text('最初の回答（批評前）'), findsOneWidget);
    expect(find.text('保存された回答履歴 2件'), findsOneWidget);

    await tester.ensureVisible(find.text('最初の回答（批評前）'));
    await tester.tap(find.text('最初の回答（批評前）'));
    await tester.pumpAndSettle();
    expect(find.text('最初の回答本文'), findsOneWidget);
    expect(find.byTooltip('最初の回答をコピー'), findsOneWidget);

    await tester.ensureVisible(find.text('保存された回答履歴 2件'));
    await tester.tap(find.text('保存された回答履歴 2件'));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('最初の保存回答'), findsOneWidget);
    expect(find.text('Claude · 完了'), findsOneWidget);
    expect(find.text('再生成した回答本文'), findsNothing);
    final regeneratedAttempt = find.byKey(
      const ValueKey('attempt-regenerated-answer'),
    );
    await tester.ensureVisible(regeneratedAttempt);
    await tester.tap(regeneratedAttempt);
    await tester.pumpAndSettle();
    expect(find.text('再生成した回答本文'), findsOneWidget);
    expect(find.byTooltip('Claude · 完了をコピー'), findsOneWidget);

    await tester.tap(find.byTooltip('Claudeの回答を再生成'));
    expect(regenerated, 1);
  });
}
