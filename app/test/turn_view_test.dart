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
    expect(find.textContaining('正しさ'), findsWidgets);
    expect(find.text('統合結果'), findsOneWidget);
    expect(find.text('Gemini'), findsOneWidget);
    expect(find.text('PARTIAL · 出力上限'), findsOneWidget);
    expect(find.text('HTTP 2回'), findsOneWidget);
    expect(find.text('利用量は不完全な可能性'), findsOneWidget);
    expect(find.textContaining('完全な回答として扱わないでください'), findsOneWidget);
    expect(find.textContaining('Provider側の処理・利用量'), findsOneWidget);
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
        },
        {
          'attempt_id': 'new-answer',
          'target': 'answer',
          'provider': 'claude',
          'status': 'completed',
          'original': false,
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
}
