import 'package:clage_cook/widgets/insights_panel.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('語彙比較・注意表現・実測トークンを透明に表示する', (tester) async {
    await _pumpPanel(
      tester,
      insights: _fullInsights,
      usageEntries: const [
        {
          'source': 'claude',
          'model': 'claude-test',
          'phase': 'answer',
          'usage': {
            'input_tokens': 1234,
            'output_tokens': 456,
            'total_tokens': 1690,
            'cache_read_input_tokens': 200,
            'output_tokens_details': {'reasoning_tokens': 32},
            'tool_tokens': 0,
          },
        },
      ],
    );

    expect(find.text('回答間の語彙比較'), findsOneWidget);
    expect(find.text('語彙的一致'), findsOneWidget);
    expect(find.text('42%'), findsNWidgets(4));
    expect(find.text('claude ↔ grok'), findsOneWidget);
    expect(find.text('暗号化'), findsOneWidget);
    expect(find.text('監査ログ'), findsOneWidget);
    expect(find.textContaining('正しさ・品質・AIの確信度'), findsOneWidget);
    expect(find.text('claude · 強い断定表現 · 1件'), findsOneWidget);
    expect(find.textContaining('内容の正否は判定'), findsOneWidget);
    expect(find.text('トークン利用量台帳'), findsOneWidget);
    expect(find.text('1,234'), findsNothing);
    expect(find.textContaining('実測値 1件'), findsOneWidget);

    await _expandUsageLedger(tester);

    expect(find.text('1,234'), findsOneWidget);
    expect(find.text('1,690'), findsOneWidget);
    expect(find.text('200'), findsOneWidget);
    expect(find.text('32'), findsOneWidget);
    expect(find.text('0'), findsOneWidget);
    expect(find.textContaining('推計や課金額ではありません'), findsOneWidget);

    expect(
      find.bySemanticsLabel('回答間の語彙的一致 42%。正しさや確信度の評価ではありません'),
      findsOneWidget,
    );
    expect(
      find.byWidgetPredicate(
        (widget) =>
            widget is Semantics &&
            widget.properties.label == 'claudeのProvider実測トークン',
      ),
      findsOneWidget,
    );

    await tester.tap(find.text('トークン利用量台帳'));
    await tester.pumpAndSettle();
    expect(find.text('1,234'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('狭い画面でも長い値と全指標がoverflowしない', (tester) async {
    tester.view.physicalSize = const Size(320, 900);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    await _pumpPanel(
      tester,
      insights: {
        ..._fullInsights,
        'distinctive_terms': {
          'very-long-provider-name-that-must-not-overflow': [
            '非常に長い固有語であっても画面外にはみ出さないこと',
          ],
        },
      },
      usageEntries: const [
        {
          'provider': 'very-long-provider-name-that-must-not-overflow',
          'model': 'a-model-name-that-is-also-deliberately-extremely-long',
          'usage': {'input_tokens': 1},
        },
      ],
    );

    expect(find.text('回答間の語彙比較'), findsOneWidget);
    expect(find.text('トークン利用量台帳'), findsOneWidget);
    await _expandUsageLedger(tester);
    expect(find.text('Input'), findsOneWidget);
    expect(find.text('Tool'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('未知値を推計せず欠損記号として安全に表示する', (tester) async {
    await _pumpPanel(
      tester,
      insights: const {
        'is_comparable': true,
        'answer_count': 'unknown',
        'comparison_count': -1,
        'agreement_score': 4,
        'provider_similarities': {'broken': 'high'},
        'pairwise_similarities': [
          {'sources': 'not-a-list', 'similarity': double.nan},
          'unexpected',
        ],
        'distinctive_terms': {'broken': 123},
        'caution_signals': {'broken': 'not-a-list'},
      },
      usageEntries: const [
        {
          'source': 'unknown-values',
          'usage': {
            'input_tokens': -1,
            'output_tokens': 1.5,
            'total_tokens': '100',
            'cached_tokens': double.infinity,
          },
        },
      ],
    );

    expect(find.text('比較不能'), findsOneWidget);
    await _expandUsageLedger(tester);
    expect(find.text('このProviderからトークン値は返されていません。'), findsOneWidget);
    expect(find.text('—'), findsAtLeastNWidgets(6));
    expect(find.textContaining('推計や課金額ではありません'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Provider別名とネストしたtoken詳細を読める', (tester) async {
    await _pumpPanel(
      tester,
      usageEntries: const [
        {
          'provider': 'chatgpt',
          'role': 'synthesis',
          'usage': {
            'prompt_tokens': 10,
            'completion_tokens': 20,
            'total_tokens': 30,
            'input_tokens_details': {'cached_tokens': 4},
            'completion_tokens_details': {'reasoning_tokens': 6},
          },
        },
      ],
    );

    await _expandUsageLedger(tester);

    expect(find.text('chatgpt'), findsOneWidget);
    expect(find.text('synthesis'), findsOneWidget);
    for (final value in ['10', '20', '30', '4', '6']) {
      expect(find.text(value), findsOneWidget);
    }
    expect(find.text('—'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('比較対象が足りないことを明示する', (tester) async {
    await _pumpPanel(
      tester,
      insights: const {
        'is_comparable': false,
        'answer_count': 1,
        'comparison_count': 0,
        'agreement_score': 0,
        'ignored_sources': ['empty-provider'],
      },
    );

    expect(find.text('比較不能'), findsOneWidget);
    expect(find.text('比較できる回答が2件以上必要です。'), findsOneWidget);
    expect(find.textContaining('空回答のため比較対象外'), findsOneWidget);
    expect(
      find.bySemanticsLabel('回答間の語彙的一致は比較できる回答が2件未満のため算出不能'),
      findsOneWidget,
    );
  });

  testWidgets('入力がなければ表示領域を作らない', (tester) async {
    await _pumpPanel(tester);

    expect(find.byType(Card), findsNothing);
    expect(find.text('回答間の語彙比較'), findsNothing);
    expect(find.text('トークン利用量台帳'), findsNothing);
  });

  testWidgets('設定で台帳だけを非表示にしても回答比較は残す', (tester) async {
    await _pumpPanel(
      tester,
      insights: _fullInsights,
      showUsageLedger: false,
      usageEntries: const [
        {
          'source': 'claude',
          'usage': {'input_tokens': 1234},
        },
      ],
    );

    expect(find.text('回答間の語彙比較'), findsOneWidget);
    expect(find.text('トークン利用量台帳'), findsNothing);
    expect(find.text('1,234'), findsNothing);
    expect(find.bySemanticsLabel('回答比較インサイト'), findsOneWidget);
  });
}

const _fullInsights = <String, dynamic>{
  'is_comparable': true,
  'answer_count': 2,
  'comparison_count': 1,
  'agreement_score': 0.42,
  'provider_similarities': {'claude': 0.42, 'grok': 0.42},
  'pairwise_similarities': [
    {
      'sources': ['claude', 'grok'],
      'similarity': 0.42,
    },
  ],
  'shared_terms': ['暗号化', '安全性'],
  'distinctive_terms': {
    'claude': ['監査ログ'],
    'grok': ['権限制御'],
  },
  'caution_signals': {
    'claude': [
      {
        'type': 'absolute_language',
        'count': 1,
        'matches': ['必ず'],
        'description': '登録語を検出しました。内容の正否は判定していません。',
      },
    ],
  },
  'ignored_sources': <String>[],
  'method': {'limitations': '意味的一致、事実の正しさ、品質、信頼度、モデルの確信度を表さない。'},
};

Future<void> _pumpPanel(
  WidgetTester tester, {
  Map<String, dynamic>? insights,
  List<Map<String, dynamic>> usageEntries = const [],
  bool showUsageLedger = true,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: InsightsPanel(
              insights: insights,
              usageEntries: usageEntries,
              showUsageLedger: showUsageLedger,
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pump();
}

Future<void> _expandUsageLedger(WidgetTester tester) async {
  final title = find.text('トークン利用量台帳');
  await tester.ensureVisible(title);
  await tester.pumpAndSettle();
  await tester.tap(title);
  await tester.pumpAndSettle();
}
