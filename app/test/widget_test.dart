// アプリが起動して会議画面が表示されることの最小スモークテスト。
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:clage_cook/main.dart';

void main() {
  testWidgets('起動して会議画面(タイトルと入力欄)が出る', (tester) async {
    await tester.pumpWidget(const ClageCookApp());

    // AppBarタイトル
    expect(find.text('Clage Cook'), findsOneWidget);
    // 4AIのカード見出し
    expect(find.text('Claude'), findsOneWidget);
    expect(find.text('Grok'), findsOneWidget);
    // 入力欄
    expect(find.byType(TextField), findsOneWidget);
  });
}
